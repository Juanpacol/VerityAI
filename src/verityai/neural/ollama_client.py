"""Wrapper around Ollama API for LLM inference with retry/timeout hardening."""

import contextlib
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Optional

import requests
from langchain_community.llms import Ollama

logger = logging.getLogger(__name__)


class OllamaGenerationError(Exception):
    """Raised when Ollama generation fails after all retry attempts."""

    def __init__(self, message: str, attempts: int, last_error: Optional[Exception] = None):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class OllamaEmbeddingError(Exception):
    """Raised when an embedding request fails or returns an unusable response."""


class OllamaClient:
    """Wrapper for Ollama LLM with exponential backoff retry and enforced timeout.

    Timeout is enforced via a worker thread regardless of whether the
    underlying LangChain/Ollama HTTP client honors its own timeout
    parameter, since that behavior varies across versions.
    """

    RETRYABLE_EXCEPTIONS = (
        requests.ConnectionError,
        requests.Timeout,
        ConnectionError,
        ConnectionRefusedError,
        FutureTimeoutError,
    )

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        backoff_max: float = 30.0,
        embed_model: Optional[str] = None,
    ):
        """Initialize Ollama client.

        Args:
            model: Model name (e.g., "llama3.2", "llama2:13b")
            base_url: Ollama server URL
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Max tokens to generate
            timeout: Timeout in seconds per generation attempt
            max_retries: Max number of attempts (including the first)
            backoff_base: Base delay (seconds) for exponential backoff
            backoff_max: Cap on backoff delay (seconds)
            embed_model: Model used for embed(); defaults to `model` if unset
        """
        self.model_name = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.embed_model = embed_model or model

        self.llm = Ollama(
            model=model,
            base_url=base_url,
            temperature=temperature,
            num_predict=max_tokens,
        )
        # Single-worker executor used purely to enforce a hard wall-clock
        # timeout around llm.invoke(), which may otherwise block forever
        # on a hung connection.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ollama-invoke")

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Generate text using Ollama, retrying transient failures with backoff.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt prepended to the request

        Returns:
            Generated text

        Raises:
            OllamaGenerationError: If all retry attempts fail
        """
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\nUser: {prompt}"

        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return self._invoke_with_timeout(full_prompt)
            except self.RETRYABLE_EXCEPTIONS as e:
                last_error = e
                logger.warning(
                    f"Ollama attempt {attempt}/{self.max_retries} failed "
                    f"(retryable: {type(e).__name__}): {e}"
                )
            except Exception as e:
                # Non-retryable (e.g. model not found, malformed request) —
                # fail fast instead of burning through retry budget.
                logger.error(f"Ollama generation failed with non-retryable error: {e}")
                raise OllamaGenerationError(
                    f"Non-retryable error generating text: {e}", attempts=attempt, last_error=e
                ) from e

            if attempt < self.max_retries:
                delay = self._compute_backoff(attempt)
                logger.info(f"Retrying in {delay:.1f}s...")
                time.sleep(delay)

        raise OllamaGenerationError(
            f"Failed to generate text after {self.max_retries} attempts. Last error: {last_error}",
            attempts=self.max_retries,
            last_error=last_error,
        ) from last_error

    def _invoke_with_timeout(self, full_prompt: str) -> str:
        """Invoke the LLM with a hard timeout enforced by a worker thread.

        Args:
            full_prompt: Fully-assembled prompt text

        Returns:
            Generated text

        Raises:
            FutureTimeoutError: If generation exceeds self.timeout
        """
        future = self._executor.submit(self.llm.invoke, full_prompt)
        return str(future.result(timeout=self.timeout))

    def _compute_backoff(self, attempt: int) -> float:
        """Compute exponential backoff delay with jitter.

        Args:
            attempt: Current attempt number (1-indexed)

        Returns:
            Delay in seconds before next retry
        """
        delay = min(self.backoff_base * (2 ** (attempt - 1)), self.backoff_max)
        jitter = random.uniform(0, delay * 0.1)
        return float(delay + jitter)

    def stream_generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
    ):
        """Stream text generation (currently yields once; true streaming is future work)."""
        yield self.generate(prompt, system_prompt)

    @staticmethod
    def is_available(base_url: str = "http://localhost:11434") -> bool:
        """Check if Ollama server is reachable."""
        try:
            response = requests.get(f"{base_url}/api/tags", timeout=2)
            return response.status_code == 200
        except Exception:
            return False

    def embed(self, text: str, model: Optional[str] = None) -> list[float]:
        """Get an embedding vector for text via Ollama's modern embed endpoint.

        Uses `POST /api/embed` (not the legacy, deprecated `/api/embeddings`
        that langchain_community's OllamaEmbeddings targets) — the modern
        endpoint returns `{"embeddings": [[...]]}` even for a single input.
        No retry loop: callers are expected to degrade (e.g. to lexical-only
        retrieval) rather than block on transient embedding failures.

        Args:
            text: Text to embed
            model: Override for the embedding model (defaults to self.embed_model)

        Returns:
            Embedding vector as a list of floats

        Raises:
            OllamaEmbeddingError: On HTTP failure, malformed response, or empty vector
        """
        m = model or self.embed_model
        try:
            response = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": m, "input": text},
                timeout=15,
            )
        except requests.RequestException as e:
            raise OllamaEmbeddingError(f"Request to Ollama embed endpoint failed: {e}") from e

        if response.status_code != 200:
            raise OllamaEmbeddingError(
                f"Ollama embed endpoint returned HTTP {response.status_code}: {response.text}"
            )

        try:
            data: dict[str, Any] = response.json()
        except ValueError as e:
            raise OllamaEmbeddingError(f"Ollama embed response was not valid JSON: {e}") from e

        embeddings = data.get("embeddings")
        if not embeddings or not isinstance(embeddings, list):
            raise OllamaEmbeddingError(f"Ollama embed response missing 'embeddings': {data}")

        vector = embeddings[0]
        if not vector:
            raise OllamaEmbeddingError("Ollama embed response contained an empty vector")

        return list(vector)

    def health_check(self) -> dict:
        """Get client health/config summary.

        Returns:
            Dictionary with availability and configuration info
        """
        return {
            "available": self.is_available(self.base_url),
            "model": self.model_name,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "embed_model": self.embed_model,
        }

    def close(self) -> None:
        """Release the worker thread pool."""
        self._executor.shutdown(wait=False)

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()
