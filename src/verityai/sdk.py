"""Minimal Python SDK: `from verityai import Verifier`.

A thin, programmatic wrapper over Orchestrator.run()/verify_python_snippet
for callers embedding VerityAI in their own code rather than going through
the REST API or CLI.
"""

from typing import Optional

from verityai.agent.orchestrator import Orchestrator
from verityai.kg.client import KGClient
from verityai.neural.ollama_client import OllamaClient
from verityai.ontology.models import GenerationRequest, GenerationResponse, VerificationResult
from verityai.symbolic.verify import verify_python_snippet


class Verifier:
    """Programmatic entry point: generate verified code, or verify code you already have.

    Example:
        >>> from verityai import Verifier
        >>> v = Verifier(model="llama3.2")
        >>> response = v.generate("write a function that adds two numbers")
        >>> result = v.verify("x = 1\\nassert x == 1")
    """

    def __init__(
        self,
        model: str = "llama3.2",
        ollama_host: str = "http://localhost:11434",
        kg_client: Optional[KGClient] = None,
        z3_timeout_seconds: float = 3.0,
    ):
        """
        Args:
            model: Ollama model name.
            ollama_host: Ollama server URL.
            kg_client: Optional KG client for rule/pattern context injection
                during generation. If None, generation proceeds without KG
                context (degraded but functional).
            z3_timeout_seconds: Per-query timeout for the verification engine.
        """
        llm_client = OllamaClient(model=model, base_url=ollama_host)
        self._orchestrator = Orchestrator(
            llm_client=llm_client, kg_client=kg_client, z3_timeout_seconds=z3_timeout_seconds
        )

    def generate(
        self, prompt: str, language: str = "python", max_attempts: int = 3
    ) -> GenerationResponse:
        """Generate code from a natural-language prompt via the full retry loop."""
        request = GenerationRequest(prompt=prompt, language=language, max_attempts=max_attempts)
        return self._orchestrator.run(request)

    def verify(self, code: str) -> VerificationResult:
        """Verify a standalone code snippet -- no LLM call, no retry loop."""
        return verify_python_snippet(code, timeout_seconds=self._orchestrator.z3_timeout_seconds)
