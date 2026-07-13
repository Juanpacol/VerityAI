"""Wrapper around Ollama API for LLM inference."""

import os
from typing import Optional

from langchain.llms.ollama import Ollama

from verityai.ontology import models


class OllamaClient:
    """Wrapper for Ollama LLM with retry/timeout handling."""

    def __init__(
        self,
        model: str = "llama2:13b",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        timeout: int = 60,
    ):
        """Initialize Ollama client.

        Args:
            model: Model name (e.g., "llama2:13b")
            base_url: Ollama server URL
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Max tokens to generate
            timeout: Timeout in seconds per request
        """
        self.model_name = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

        # Initialize LangChain Ollama LLM
        self.llm = Ollama(
            model=model,
            base_url=base_url,
            temperature=temperature,
            num_predict=max_tokens,
        )

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        retry_count: int = 1,
    ) -> str:
        """Generate text using Ollama.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            retry_count: Number of retries on failure

        Returns:
            Generated text

        Raises:
            RuntimeError: If all retry attempts fail
        """
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\nUser: {prompt}"

        last_error = None
        for attempt in range(retry_count):
            try:
                response = self.llm.invoke(full_prompt)
                return response
            except Exception as e:
                last_error = e
                if attempt < retry_count - 1:
                    continue
                else:
                    break

        raise RuntimeError(
            f"Failed to generate text after {retry_count} attempts. "
            f"Last error: {last_error}"
        )

    def stream_generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
    ):
        """Stream text generation (placeholder for now)."""
        # TODO: Implement streaming in future
        yield self.generate(prompt, system_prompt)

    @staticmethod
    def is_available(base_url: str = "http://localhost:11434") -> bool:
        """Check if Ollama server is available."""
        import requests

        try:
            response = requests.get(f"{base_url}/api/tags", timeout=2)
            return response.status_code == 200
        except Exception:
            return False
