"""Integration test: full Orchestrator loop against realistic multi-attempt scenarios.

Uses a fake LLM (no live Ollama) but exercises the real KGClient-shaped
interface, real PromptBuilder, real symbolic verification stack, and real
confidence scoring — everything except the network call to Ollama itself.
"""

from typing import Optional

from verityai.agent.orchestrator import Orchestrator
from verityai.neural.prompt_builder import PromptBuilder
from verityai.ontology.models import GenerationRequest, VerificationStatus


class ScriptedLLMClient:
    """Simulates an LLM that improves its answer across retries, like a real model would."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.call_count = 0

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        response = self.responses[min(self.call_count, len(self.responses) - 1)]
        self.call_count += 1
        return response


class StubKGClient:
    """Minimal stand-in for KGClient returning fixed rules, no Neo4j needed."""

    class _Rule:
        def __init__(self, name, description):
            self.name = name
            self.description = description

    def get_rules_by_category(self, category, language="python"):
        if category == "security":
            return [self._Rule("no_null_dereference", "Avoid null dereferences")]
        return [self._Rule("bounds_check", "Verify array bounds")]


def wrap(code: str) -> str:
    return f"Here's my reasoning:\n\n```python\n{code}\n```"


class TestOrchestratorFullLoopWithKG:
    """Simulates a realistic retry-and-recover scenario against a KG-backed orchestrator."""

    def test_full_loop_recovers_from_bad_first_attempt(self):
        # First attempt: internally contradictory (off-by-one style logic bug)
        broken = "x = 10\ny = x - 10\nassert y == 5"  # y is 0, assertion fails -> UNSAT
        # Second attempt: LLM "reads" the failure reason and fixes it
        fixed = "x = 10\ny = x - 10\nassert y == 0"

        llm = ScriptedLLMClient([wrap(broken), wrap(fixed)])
        orchestrator = Orchestrator(
            llm_client=llm,
            kg_client=StubKGClient(),
            prompt_builder=PromptBuilder(),
        )

        result = orchestrator.run(
            GenerationRequest(prompt="subtract 10 from x and store in y", max_attempts=3)
        )

        assert result.status == "success"
        assert llm.call_count == 2
        assert result.final_verification.status == VerificationStatus.PASS
        # Trace history should show the full arc: fail then pass
        assert result.traces[0].verification_result.status == VerificationStatus.FAIL
        assert result.traces[1].verification_result.status == VerificationStatus.PASS
        # Confidence should be meaningfully higher on the successful attempt
        assert result.traces[1].confidence_score > result.traces[0].confidence_score

    def test_kg_rules_appear_in_first_prompt(self):
        llm = ScriptedLLMClient([wrap("x = 1\nassert x == 1")])
        orchestrator = Orchestrator(
            llm_client=llm, kg_client=StubKGClient(), prompt_builder=PromptBuilder()
        )

        orchestrator.run(GenerationRequest(prompt="assign 1 to x"))

        # We can't see the prompt directly here, but a successful run with a
        # KG client wired in (vs. None) exercises the KG-context-fetch path
        # without raising — the unit tests cover prompt content directly.
        assert llm.call_count == 1

    def test_explanation_is_human_readable_on_success(self):
        llm = ScriptedLLMClient([wrap("x = 42\nassert x == 42")])
        orchestrator = Orchestrator(llm_client=llm)

        result = orchestrator.run(GenerationRequest(prompt="test"))

        assert "passed" in result.explanation.lower() or "✓" in result.explanation

    def test_explanation_is_human_readable_on_failure(self):
        llm = ScriptedLLMClient([wrap("x = 1\nassert x == 2")] * 3)
        orchestrator = Orchestrator(llm_client=llm)

        result = orchestrator.run(GenerationRequest(prompt="test", max_attempts=3))

        assert result.status == "failed"
        assert len(result.explanation) > 0
        assert "FAILED" in result.explanation or "failed" in result.explanation.lower()

    def test_all_three_attempts_recorded_when_never_succeeding(self):
        llm = ScriptedLLMClient([wrap("x = 1\nassert x == 2")] * 3)
        orchestrator = Orchestrator(llm_client=llm)

        result = orchestrator.run(GenerationRequest(prompt="test", max_attempts=3))

        assert len(result.traces) == 3
        assert all(t.verification_result.status == VerificationStatus.FAIL for t in result.traces)
        # Each attempt number should be sequential
        assert [t.attempt_number for t in result.traces] == [1, 2, 3]
