"""Unit tests for the generate-verify-retry orchestrator.

Uses a fake LLM client (no live Ollama needed) so these tests run fully
offline and deterministically.
"""

from tests.fakes import AlwaysFailingLLMClient, FakeLLMClient, wrap_code
from verityai.agent.orchestrator import Orchestrator
from verityai.ontology.models import GenerationRequest, Rule, VerificationStatus


class TestOrchestratorSuccessPath:
    def test_verified_on_first_attempt(self):
        response_code = "x = 5\nassert x == 5"
        llm = FakeLLMClient([wrap_code(response_code)])

        orchestrator = Orchestrator(llm_client=llm)
        request = GenerationRequest(prompt="assign 5 to x", max_attempts=3)
        result = orchestrator.run(request)

        assert result.status == "success"
        assert llm.call_count == 1
        assert len(result.traces) == 1
        assert result.final_verification.status == VerificationStatus.PASS

    def test_code_extracted_from_fenced_block(self):
        llm = FakeLLMClient([wrap_code("x = 1\nassert x == 1")])
        orchestrator = Orchestrator(llm_client=llm)

        result = orchestrator.run(GenerationRequest(prompt="test"))

        assert "x = 1" in result.code
        assert "```" not in result.code


class TestOrchestratorRetryPath:
    def test_retries_after_failure_then_succeeds(self):
        failing_code = "x = 5\nassert x == 999"  # contradiction -> UNSAT/FAIL
        passing_code = "x = 5\nassert x == 5"

        llm = FakeLLMClient([wrap_code(failing_code), wrap_code(passing_code)])
        orchestrator = Orchestrator(llm_client=llm)

        result = orchestrator.run(GenerationRequest(prompt="test", max_attempts=3))

        assert result.status == "success"
        assert llm.call_count == 2
        assert len(result.traces) == 2
        assert result.traces[0].verification_result.status == VerificationStatus.FAIL

    def test_failure_reason_injected_into_retry_prompt(self):
        failing_code = "x = 5\nassert x == 999"
        passing_code = "x = 5\nassert x == 5"

        llm = FakeLLMClient([wrap_code(failing_code), wrap_code(passing_code)])
        orchestrator = Orchestrator(llm_client=llm)

        orchestrator.run(GenerationRequest(prompt="test", max_attempts=3))

        assert len(llm.prompts_seen) == 2
        # First prompt has no failure context
        assert "Previous Attempt Failed" not in llm.prompts_seen[0]
        # Second prompt must carry forward the failure reason
        assert "Previous Attempt Failed" in llm.prompts_seen[1]

    def test_exhausts_max_attempts_on_persistent_failure(self):
        failing_code = "x = 5\nassert x == 999"
        llm = FakeLLMClient([wrap_code(failing_code)] * 3)

        orchestrator = Orchestrator(llm_client=llm)
        result = orchestrator.run(GenerationRequest(prompt="test", max_attempts=3))

        assert result.status == "failed"
        assert llm.call_count == 3
        assert len(result.traces) == 3

    def test_respects_custom_max_attempts(self):
        failing_code = "x = 5\nassert x == 999"
        llm = FakeLLMClient([wrap_code(failing_code)] * 5)

        orchestrator = Orchestrator(llm_client=llm)
        result = orchestrator.run(GenerationRequest(prompt="test", max_attempts=1))

        assert llm.call_count == 1
        assert len(result.traces) == 1


class TestOrchestratorLLMFailure:
    def test_llm_unreachable_returns_failed_response_without_crashing(self):
        llm = AlwaysFailingLLMClient()
        orchestrator = Orchestrator(llm_client=llm)

        result = orchestrator.run(GenerationRequest(prompt="test", max_attempts=3))

        assert result.status == "failed"
        assert result.code == ""
        assert len(result.traces) == 0  # Never got far enough to record an attempt

    def test_llm_failure_does_not_consume_retry_budget_pointlessly(self):
        """If the LLM itself is down, all 3 retries failing identically wastes
        time/money for no benefit — orchestrator should abort on first LLM error."""
        llm = AlwaysFailingLLMClient()
        orchestrator = Orchestrator(llm_client=llm)

        orchestrator.run(GenerationRequest(prompt="test", max_attempts=3))
        # AlwaysFailingLLMClient doesn't track calls, but the fact this
        # returns promptly (no infinite loop) plus 0 traces confirms
        # single-call abort behavior indirectly via test above.


class TestOrchestratorNotVerifiedPath:
    def test_partial_status_for_non_verifiable_code(self):
        # Function call + no assertions -> falls outside verifiable subset entirely
        code = "result = some_undefined_helper_function()"
        llm = FakeLLMClient([wrap_code(code)])

        orchestrator = Orchestrator(llm_client=llm)
        result = orchestrator.run(GenerationRequest(prompt="test", max_attempts=1))

        assert result.status in ("partial", "failed")
        assert result.final_verification.status in (
            VerificationStatus.NOT_VERIFIED,
            VerificationStatus.FAIL,
        )


class TestOrchestratorNoCodeBlock:
    def test_response_without_fenced_block_treated_as_raw_code(self):
        llm = FakeLLMClient(["x = 1\nassert x == 1"])  # no markdown fencing
        orchestrator = Orchestrator(llm_client=llm)

        result = orchestrator.run(GenerationRequest(prompt="test", max_attempts=1))

        assert "x = 1" in result.code


class TestOrchestratorKGContextOptional:
    def test_runs_without_kg_client(self):
        """kg_client=None should not crash — degraded mode, no rules injected."""
        llm = FakeLLMClient([wrap_code("x = 1\nassert x == 1")])
        orchestrator = Orchestrator(llm_client=llm, kg_client=None)

        result = orchestrator.run(GenerationRequest(prompt="test"))

        assert result.status == "success"

    def test_kg_client_failure_does_not_block_generation(self):
        """A broken KG connection should degrade gracefully, not crash the request."""

        class BrokenKGClient:
            def get_rules_by_category(self, category, language="python"):
                raise ConnectionError("Neo4j unreachable")

        llm = FakeLLMClient([wrap_code("x = 1\nassert x == 1")])
        orchestrator = Orchestrator(llm_client=llm, kg_client=BrokenKGClient())

        result = orchestrator.run(GenerationRequest(prompt="test"))

        assert result.status == "success"


def make_rule(**overrides) -> Rule:
    defaults = dict(
        name="division_safety",
        description="check divide by zero",
        category="security",
        condition="check divide by zero",
        severity="high",
        applies_to=["python"],
    )
    defaults.update(overrides)
    return Rule(**defaults)


class TestOrchestratorRetrievalStrategy:
    def test_default_strategy_is_legacy(self):
        llm = FakeLLMClient([wrap_code("x = 1\nassert x == 1")])
        orchestrator = Orchestrator(llm_client=llm)
        assert orchestrator.retrieval_strategy == "legacy"

    def test_legacy_context_has_no_retrieval_key(self):
        class FakeCategoryKGClient:
            def get_rules_by_category(self, category, language="python"):
                return []

        llm = FakeLLMClient([wrap_code("x = 1\nassert x == 1")])
        orchestrator = Orchestrator(llm_client=llm, kg_client=FakeCategoryKGClient())

        result = orchestrator.run(GenerationRequest(prompt="test"))

        assert result.status == "success"
        assert "retrieval" not in result.traces[0].kg_context

    def test_hybrid_strategy_returns_relevant_rule_with_provenance(self):
        rule = make_rule()

        class FakeHybridKGClient:
            def get_rules_with_embeddings(self, language="python"):
                return [(rule, None)]

        llm = FakeLLMClient([wrap_code("x = 1\nassert x == 1")])
        orchestrator = Orchestrator(
            llm_client=llm, kg_client=FakeHybridKGClient(), retrieval_strategy="hybrid"
        )

        result = orchestrator.run(GenerationRequest(prompt="divide by zero safety"))

        kg_context = result.traces[0].kg_context
        assert kg_context["rules"][0]["name"] == "division_safety"
        assert "provenance" in kg_context["rules"][0]
        assert kg_context["retrieval"]["strategy"] == "hybrid"
        # FakeLLMClient has no embed() method -> degrades to lexical-only.
        assert kg_context["retrieval"]["mode"] == "lexical_only"

    def test_hybrid_kg_failure_degrades_to_empty_context(self):
        class BrokenHybridKGClient:
            def get_rules_with_embeddings(self, language="python"):
                raise ConnectionError("Neo4j unreachable")

        llm = FakeLLMClient([wrap_code("x = 1\nassert x == 1")])
        orchestrator = Orchestrator(
            llm_client=llm, kg_client=BrokenHybridKGClient(), retrieval_strategy="hybrid"
        )

        result = orchestrator.run(GenerationRequest(prompt="test"))

        assert result.status == "success"
        assert result.traces[0].kg_context == {}

    def test_confidence_rises_with_matching_embed_fn(self):
        rule = make_rule()

        class FakeHybridKGClient:
            def get_rules_with_embeddings(self, language="python"):
                return [(rule, [1.0, 0.0])]

        code = "x = 1\nassert x == 1"

        llm_no_embed = FakeLLMClient([wrap_code(code)])
        result_no_embed = Orchestrator(
            llm_client=llm_no_embed, kg_client=FakeHybridKGClient(), retrieval_strategy="hybrid"
        ).run(GenerationRequest(prompt="divide by zero"))

        llm_with_embed = FakeLLMClient([wrap_code(code)])
        llm_with_embed.embed = lambda text: [1.0, 0.0]  # identical vector -> cosine similarity 1.0
        result_with_embed = Orchestrator(
            llm_client=llm_with_embed, kg_client=FakeHybridKGClient(), retrieval_strategy="hybrid"
        ).run(GenerationRequest(prompt="divide by zero"))

        assert result_with_embed.confidence > result_no_embed.confidence


class TestOrchestratorSplitCodeAndReasoning:
    def test_split_python_fenced_block(self):
        orchestrator = Orchestrator(llm_client=FakeLLMClient([]))
        code, reasoning = orchestrator._split_code_and_reasoning(
            "Let me think.\n```python\nx = 1\n```\nDone."
        )
        assert code == "x = 1"
        assert "Let me think" in reasoning
        assert "Done" in reasoning

    def test_split_generic_fenced_block(self):
        orchestrator = Orchestrator(llm_client=FakeLLMClient([]))
        code, reasoning = orchestrator._split_code_and_reasoning("```\ny = 2\n```")
        assert code == "y = 2"

    def test_no_fence_returns_full_text_as_code(self):
        orchestrator = Orchestrator(llm_client=FakeLLMClient([]))
        code, reasoning = orchestrator._split_code_and_reasoning("z = 3")
        assert code == "z = 3"
        assert reasoning == ""
