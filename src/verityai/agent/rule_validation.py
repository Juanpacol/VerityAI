"""Z3 consistency gate + human-approval queue for candidate KG rules.

A candidate Rule produced by continuous_learning.derive_candidate_rule()
must clear two gates before it's written to the KG:

1. Z3 consistency check (validate_candidate_rule) — the rule's test_code,
   if present, must not be self-contradictory.
2. Human approval (RuleApprovalQueue) — even a Z3-consistent rule still
   requires an explicit approve/reject decision. VerityAI never writes a
   learned rule to the KG unattended, regardless of what Z3 says.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from uuid import UUID

from verityai.ontology.models import Rule, VerificationResult, VerificationStatus
from verityai.symbolic.verify import verify_python_snippet

logger = logging.getLogger(__name__)


class RuleValidationStatus(str, Enum):
    """Outcome of screening a candidate rule's test_code against Z3."""
    CONSISTENT = "consistent"  # Z3 found the test_code satisfiable
    CONTRADICTORY = "contradictory"  # Z3 found it UNSAT -- self-contradictory
    UNVERIFIABLE = "unverifiable"  # No test_code, or outside the verifiable subset


@dataclass(frozen=True)
class RuleValidationResult:
    status: RuleValidationStatus
    verification: VerificationResult
    reason: str


def validate_candidate_rule(rule: Rule, timeout_seconds: float = 3.0) -> RuleValidationResult:
    """Screen a candidate rule's test_code for internal Z3 consistency.

    This does NOT approve the rule for KG ingestion — see
    RuleApprovalQueue for the required human sign-off. It only filters out
    rules that are trivially self-contradictory before a human has to
    spend time reviewing them.
    """
    if not rule.test_code:
        return RuleValidationResult(
            status=RuleValidationStatus.UNVERIFIABLE,
            verification=VerificationResult(
                code_id="", status=VerificationStatus.NOT_VERIFIED, confidence=0.0
            ),
            reason="Rule has no test_code to check against Z3 -- requires manual review",
        )

    verification = verify_python_snippet(rule.test_code, timeout_seconds=timeout_seconds)

    if verification.status == VerificationStatus.FAIL:
        return RuleValidationResult(
            status=RuleValidationStatus.CONTRADICTORY,
            verification=verification,
            reason="test_code is self-contradictory (Z3 UNSAT) -- rejecting candidate",
        )
    if verification.status == VerificationStatus.PASS:
        return RuleValidationResult(
            status=RuleValidationStatus.CONSISTENT,
            verification=verification,
            reason="test_code is Z3-consistent",
        )

    # UNKNOWN / TIMEOUT / NOT_VERIFIED: Z3 couldn't conclusively decide.
    return RuleValidationResult(
        status=RuleValidationStatus.UNVERIFIABLE,
        verification=verification,
        reason=(
            f"Z3 could not conclusively check test_code "
            f"(status={verification.status.value}) -- requires manual review"
        ),
    )


class ApprovalDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class PendingRuleApproval:
    """One candidate rule sitting in the approval queue."""
    rule: Rule
    validation: RuleValidationResult
    decision: ApprovalDecision = ApprovalDecision.PENDING
    reviewer_note: Optional[str] = None
    applied: bool = False  # True once ingest_learned_rule() has written it to the KG


class RuleApprovalQueue:
    """Holds Z3-screened candidate rules pending human approval.

    Every candidate is queued regardless of validate_candidate_rule's
    verdict — a CONTRADICTORY rule is auto-marked REJECTED rather than
    silently dropped, so a human can still see *why* the learning pipeline
    rejected it. Auto-rejection can be overridden via approve() if a
    reviewer disagrees with Z3 (e.g. the test_code itself was wrong, not
    the rule).
    """

    def __init__(self):
        self._queue: list[PendingRuleApproval] = []

    def submit(self, rule: Rule, validation: RuleValidationResult) -> PendingRuleApproval:
        """Queue a candidate rule after it has been Z3-screened."""
        entry = PendingRuleApproval(rule=rule, validation=validation)
        if validation.status == RuleValidationStatus.CONTRADICTORY:
            entry.decision = ApprovalDecision.REJECTED
            entry.reviewer_note = "Auto-rejected: Z3 found test_code contradictory"
        self._queue.append(entry)
        return entry

    def pending(self) -> list[PendingRuleApproval]:
        """Entries still awaiting a human decision."""
        return [e for e in self._queue if e.decision == ApprovalDecision.PENDING]

    def approve(self, rule_id: UUID, reviewer_note: Optional[str] = None) -> None:
        """Approve a queued rule (including overriding an auto-rejection)."""
        entry = self._find(rule_id)
        entry.decision = ApprovalDecision.APPROVED
        entry.reviewer_note = reviewer_note

    def reject(self, rule_id: UUID, reviewer_note: Optional[str] = None) -> None:
        """Reject a queued rule."""
        entry = self._find(rule_id)
        entry.decision = ApprovalDecision.REJECTED
        entry.reviewer_note = reviewer_note

    def approved_unapplied(self) -> list[PendingRuleApproval]:
        """Approved entries not yet written to the KG."""
        return [
            e
            for e in self._queue
            if e.decision == ApprovalDecision.APPROVED and not e.applied
        ]

    def mark_applied(self, rule_id: UUID) -> None:
        """Record that an approved rule has been ingested into the KG."""
        self._find(rule_id).applied = True

    def _find(self, rule_id: UUID) -> PendingRuleApproval:
        for entry in self._queue:
            if entry.rule.id == rule_id:
                return entry
        raise KeyError(f"No queued approval entry for rule {rule_id}")
