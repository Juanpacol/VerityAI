"""Unit tests for hardened Ollama client (retry, backoff, timeout)."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from verityai.neural.ollama_client import (
    OllamaClient,
    OllamaEmbeddingError,
    OllamaGenerationError,
)


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

    def test_health_check_includes_embed_model(self):
        """Test health check reports the embed model."""
        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient(model="llama3.2", embed_model="nomic-embed-text")

            with patch.object(OllamaClient, "is_available", return_value=False):
                health = client.health_check()

            assert health["embed_model"] == "nomic-embed-text"
            client.close()

    def test_embed_model_defaults_to_generation_model(self):
        """Test embed_model falls back to the generation model when unset."""
        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient(model="llama3.2")

            assert client.embed_model == "llama3.2"
            client.close()


class TestOllamaClientEmbed:
    """Tests for the embed() method (modern /api/embed endpoint)."""

    def test_embed_success(self):
        """Test successful embedding request returns the vector."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"embeddings": [[0.1, 0.2]]}

        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient()
            with patch("requests.post", return_value=mock_response) as mock_post:
                vector = client.embed("some text")

            assert vector == [0.1, 0.2]
            call_kwargs = mock_post.call_args
            assert call_kwargs[0][0] == "http://localhost:11434/api/embed"
            assert call_kwargs[1]["json"] == {"model": "llama3.2", "input": "some text"}
            assert call_kwargs[1]["timeout"] == 15
            client.close()

    def test_embed_uses_model_override(self):
        """Test embed() uses the explicit model override over embed_model."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"embeddings": [[0.5]]}

        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient(embed_model="nomic-embed-text")
            with patch("requests.post", return_value=mock_response) as mock_post:
                client.embed("text", model="other-model")

            assert mock_post.call_args[1]["json"]["model"] == "other-model"
            client.close()

    def test_embed_http_error(self):
        """Test embed() raises OllamaEmbeddingError on non-200 response."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "model not found"

        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient()
            with (
                patch("requests.post", return_value=mock_response),
                pytest.raises(OllamaEmbeddingError, match="HTTP 404"),
            ):
                client.embed("text")
            client.close()

    def test_embed_missing_embeddings_key(self):
        """Test embed() raises OllamaEmbeddingError when response is malformed."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"unexpected": "shape"}

        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient()
            with (
                patch("requests.post", return_value=mock_response),
                pytest.raises(OllamaEmbeddingError, match="missing 'embeddings'"),
            ):
                client.embed("text")
            client.close()

    def test_embed_empty_vector(self):
        """Test embed() raises OllamaEmbeddingError on an empty embedding vector."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"embeddings": [[]]}

        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient()
            with (
                patch("requests.post", return_value=mock_response),
                pytest.raises(OllamaEmbeddingError, match="empty vector"),
            ):
                client.embed("text")
            client.close()

    def test_embed_connection_error(self):
        """Test embed() wraps connection errors in OllamaEmbeddingError."""
        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient()
            with (
                patch("requests.post", side_effect=requests.ConnectionError("refused")),
                pytest.raises(OllamaEmbeddingError, match="Request to Ollama embed"),
            ):
                client.embed("text")
            client.close()

    def test_embed_timeout_propagates_as_embedding_error(self):
        """Test embed() wraps a timeout in OllamaEmbeddingError, not raw Timeout."""
        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient()
            with (
                patch("requests.post", side_effect=requests.Timeout("timed out")),
                pytest.raises(OllamaEmbeddingError),
            ):
                client.embed("text")
            client.close()

    def test_embed_no_retry_on_failure(self):
        """Test embed() does not retry — a single failed call raises immediately."""
        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient()
            with (
                patch(
                    "requests.post", side_effect=requests.ConnectionError("refused")
                ) as mock_post,
                pytest.raises(OllamaEmbeddingError),
            ):
                client.embed("text")
            assert mock_post.call_count == 1
            client.close()

    def test_embed_malformed_json_body(self):
        """Test embed() raises OllamaEmbeddingError when the response body isn't JSON."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("not json")

        with patch("verityai.neural.ollama_client.Ollama"):
            client = OllamaClient()
            with (
                patch("requests.post", return_value=mock_response),
                pytest.raises(OllamaEmbeddingError, match="not valid JSON"),
            ):
                client.embed("text")
            client.close()
