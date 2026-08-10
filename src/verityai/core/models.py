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


# --- Knowledge graph -----------------------------------------------------


class NodeKind(str, Enum):
    """What a graph node represents.

    `EXTERNAL` is the one that earns its place. When a module imports
    `requests`, that import is real structural information even though no file
    in the repository defines it. Dropping it would leave the graph unable to
    answer "what third-party code does this depend on", and would make an
    agent's claim about an external API impossible to check in Phase 3.
    """

    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    TEST = "test"
    EXTERNAL = "external"


class EdgeKind(str, Enum):
    """How two nodes relate.

    All edges are directed and read source-to-target: `CONTAINS` runs from the
    container to the contained, `CALLS` from caller to callee, `TESTS` from
    the test to what it exercises.
    """

    CONTAINS = "contains"
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    TESTS = "tests"


class GraphNode(BaseModel):
    """One entity in the code graph.

    `id` is derived, not random: `kind:path:qualname`. That makes re-ingestion
    an upsert rather than a duplication, and it means a node id is a *readable*
    address a human can reason about in a query result. A UUID here would be
    correct and useless.
    """

    id: str
    kind: NodeKind
    name: str
    qualname: str = ""
    path: str = ""
    line: int | None = None
    end_line: int | None = None
    # Signature for callables, base list for classes -- enough to answer
    # "does this function take the argument the agent thinks it does" without
    # re-reading the file.
    signature: str = ""
    docstring: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def make_id(kind: "NodeKind", path: str, qualname: str = "") -> str:
        """Build the deterministic id for a node."""
        return f"{kind.value}:{path}:{qualname}" if qualname else f"{kind.value}:{path}"


class GraphEdge(BaseModel):
    """A directed relationship between two nodes.

    `resolved` is the field that matters. A call to a name the ingester could
    not tie to a definition is recorded with `resolved=False` and a `target`
    holding the raw name, rather than being dropped. Discarding them would
    throw away precisely the signal Phase 3 needs — a call to something that
    does not exist is what a hallucinated API looks like from the graph's side.
    """

    source: str
    target: str
    kind: EdgeKind
    resolved: bool = True
    line: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestReport(BaseModel):
    """What one ingestion run did, and what it deliberately did not do.

    `skipped` is not an error log. Phase 2 ingests Python only, so every other
    file is recorded as out of scope with a reason — the same discipline as
    the pre-pivot `NOT_VERIFIED` status (ADR-0001), which existed because
    "we did not check this" and "this is fine" must never look alike.
    """

    files_scanned: int = 0
    # Files the ingester was willing to attempt -- i.e. this project's own
    # Python. The denominator for the coverage figure that means something.
    files_eligible: int = 0
    # Python files belonging to a nested project (vendored dependency, cloned
    # reference repo). Counted apart from non-Python files because "we do not
    # read Rust" and "we chose not to read this Python" are different facts.
    files_vendored: int = 0
    files_ingested: int = 0
    files_unchanged: int = 0
    nodes: int = 0
    edges: int = 0
    unresolved_edges: int = 0
    # path -> reason. Non-Python files, syntax errors, unreadable files.
    skipped: dict[str, str] = Field(default_factory=dict)
    duration_seconds: float = 0.0

    @property
    def files_in_graph(self) -> int:
        return self.files_ingested + self.files_unchanged

    @property
    def out_of_scope(self) -> int:
        """Files skipped for being a language this phase does not read."""
        return self.files_scanned - self.files_eligible - self.files_vendored

    @property
    def failed(self) -> int:
        """Eligible files that could not be parsed. These are real problems."""
        return self.files_eligible - self.files_in_graph

    @property
    def coverage_note(self) -> str:
        """One line stating how much of what it *could* read is in the graph.

        The denominator is eligible files, not every file in the tree. An
        earlier version divided by the whole tree and reported "4% coverage"
        on this repository — technically true, wildly misleading, since every
        Python file was in fact ingested and the other 1,266 files were JSON
        evidence records the ingester was never going to read.

        Out-of-scope files are still reported, separately, because a graph
        covering all the Python in a repo that is mostly TypeScript is a fact
        the user needs stated plainly.
        """
        if self.files_eligible == 0:
            return f"no Python files found among {self.files_scanned:,} scanned"

        note = (
            f"{self.files_in_graph}/{self.files_eligible} Python files in the graph "
            f"({self.files_in_graph / self.files_eligible:.0%})"
        )
        if self.failed:
            note += f"; {self.failed} failed to parse"
        if self.files_vendored:
            note += f"; {self.files_vendored} in nested projects (vendored, not yours)"
        if self.out_of_scope:
            note += f"; {self.out_of_scope:,} non-Python files not read (Phase 2 is Python-only)"
        return note


# --- Consistency -----------------------------------------------------------


class ClaimKind(str, Enum):
    """What kind of checkable statement a claim is.

    Each kind maps to exactly one deterministic check in `consistency/check.py`
    — there is no kind here that requires a model to verify. A claim the
    extractor cannot categorize this way is not extracted at all, rather than
    forced into the nearest kind and checked wrongly.

    `DECISION_ALIGNMENT` is the one kind never produced by `extract_claims` —
    it is synthesized directly by `check_decision_resurfacing` against
    `.verity/` memory rather than pulled from a text span, because "does this
    resemble a rejected decision" is a whole-text comparison, not something
    with a single matching substring. It still gets its own kind rather than
    borrowing another one, so a report never mislabels a resurfacing warning
    as a symbol-existence claim.
    """

    SYMBOL_EXISTS = "symbol_exists"
    SYMBOL_RELATION = "symbol_relation"
    FILE_EXISTS = "file_exists"
    DECISION_ALIGNMENT = "decision_alignment"


class Claim(BaseModel):
    """One checkable assertion pulled out of agent-produced text.

    `raw_text` is kept verbatim so a human reviewing a flagged claim can see
    exactly what triggered it, rather than trusting the extractor's summary of
    its own output — the same reasoning as keeping `excerpt` on `Evidence`.
    """

    kind: ClaimKind
    subject: str
    relation: str | None = None
    target: str | None = None
    raw_text: str = ""


class CheckStatus(str, Enum):
    """Outcome of checking one claim against the graph or memory."""

    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


class ClaimCheck(BaseModel):
    """The verdict on one claim, with the evidence that produced it.

    `confidence` here is the check's own certainty in its verdict, not a
    calibrated probability the claim is true — the distinction T1 exists to
    enforce. A hallucinated-symbol check is binary and reports 1.0; a
    decision-resurfacing check is a lexical-overlap heuristic and must report
    something less than certain, because it can be wrong in both directions.
    """

    claim: Claim
    status: CheckStatus
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)


class ConsistencyReport(BaseModel):
    """Every claim checked in one pass, plus what could not be checked at all.

    `degraded_reason` follows the same rule as `RetrievalResult` in Phase 1:
    if the graph has not been built, or a claim's kind has no checker, that is
    stated rather than silently producing zero findings that look like a
    clean bill of health.
    """

    checks: list[ClaimCheck] = Field(default_factory=list)
    claims_extracted: int = 0
    degraded_reason: str | None = None

    @property
    def contradictions(self) -> list[ClaimCheck]:
        return [c for c in self.checks if c.status is CheckStatus.CONTRADICTED]

    @property
    def is_clean(self) -> bool:
        return not self.contradictions


# --- Reliability -----------------------------------------------------------


class VerificationStatus(str, Enum):
    """Outcome of checking one rule against one piece of evidence.

    Rescued from the pre-pivot `ontology.models`, where it named a Z3
    verdict. Its meaning here is narrower and more honest: a forward-chaining
    rule engine over fact strings, not a proof. `FAIL` requires the rule's
    trigger condition to be present and its required mitigation to be absent
    — see `Rule.formal_spec` and `check_for_violation` in
    `reliability/rule_engine.py` for exactly what that means.
    """

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class Rule(BaseModel):
    """A checkable rule, expressed as a PRE/POST fact relationship.

    `formal_spec` uses the small string grammar `rule_engine.py` parses:
    `"PRE: fact_a, fact_b; POST: fact_c"`. This is not a general expression
    language — it is exactly expressive enough for "if this trigger pattern
    is present in the code and this mitigating pattern is absent, that is a
    violation," which is what T6 found pattern-matching could catch that Z3
    structurally cannot (SQL injection, check-then-act races). A rule needing
    more than that needs a different engine, not a richer spec string here.
    """

    id: str
    name: str
    description: str = ""
    category: str = ""  # "security" | "architecture" | ...
    severity: str = "medium"
    formal_spec: str
    applies_to: list[str] = Field(default_factory=lambda: ["python"])


class Finding(BaseModel):
    """One reliability check's verdict on one piece of code.

    Only `FAIL` findings are normally surfaced to a human — `PASS` and
    `UNKNOWN` are kept internally auditable (a rule engine that has never
    produced anything but `FAIL` is as suspicious as one that has never
    produced anything but `PASS`, per T6) but are not findings in the sense
    of "something to act on."
    """

    rule_id: str
    rule_name: str
    status: VerificationStatus
    severity: str = "medium"
    message: str
    path: str = ""
    line: int | None = None


class ReliabilityReport(BaseModel):
    """Every finding from one reliability run, plus what was and wasn't checked.

    `degraded_reason` follows the same rule as `ConsistencyReport`: a report
    with zero findings must be distinguishable from a report that couldn't
    check anything at all.
    """

    findings: list[Finding] = Field(default_factory=list)
    files_scanned: int = 0
    degraded_reason: str | None = None

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.status is VerificationStatus.FAIL]

    @property
    def is_clean(self) -> bool:
        return not self.violations


class ArchitecturePolicy(BaseModel):
    """A declarative statement of which top-level packages may import which.

    This operationalizes the "Dependency rule" section of CLAUDE.md as
    something checkable rather than something hoped for. `"core"` is always
    implicitly allowed as an import target for everyone and never needs
    listing; `"*"` in a package's allowed list means "may import anything"
    (used for `cli` and `mcp`, which by design sit above every engine).
    """

    allowed_imports: dict[str, list[str]] = Field(default_factory=dict)
