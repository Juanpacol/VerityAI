"""Continuous learning loop: production feedback -> candidate KG rule.

Scope for this module (Phase 2 Week 3): capture accept/reject/correct
feedback tied to a `ReasoningTrace`, and derive an unvalidated *candidate*
Rule from it. Validating that candidate against Z3 and writing it to the
KG behind a human-approval step is Phase 2 Week 4 work — kept separate so
an unvalidated candidate can never silently reach the KG.
"""

import logging
from typing import Optional
from uuid import UUID

from verityai.ontology.models import Feedback, FeedbackType, ReasoningTrace, Rule

logger = logging.getLogger(__name__)


class FeedbackStore:
    """In-memory capture of feedback events, keyed by the trace they respond to.

    A production deployment would back this with Postgres the same way
    `TraceStore` does; this stays in-memory because Week 3's scope is the
    feedback schema and the candidate-rule derivation pipeline, not a new
    persistence layer.
    """

    def __init__(self):
        self._feedback: list[Feedback] = []

    def record(self, feedback: Feedback) -> None:
        """Capture one feedback event."""
        self._feedback.append(feedback)
        logger.info(
            f"Recorded {feedback.feedback_type.value} feedback for trace {feedback.trace_id}"
        )

    def for_trace(self, trace_id: UUID) -> list[Feedback]:
        """All feedback recorded against a given trace, in recording order."""
        return [f for f in self._feedback if f.trace_id == trace_id]

    def all(self) -> list[Feedback]:
        """Every feedback event recorded so far, in recording order."""
        return list(self._feedback)


def derive_candidate_rule(feedback: Feedback, original_trace: ReasoningTrace) -> Optional[Rule]:
    """Build a candidate KG rule from a reject/correct feedback event.

    Returns None when there is nothing to learn from:
    - ACCEPT feedback (the code was already right).
    - REJECT/CORRECT feedback with no `reason` (no signal to turn into a
      rule condition — a bare "reject" doesn't say *why*).

    The returned Rule is a candidate only: it is not validated against Z3
    and not written to the KG here (that's the Week 4 approval pipeline).
    """
    if feedback.feedback_type == FeedbackType.ACCEPT:
        return None
    if not feedback.reason:
        logger.debug(
            f"No reason on {feedback.feedback_type.value} feedback for trace "
            f"{feedback.trace_id}; cannot derive a candidate rule"
        )
        return None

    return Rule(
        name=f"learned_from_feedback_{feedback.id}",
        description=feedback.reason,
        category="learned",
        condition=feedback.reason,
        severity="medium",
        applies_to=["python"],
        # corrected_code, when present, IS the executable snippet
        # demonstrating the fix -- this is what rule_validation.py runs
        # through Z3 to screen out self-contradictory candidates before
        # a human ever has to look at them.
        test_code=feedback.corrected_code,
        examples={
            "trace_id": str(original_trace.id),
            "rejected_code": original_trace.generated_code,
            "corrected_code": feedback.corrected_code,
        },
    )
