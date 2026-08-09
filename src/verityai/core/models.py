"""The domain model of the harness. Zero dependencies beyond Pydantic.

This module inherits the one architectural rule that survived the pivot: the
model layer depends on nothing, so every engine (context, memory, graph,
consistency, reliability) can depend on it without any of them depending on
each other. The old `ontology/models.py` held that position; the difference is
what the models are *about*. They no longer describe a code-generation run
(prompt, generated code, Z3 verdict) — they describe the state of an
engineering task that an external agent is working on.

Two modelling decisions carry weight downstream:

1. `Decision.status` is a lifecycle, not a boolean. An agent re-proposing an
   approach that was explicitly rejected is one of the failure modes the
   harness exists to catch, and you cannot catch it if superseded decisions
   are deleted rather than marked. Nothing in `.verity/` is ever overwritten;
   history is the point.

2. `Fact` separates `source`, `evidence` and `confidence` into three fields.
   Collapsing them into one "trusted: bool" is what makes a system launder an
   LLM's guess into a fact. A `Fact` whose `evidence` is empty is an
   assumption wearing a fact's clothes, and `Fact.is_grounded` says so.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    """Timezone-aware UTC timestamp.

    `datetime.utcnow()` (used by the pre-pivot code) returns a *naive*
    datetime, which compares unequal to an aware one and silently breaks
    ordering the moment anything is serialized and read back.
    """
    return datetime.now(timezone.utc)


# --- Enumerations --------------------------------------------------------


class DecisionStatus(str, Enum):
    """Lifecycle of an engineering decision."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class Relevance(str, Enum):
    """How a context item relates to the task at hand.

    The five buckets are the ones the Context Engine reports on. `CRITICAL`
    is not "very relevant" — it is a hard guarantee that the item survives
    pruning at any budget, which is why it is a separate bucket rather than
    a high score.
    """

    CRITICAL = "critical"
    RELEVANT = "relevant"
    REDUNDANT = "redundant"
    OBSOLETE = "obsolete"
    IRRELEVANT = "irrelevant"


class ItemKind(str, Enum):
    """Provenance of a context item — what produced this text.

    Kept separate from `Relevance` because they answer different questions:
    kind is a fact about origin, relevance is a judgement about usefulness.
    Tool output is singled out because it is the dominant source of context
    bloat and gets its own filtering pass.
    """

    USER_MESSAGE = "user_message"
    AGENT_MESSAGE = "agent_message"
    TOOL_OUTPUT = "tool_output"
    FILE_CONTENT = "file_content"
    SYSTEM = "system"
    MEMORY = "memory"


class RecordType(str, Enum):
    """The kinds of state the Memory Engine persists, one JSONL file each."""

    TASK = "task"
    DECISION = "decision"
    CONSTRAINT = "constraint"
    DISCOVERY = "discovery"
    FAILURE = "failure"
    FACT = "fact"


# --- Evidence ------------------------------------------------------------


class Evidence(BaseModel):
    """A pointer to something checkable that supports a claim.

    Deliberately a pointer and not a copy: `locator` identifies where the
    support lives (a file path with an optional line range, a commit sha, a
    command that was run) so it can be re-checked later and found stale.
    A `content_hash` of what was seen at capture time is what makes staleness
    detectable at all — if the hash no longer matches, the evidence has moved
    on and anything resting on it is suspect.
    """

    kind: str  # "file" | "commit" | "command" | "test" | "config"
    locator: str
    excerpt: str | None = None
    content_hash: str | None = None
    captured_at: datetime = Field(default_factory=_now)


# --- State records -------------------------------------------------------


class Record(BaseModel):
    """Fields shared by everything written to `.verity/state/`.

    Every record is append-only and carries its own provenance. `source`
    answers "who claimed this" (`cli`, `mcp:claude-code`, `extract:commit`),
    which is what lets the harness later distinguish something the developer
    asserted from something an agent inferred.
    """

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=_now)
    source: str = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class Task(BaseModel):
    """What the agent is currently trying to accomplish.

    Not a `Record`: there is one active task at a time and it is the root
    that everything else hangs off, so it is stored as a single object rather
    than an append-only stream.
    """

    id: UUID = Field(default_factory=uuid4)
    title: str
    description: str = ""
    status: str = "active"  # active | done | abandoned
    next_action: str | None = None
    relevant_files: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Decision(Record):
    """A choice that was made, and why.

    `supersedes` forms a chain rather than mutating the old record, so the
    full deliberation history stays reconstructible. A `REJECTED` decision is
    just as load-bearing as an `ACTIVE` one — it is the thing an agent must
    not quietly re-propose three hours later.
    """

    statement: str
    rationale: str = ""
    status: DecisionStatus = DecisionStatus.ACTIVE
    supersedes: UUID | None = None


class Constraint(Record):
    """A rule the solution must respect, whatever else changes.

    `hard=True` means a violation invalidates the work rather than merely
    costing quality points. The distinction drives how loudly the Consistency
    Engine reports a violation, so it is recorded rather than inferred.
    """

    statement: str
    hard: bool = True
    origin: str = ""  # "user" | "codebase" | "policy" | ...


class Discovery(Record):
    """Something learned about the project that was not known at the start.

    This is the class of information most expensive to lose: it was paid for
    with tool calls and reading, and an agent that forgets it will pay again.
    """

    statement: str


class Failure(Record):
    """Something that was tried and did not work.

    Stored so the same dead end is not walked twice — the single highest-value
    record type for long tasks, and the one agents are worst at retaining.
    """

    attempted: str
    error: str = ""
    resolved: bool = False


class Fact(Record):
    """A claim about the project, with its grounding kept explicit.

    `confidence` is *not* a quality score for the claim. It records how sure
    the recorder was, and is never inflated by the harness. Grounding is a
    separate, structural question answered by `is_grounded`.
    """

    statement: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    verified: bool = False

    @property
    def is_grounded(self) -> bool:
        """True when at least one piece of checkable evidence backs this.

        An ungrounded `Fact` is an assumption. Callers that treat the two
        the same are the reason hallucinated claims propagate.
        """
        return bool(self.evidence)


# --- Context -------------------------------------------------------------


class ContextItem(BaseModel):
    """One addressable unit of context, with its measurement attached.

    `token_count` and `token_method` travel together on purpose. A count
    without its method is not a measurement — an exact tiktoken count and a
    chars/4 estimate are different kinds of number, and a report that mixes
    them silently is misleading. Nothing in this codebase prints one without
    the other.
    """

    id: UUID = Field(default_factory=uuid4)
    kind: ItemKind
    content: str
    token_count: int = 0
    token_method: str = "unmeasured"
    relevance: Relevance | None = None
    # Why the classifier landed on that relevance. Populated by
    # `context/classify.py`; always human-auditable, never a bare score.
    relevance_reason: str = ""
    # Position in the original, pre-pruning context. Retained so a pruned
    # context can be diffed against the input it came from.
    original_index: int = 0
    content_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_protected(self) -> bool:
        """Critical items survive pruning regardless of budget."""
        return self.relevance is Relevance.CRITICAL


class ContextHealth(BaseModel):
    """Multi-dimensional health of a context window.

    Every dimension is reported alongside the aggregate, always. A single
    number invites exactly the mistake the pre-pivot confidence score made
    (T1: ECE 0.14–0.50, uncalibrated, and in one configuration inverted) —
    it looked authoritative, was not, and hid its own components. `score` here
    is an explicitly-weighted summary of numbers the caller can also see, not
    a measurement in its own right.
    """

    window_usage: float = Field(ge=0.0, le=1.0)
    relevant_ratio: float = Field(ge=0.0, le=1.0)
    critical_retained: float = Field(ge=0.0, le=1.0)
    redundancy: float = Field(ge=0.0, le=1.0)
    tool_noise: float = Field(ge=0.0, le=1.0)
    stale_count: int = 0
    contradiction_count: int = 0
    total_tokens: int = 0
    token_method: str = "unmeasured"
    notes: list[str] = Field(default_factory=list)

    @property
    def score(self) -> float:
        """Weighted summary in [0, 1]. Never display this without the parts.

        Weights are a stated editorial judgement, not an empirical result:
        losing critical memory is treated as the worst outcome, followed by
        low relevance density, then redundancy and tool noise, with window
        pressure counting least because a full window is only a problem if
        what fills it is poor.
        """
        return round(
            0.35 * self.critical_retained
            + 0.25 * self.relevant_ratio
            + 0.15 * (1.0 - self.redundancy)
            + 0.15 * (1.0 - self.tool_noise)
            + 0.10 * (1.0 - self.window_usage),
            3,
        )


class PruneStage(BaseModel):
    """Token accounting for one stage of the pruning pipeline.

    Per-stage rather than just before/after so a regression can be traced to
    the stage that caused it, and so a stage that earns nothing is visible
    and can be removed.
    """

    name: str
    items_before: int
    items_after: int
    tokens_before: int
    tokens_after: int
    duration_seconds: float = 0.0

    @property
    def tokens_saved(self) -> int:
        return self.tokens_before - self.tokens_after


class PruneResult(BaseModel):
    """Outcome of running the context pipeline.

    Carries the full stage-by-stage ledger rather than a summary, because the
    headline claim of this project is a token-savings number and it has to be
    auditable line by line to be worth anything.
    """

    items: list[ContextItem]
    stages: list[PruneStage] = Field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    token_method: str = "unmeasured"
    budget: int | None = None
    budget_met: bool = True
    dropped_critical: list[UUID] = Field(default_factory=list)

    @property
    def tokens_saved(self) -> int:
        return self.tokens_before - self.tokens_after

    @property
    def reduction_ratio(self) -> float:
        """Fraction of tokens removed, in [0, 1]. 0.0 when there was nothing."""
        if self.tokens_before <= 0:
            return 0.0
        return round(self.tokens_saved / self.tokens_before, 4)


# --- Snapshots -----------------------------------------------------------


class Snapshot(BaseModel):
    """A restorable point-in-time capture of task state.

    Context only. Code rollback is git's job — the harness may *recommend*
    reverting a commit but never rewrites the user's repository, which keeps
    the blast radius of a bad restore inside `.verity/`.
    """

    number: int
    label: str = ""
    task: Task | None = None
    decisions: list[Decision] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    discoveries: list[Discovery] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    git_sha: str | None = None
