"""Pydantic models for T5 study responses.

The one design decision worth restating here, because it is the whole
reason this model has the shape it does: **stated trust and behavioural
reliance are separate constructs**, and the XAI literature treats
conflating them as a known methodological gap (see the related-work
section of docs/T5_HUMAN_EVAL_PROTOCOL.md). So `trusts_code` (attitudinal:
"do you trust this?") and `merge_intent` (behavioural: "would you actually
ship it?") are two required fields, asked separately and analysed
separately. A participant who says "I trust it" but would still insist on
a full review is a materially different -- and more damning -- data point
than one who would merge it blind, and a single "trusted" boolean would
erase that distinction.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MergeIntent(str, Enum):
    """Behavioural reliance measure -- what the participant would actually do."""

    MERGE_AS_IS = "merge_as_is"
    MERGE_AFTER_SKIM = "merge_after_skim"
    FULL_REVIEW = "full_review"


class KeptElement(str, Enum):
    """Answer to "if you could keep only one element, what would you keep?"."""

    CONFIDENCE = "confidence"
    Z3 = "z3"
    RETRIEVAL = "retrieval"
    CODE = "code"
    OTHER = "other"


class StudyResponseSubmission(BaseModel):
    """What the browser posts. Deliberately does NOT include `condition`.

    The condition is stamped server-side from the run registry. Accepting
    it from the client would let a participant (or a broken retry) report a
    condition they were not actually shown, which would silently corrupt
    the manipulation this whole study rests on.
    """

    run_id: UUID
    trusts_code: bool
    trust_reason: str = ""
    merge_intent: MergeIntent
    kept_element: Optional[KeptElement] = None
    kept_element_other: Optional[str] = None
    reduced_trust_note: Optional[str] = None
    comparison_to_current_tools: Optional[str] = None
    # Covariate, recorded not screened on -- the protocol is explicit that
    # experience level is worth knowing but is not an eligibility filter.
    experience_with_ai_tools: Optional[str] = None


class StudyResponse(StudyResponseSubmission):
    """A stored response, with the server-assigned fields filled in."""

    id: UUID = Field(default_factory=uuid4)
    condition: str  # "A" | "B" | "C", from the registry
    created_at: datetime = Field(default_factory=datetime.utcnow)
