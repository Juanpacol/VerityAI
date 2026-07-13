"""Neural layer — LLM client and prompt building."""

from .ollama_client import OllamaClient
from .prompt_builder import PromptBuilder

__all__ = ["OllamaClient", "PromptBuilder"]
