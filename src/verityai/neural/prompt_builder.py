"""Prompt building with dynamic context injection from Knowledge Graph."""

from typing import Any, Optional


class PromptBuilder:
    """Build prompts with dynamic context from KG."""

    def __init__(self):
        self.base_system_prompt = """You are an expert code generator that reasons step-by-step about correctness.

When generating code:
1. Think through the requirements carefully
2. Show your step-by-step reasoning
3. Write clean, verified code
4. Consider edge cases and potential bugs

Always explain your thinking before the code."""

    def build_generation_prompt(
        self,
        user_request: str,
        kg_context: Optional[dict[str, Any]] = None,
        previous_failure: Optional[str] = None,
    ) -> str:
        """Build a code generation prompt with KG context.

        Args:
            user_request: What the user asked for
            kg_context: Rules + patterns from Knowledge Graph
            previous_failure: Reason for previous attempt's failure (for retry)

        Returns:
            Full prompt to send to LLM
        """
        prompt = self.base_system_prompt + "\n\n"

        if kg_context:
            if "rules" in kg_context and kg_context["rules"]:
                prompt += "## Applicable Security Rules\n"
                for rule in kg_context["rules"]:
                    prompt += f"- {rule['name']}: {rule['description']}\n"
                prompt += "\n"

            if "patterns" in kg_context and kg_context["patterns"]:
                prompt += "## Similar Verified Patterns\n"
                for pattern in kg_context["patterns"]:
                    prompt += f"- {pattern['name']}: {pattern['description']}\n"
                prompt += "\n"

        if previous_failure:
            prompt += f"## Previous Attempt Failed\n"
            prompt += f"Reason: {previous_failure}\n"
            prompt += f"Please fix this and try again.\n\n"

        prompt += f"## Request\n{user_request}\n"

        return prompt
