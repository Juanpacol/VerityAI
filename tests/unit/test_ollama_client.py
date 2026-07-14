"""Unit tests for hardened Ollama client (retry, backoff, timeout)."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from verityai.neural.ollama_client import OllamaClient, OllamaGenerationError


class TestOllamaClientConfig:
    """Tests for client configuration."""

    def test_default_configuration(self):
        """Test default client configuration values."""
        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient()

            assert client.model_name == "llama3.2"
            assert client.timeout == 60.0
            assert client.max_retries == 3
            client.close()

    def test_custom_configuration(self):
        """Test custom client configuration."""
        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient(
                model="llama2:13b",
                timeout=30.0,
                max_retries=5,
                backoff_base=0.5,
                backoff_max=10.0,
            )

            assert client.model_name == "llama2:13b"
            assert client.timeout == 30.0
            assert client.max_retries == 5
            client.close()


class TestOllamaClientBackoff:
    """Tests for exponential backoff computation."""

    def test_backoff_increases_with_attempt(self):
        """Test that backoff delay grows exponentially."""
        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient(backoff_base=1.0, backoff_max=100.0)

            delay1 = client._compute_backoff(1)
            delay2 = client._compute_backoff(2)
            delay3 = client._compute_backoff(3)

            # Each delay should be roughly double the previous (plus jitter)
            assert delay1 < delay2 < delay3
            client.close()

    def test_backoff_capped_at_max(self):
        """Test that backoff never exceeds backoff_max."""
        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient(backoff_base=1.0, backoff_max=5.0)

            delay = client._compute_backoff(10)  # Would be huge without cap

            assert delay <= 5.0 * 1.1  # Allow for jitter
            client.close()


class TestOllamaClientRetry:
    """Tests for retry logic on generation failures."""

    def test_retries_on_connection_error(self):
        """Test that connection errors trigger retry."""
        with patch("verityai.neural.ollama_client.Ollama") as MockOllama:
            mock_llm = MagicMock()
            mock_llm.invoke.side_effect = [
                requests.ConnectionError("Connection refused"),
                "Success on second attempt",
            ]
            MockOllama.return_value = mock_llm

            client = OllamaClient(max_retries=3, backoff_base=0.01, backoff_max=0.05)
            result = client.generate("test prompt")

            assert result == "Success on second attempt"
            assert mock_llm.invoke.call_count == 2
            client.close()

    def test_raises_after_max_retries_exhausted(self):
        """Test that OllamaGenerationError is raised after all retries fail."""
        with patch("verityai.neural.ollama_client.Ollama") as MockOllama:
            mock_llm = MagicMock()
            mock_llm.invoke.side_effect = requests.ConnectionError("Always fails")
            MockOllama.return_value = mock_llm

            client = OllamaClient(max_retries=2, backoff_base=0.01, backoff_max=0.05)

            with pytest.raises(OllamaGenerationError) as exc_info:
                client.generate("test prompt")

            assert exc_info.value.attempts == 2
            assert mock_llm.invoke.call_count == 2
            client.close()

    def test_non_retryable_error_fails_fast(self):
        """Test that non-retryable errors don't consume retry budget."""
        with patch("verityai.neural.ollama_client.Ollama") as MockOllama:
            mock_llm = MagicMock()
            mock_llm.invoke.side_effect = ValueError("Model not found")
            MockOllama.return_value = mock_llm

            client = OllamaClient(max_retries=5, backoff_base=0.01, backoff_max=0.05)

            with pytest.raises(OllamaGenerationError) as exc_info:
                client.generate("test prompt")

            # Should fail on first attempt, not retry 5 times
            assert exc_info.value.attempts == 1
            assert mock_llm.invoke.call_count == 1
            client.close()

    def test_successful_generation_no_retry_needed(self):
        """Test successful generation on first attempt."""
        with patch("verityai.neural.ollama_client.Ollama") as MockOllama:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value = "Generated code here"
            MockOllama.return_value = mock_llm

            client = OllamaClient()
            result = client.generate("test prompt")

            assert result == "Generated code here"
            assert mock_llm.invoke.call_count == 1
            client.close()

    def test_system_prompt_prepended(self):
        """Test that system prompt is prepended to user prompt."""
        with patch("verityai.neural.ollama_client.Ollama") as MockOllama:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value = "response"
            MockOllama.return_value = mock_llm

            client = OllamaClient()
            client.generate("user request", system_prompt="You are an assistant")

            call_args = mock_llm.invoke.call_args[0][0]
            assert "You are an assistant" in call_args
            assert "user request" in call_args
            client.close()


class TestOllamaClientHealthCheck:
    """Tests for health check functionality."""

    def test_health_check_structure(self):
        """Test health check returns expected fields."""
        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient(model="llama3.2", timeout=45.0, max_retries=4)

            with patch.object(OllamaClient, "is_available", return_value=False):
                health = client.health_check()

            assert health["model"] == "llama3.2"
            assert health["timeout"] == 45.0
            assert health["max_retries"] == 4
            assert "available" in health
            client.close()

    def test_is_available_false_on_connection_error(self):
        """Test is_available returns False when server unreachable."""
        with patch("requests.get", side_effect=requests.ConnectionError()):
            assert OllamaClient.is_available() is False

    def test_is_available_true_on_success(self):
        """Test is_available returns True on successful response."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("requests.get", return_value=mock_response):
            assert OllamaClient.is_available() is True
