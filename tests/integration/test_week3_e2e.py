"""Phase 2 Week 3 acceptance test: scripted 3-turn refinement conversation.

Plan's hardened "done" criterion for the refinement work: a scripted 3+
turn conversation converging on verified code, with an EXPLICIT assertion
that re-verification on later turns was incremental (only the changed
function was sent through Z3 again) rather than a claim of "implemented".
"""

from typing import Optional

from verityai.agent.orchestrator import Orchestrator
from verityai.agent.session import ConversationSession
from verityai.neural.ollama_client import OllamaGenerationError
from verityai.ontology.models import GenerationRequest, VerificationStatus


class FakeLLMClient:
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


def wrap_code(code: str) -> str:
    return f"Here is the code:\n\n```python\n{code}\n```"


TURN1_CODE = """def compute_total():
    x = 1
    assert x == 1
    return x

def compute_average():
    y = 2
    assert y == 2
    return y
"""

# Turn 2: only compute_total changes (the requested refinement).
TURN2_CODE = """def compute_total():
    x = 1
    total = x
    assert total == 1
    return total

def compute_average():
    y = 2
    assert y == 2
    return y
"""


class TestThreeTurnRefinementConversation:
    def test_conversation_converges_with_incremental_reverification(self):
        llm = FakeLLMClient([wrap_code(TURN1_CODE), wrap_code(TURN2_CODE)])
        session = ConversationSession(orchestrator=Orchestrator(llm_client=llm))

        # Turn 1: initial request, full generate-verify-retry loop.
        turn1 = session.start(GenerationRequest(prompt="write compute_total and compute_average"))
        assert turn1.status == "success"
        assert turn1.final_verification.status == VerificationStatus.PASS

        # Turn 2: refine one function only. Only compute_total's source
        # actually changed, so only it should be re-sent through Z3.
        turn2 = session.refine("add a total variable to compute_total")
        assert turn2.status == "success"
        assert session._incremental_verifier.last_reverified == ["compute_total"]
        assert llm.call_count == 2  # one call per turn, no retries burned

        # Turn 3: ask for the proof — must NOT call the LLM or re-verify anything.
        turn3 = session.refine("show me the proof")
        assert llm.call_count == 2  # unchanged: no LLM call for this turn
        assert session._incremental_verifier.last_reverified == ["compute_total"]  # unchanged from turn 2
        assert turn3.code == turn2.code
        assert "passed" in turn3.explanation.lower() or "✓" in turn3.explanation

        assert len(session.turns) == 3
        assert [t.user_message for t in session.turns] == [
            "write compute_total and compute_average",
            "add a total variable to compute_total",
            "show me the proof",
        ]
