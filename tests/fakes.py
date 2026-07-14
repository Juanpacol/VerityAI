"""Shared test doubles, previously copy-pasted (with drift) across 8 test files.

`FakeLLMClient` here always tracks `prompts_seen` -- a strict superset of
the variant some files used that didn't, so unifying on this one changes
no existing test's observable behavior.
"""

from typing import Optional

from verityai.neural.ollama_client import OllamaGenerationError


class FakeLLMClient:
    """Stand-in for OllamaClient that returns scripted responses in sequence."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.call_count = 0
        self.prompts_seen: list[str] = []

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        self.prompts_seen.append(prompt)
        if self.call_count >= len(self.responses):
            raise OllamaGenerationError("No more scripted responses", attempts=1)
        response = self.responses[self.call_count]
        self.call_count += 1
        return response


class AlwaysFailingLLMClient:
    """Stand-in for a completely unreachable Ollama server."""

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        raise OllamaGenerationError("Connection refused", attempts=3)


def wrap_code(code: str) -> str:
    """Wrap a code string as an LLM response would fence it.

    Orchestrator._split_code_and_reasoning extracts the fenced block
    regardless of surrounding prose, so the exact wrapper text is
    interchangeable -- previously 3 slightly different variants existed
    across test files with no behavioral difference.
    """
    return f"Here is the code:\n\n```python\n{code}\n```"
