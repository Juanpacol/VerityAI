"""Unit tests for multi-turn conversational sessions (agent/session.py).

Uses a fake LLM client so these run fully offline; the point under test is
the session/incremental-verification wiring, not LLM behavior.
"""

import pytest

from tests.fakes import FakeLLMClient, wrap_code
from verityai.agent.orchestrator import Orchestrator
from verityai.agent.session import ConversationSession
from verityai.ontology.models import GenerationRequest

INITIAL_CODE = """def foo():
    x = 1
    assert x == 1
    return x

def bar():
    y = 2
    assert y == 2
    return y
"""

REFINED_CODE_FOO_CHANGED = """def foo():
    x = 1
    z = x
    assert z == 1
    return z

def bar():
    y = 2
    assert y == 2
    return y
"""


class TestSessionStart:
    def test_start_runs_full_orchestrator_loop(self):
        llm = FakeLLMClient([wrap_code(INITIAL_CODE)])
        session = ConversationSession(orchestrator=Orchestrator(llm_client=llm))

        response = session.start(GenerationRequest(prompt="define foo and bar"))

        assert response.status == "success"
        assert len(session.turns) == 1
        assert session.current_code == response.code


class TestSessionRefine:
    def test_refine_before_start_raises(self):
        session = ConversationSession(orchestrator=Orchestrator(llm_client=FakeLLMClient([])))
        with pytest.raises(ValueError):
            session.refine("make it thread-safe")

    def test_refine_only_reverifies_the_changed_function(self):
        llm = FakeLLMClient([wrap_code(INITIAL_CODE), wrap_code(REFINED_CODE_FOO_CHANGED)])
        session = ConversationSession(orchestrator=Orchestrator(llm_client=llm))

        session.start(GenerationRequest(prompt="define foo and bar"))
        response = session.refine("make foo thread-safe")

        assert response.status == "success"
        assert len(session.turns) == 2
        # bar's source is byte-for-byte identical across turns -> only foo
        # should have been sent through the verifier again.
        assert session._incremental_verifier.last_reverified == ["foo"]

    def test_refine_second_call_with_no_further_changes_reverifies_nothing(self):
        llm = FakeLLMClient(
            [
                wrap_code(INITIAL_CODE),
                wrap_code(REFINED_CODE_FOO_CHANGED),
                wrap_code(REFINED_CODE_FOO_CHANGED),
            ]
        )
        session = ConversationSession(orchestrator=Orchestrator(llm_client=llm))

        session.start(GenerationRequest(prompt="define foo and bar"))
        session.refine("make foo thread-safe")
        session.refine("no-op refinement")

        assert session._incremental_verifier.last_reverified == []

    def test_refine_uses_single_shot_generation_not_retry_loop(self):
        """Refinement turns should call the LLM at most once, not burn a retry budget."""
        llm = FakeLLMClient([wrap_code(INITIAL_CODE), wrap_code(REFINED_CODE_FOO_CHANGED)])
        session = ConversationSession(orchestrator=Orchestrator(llm_client=llm))

        session.start(GenerationRequest(prompt="define foo and bar"))
        assert llm.call_count == 1

        session.refine("make foo thread-safe")
        assert llm.call_count == 2

    def test_refine_prompt_includes_current_code_as_context(self):
        llm = FakeLLMClient([wrap_code(INITIAL_CODE), wrap_code(REFINED_CODE_FOO_CHANGED)])
        session = ConversationSession(orchestrator=Orchestrator(llm_client=llm))

        session.start(GenerationRequest(prompt="define foo and bar"))
        session.refine("make foo thread-safe")

        assert "def foo():" in llm.prompts_seen[1]
        assert "make foo thread-safe" in llm.prompts_seen[1]
