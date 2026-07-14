"""Splitting an LLM's raw text response into (code, reasoning).

Extracted from Orchestrator so evaluation/baselines.py can parse LLM output
the same way the orchestrator does without needing an Orchestrator
instance (baseline 1 in particular does no verification at all).
"""


def split_code_and_reasoning(raw_response: str) -> tuple[str, str]:
    """Split an LLM response into (code, reasoning) using its first fenced code block.

    Args:
        raw_response: Full LLM response, expected to contain reasoning
            text plus a ```python ... ``` or ``` ... ``` fenced block

    Returns:
        (code, reasoning) — if no fenced block is found, the entire
        response is treated as code with empty reasoning
    """
    for fence in ("```python", "```"):
        if fence in raw_response:
            before, _, rest = raw_response.partition(fence)
            code_block, has_close, after = rest.partition("```")
            if has_close:
                return code_block.strip(), (before + after).strip()

    return raw_response.strip(), ""
