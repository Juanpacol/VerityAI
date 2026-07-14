"""Phase 2 Week 4 acceptance test: the full continuous learning loop.

Plan's hardened "done" criterion: a simulated "production failure" that
actually updates the KG after passing through Z3 validation + human
approval — not just "the pipeline classes exist".

Chain under test:
  Orchestrator.run() (bad code)
    -> user Feedback (reject/correct, with reason + corrected_code)
    -> continuous_learning.derive_candidate_rule()
    -> rule_validation.validate_candidate_rule()  [Z3 gate]
    -> RuleApprovalQueue                          [human gate]
    -> KGIngestion.ingest_learned_rule()          [KG write, faked driver]
"""

from uuid import uuid4

from tests.fakes import FakeLLMClient, wrap_code
from verityai.agent.continuous_learning import derive_candidate_rule
from verityai.agent.orchestrator import Orchestrator
from verityai.agent.rule_validation import (
    ApprovalDecision,
    RuleApprovalQueue,
    RuleValidationStatus,
    validate_candidate_rule,
)
from verityai.kg.ingestion import KGIngestion
from verityai.ontology.models import Feedback, FeedbackType, GenerationRequest


class FakeSession:
    """Stand-in for a neo4j Session: records every write."""

    def __init__(self, store: dict):
        self._store = store

    def run(self, query, **kwargs):
        self._store[kwargs["id"]] = kwargs
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeDriver:
    """Stand-in for a neo4j Driver: no real Neo4j needed for this test."""

    def __init__(self):
        self.store: dict = {}

    def session(self):
        return FakeSession(self.store)


class TestContinuousLearningHappyPath:
    def test_approved_correction_reaches_the_kg(self):
        # 1. Orchestrator produces code the user isn't happy with, despite
        #    it passing this MVP's internal-consistency check (e.g. it's
        #    missing a guard the user cares about, not something Z3 catches).
        bad_code = "def divide(a, b):\n    return a / b"
        llm = FakeLLMClient([wrap_code(bad_code)])
        orchestrator = Orchestrator(llm_client=llm)
        response = orchestrator.run(
            GenerationRequest(prompt="write a divide function", max_attempts=1)
        )
        trace = response.traces[0]

        # 2. User rejects it with a reason and a corrected snippet.
        feedback = Feedback(
            trace_id=trace.id,
            feedback_type=FeedbackType.CORRECT,
            reason="Division by zero is not guarded against",
            # No function parameters: the AST->Z3 converter only binds
            # locally-assigned variables (see ast_to_smt.py), so a
            # verifiable test_code snippet must assign before asserting.
            # `//` not `/`: the converter's BinOp handling only supports
            # FloorDiv, not true division -- true division inside a
            # `return` used to be silently invisible to non-verifiable
            # tracking (a real bug, since fixed; see ast_to_smt.py's
            # Return-handling comment) and this fixture's `/` was
            # unknowingly relying on that gap to report CONSISTENT.
            corrected_code="def divide():\n    a = 10\n    b = 5\n    assert b != 0\n    return a // b",
        )

        # 3. Derive a candidate rule from the feedback.
        candidate = derive_candidate_rule(feedback, trace)
        assert candidate is not None
        assert candidate.test_code == feedback.corrected_code

        # 4. Z3 gate: the corrected snippet's assert is not self-contradictory.
        validation = validate_candidate_rule(candidate)
        assert validation.status == RuleValidationStatus.CONSISTENT

        # 5. Human gate: queued as pending, then explicitly approved.
        queue = RuleApprovalQueue()
        entry = queue.submit(candidate, validation)
        assert entry.decision == ApprovalDecision.PENDING
        queue.approve(candidate.id, reviewer_note="Reasonable guard, approved")

        assert queue.pending() == []
        assert [e.rule.id for e in queue.approved_unapplied()] == [candidate.id]

        # 6. Ingest into the KG (faked driver -- no live Neo4j needed).
        driver = FakeDriver()
        KGIngestion(driver).ingest_learned_rule(candidate)
        queue.mark_applied(candidate.id)

        assert str(candidate.id) in driver.store
        assert driver.store[str(candidate.id)]["test_code"] == feedback.corrected_code
        assert queue.approved_unapplied() == []  # marked applied, won't be re-ingested


class TestContinuousLearningAutoRejectPath:
    def test_contradictory_correction_is_auto_rejected_without_reaching_kg(self):
        trace_id = uuid4()
        feedback = Feedback(
            trace_id=trace_id,
            feedback_type=FeedbackType.CORRECT,
            reason="Claims x is always 2 but code sets it to 1",
            corrected_code="x = 1\nassert x == 2",  # self-contradictory
        )

        from verityai.ontology.models import ReasoningTrace

        trace = ReasoningTrace(
            id=trace_id,
            user_prompt="p",
            generated_code="x = 1",
            attempt_number=1,
            kg_context={},
            llm_reasoning="",
            confidence_score=0.0,
        )

        candidate = derive_candidate_rule(feedback, trace)
        validation = validate_candidate_rule(candidate)
        assert validation.status == RuleValidationStatus.CONTRADICTORY

        queue = RuleApprovalQueue()
        entry = queue.submit(candidate, validation)

        # Auto-rejected -- no human action needed to keep it out of the KG.
        assert entry.decision == ApprovalDecision.REJECTED
        assert queue.pending() == []
        assert queue.approved_unapplied() == []

        driver = FakeDriver()
        assert driver.store == {}  # never even attempted
