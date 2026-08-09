"""Checking claims against the graph and against memory. No model involved.

Two independent things get checked here, and they answer different questions:

- **Claims about code** (`SYMBOL_EXISTS`, `SYMBOL_RELATION`, `FILE_EXISTS`) are
  checked against the code graph from Phase 2. This is the hallucination
  check: an agent asserting `AuthService.refresh_token` exists, or that
  `OrderService` calls `PaymentGateway`, can be contradicted by a graph that
  has no such node or edge.
- **Decision resurfacing** is checked against `.verity/` memory, not the
  graph. It answers a different question: is this text proposing something
  that was already tried and explicitly rejected? That is a lexical-overlap
  heuristic, not a lookup, and it is scored and reported as one — it can
  produce a false positive on a coincidental phrase, so it is never allowed to
  read as more certain than a graph lookup.

Both share the same discipline the graph layer already established: an
ambiguous or unresolvable case is `UNVERIFIABLE`, never guessed into
`SUPPORTED` or `CONTRADICTED`. A relation claim whose target matches an edge
the ingester deliberately left unresolved (see ADR-0006 — ambiguous names are
not guessed at) is exactly this case, and is reported as such rather than as
a false contradiction.
"""

from pathlib import Path

from verityai.consistency.claims import extract_claims
from verityai.context.rank import bm25_rank
from verityai.core.models import (
    CheckStatus,
    Claim,
    ClaimCheck,
    ClaimKind,
    ConsistencyReport,
    DecisionStatus,
    Evidence,
)
from verityai.graph.query import GraphQuery
from verityai.graph.store import EdgeKind
from verityai.memory.store import MemoryStore

# Below this lexical-overlap score, a coincidental resemblance to a rejected
# decision is treated as noise rather than a resurfacing. Chosen so that
# sharing one or two common words does not flag every sentence in a
# conversation about the same general area of the code.
_RESURFACING_THRESHOLD = 0.15

_RELATION_EDGE_KINDS = {
    "calls": EdgeKind.CALLS,
    "inherits": EdgeKind.INHERITS,
}


def check_symbol_exists(claim: Claim, query: GraphQuery) -> ClaimCheck:
    """Does anything in the graph define this symbol?"""
    matches = query.define(claim.subject)
    if matches:
        return ClaimCheck(
            claim=claim,
            status=CheckStatus.SUPPORTED,
            confidence=1.0,
            explanation=f"{len(matches)} definition(s) found",
            evidence=[
                Evidence(kind="file", locator=f"{m.path}:{m.line}" if m.line else m.path)
                for m in matches[:5]
            ],
        )
    return ClaimCheck(
        claim=claim,
        status=CheckStatus.CONTRADICTED,
        confidence=1.0,
        explanation=f"no definition of {claim.subject!r} found anywhere in the graph",
    )


def check_symbol_relation(claim: Claim, query: GraphQuery) -> ClaimCheck:
    """Does the graph actually contain the claimed relationship?"""
    edge_kind = _RELATION_EDGE_KINDS.get(claim.relation or "")
    if edge_kind is None:
        return ClaimCheck(
            claim=claim,
            status=CheckStatus.UNVERIFIABLE,
            confidence=0.0,
            explanation=f"no checker for relation {claim.relation!r}",
        )

    subject_nodes = query.define(claim.subject)
    if not subject_nodes:
        return ClaimCheck(
            claim=claim,
            status=CheckStatus.CONTRADICTED,
            confidence=1.0,
            explanation=f"no definition of {claim.subject!r} found",
        )

    target_nodes = query.define(claim.target or "")
    if not target_nodes:
        return ClaimCheck(
            claim=claim,
            status=CheckStatus.CONTRADICTED,
            confidence=1.0,
            explanation=f"{claim.subject!r} exists, but {claim.target!r} was not found either",
        )
    target_ids = {node.id for node in target_nodes}

    for subject_node in subject_nodes:
        neighbours = query.store.neighbours(subject_node.id, kinds=[edge_kind], direction="out")
        if any(node.id in target_ids for node in neighbours):
            return ClaimCheck(
                claim=claim,
                status=CheckStatus.SUPPORTED,
                confidence=1.0,
                explanation=f"a resolved {claim.relation} edge connects them",
                evidence=[
                    Evidence(kind="file", locator=f"{subject_node.path}:{subject_node.line}")
                ],
            )

    # An unresolved edge with a matching raw name means the ingester saw a
    # call to something named like the target but could not tie it to a
    # definition -- per ADR-0006 that is deliberate, not a gap, and it means
    # this claim cannot be confirmed OR denied from the graph alone.
    target_name = (claim.target or "").rsplit(".", 1)[-1]
    for subject_node in subject_nodes:
        for edge in query.store.edges_from(subject_node.id, kind=edge_kind):
            if not edge.resolved and edge.target.rsplit(".", 1)[-1] == target_name:
                return ClaimCheck(
                    claim=claim,
                    status=CheckStatus.UNVERIFIABLE,
                    confidence=0.0,
                    explanation=(
                        f"an unresolved {claim.relation} edge named {edge.target!r} exists; "
                        "the ingester could not confirm it points at this specific target"
                    ),
                )

    return ClaimCheck(
        claim=claim,
        status=CheckStatus.CONTRADICTED,
        confidence=0.9,
        explanation=f"both symbols exist, but no {claim.relation} edge connects them",
    )


def check_file_exists(claim: Claim, repo_root: Path | None) -> ClaimCheck:
    """Does this path actually exist in the repository?"""
    if repo_root is None:
        return ClaimCheck(
            claim=claim,
            status=CheckStatus.UNVERIFIABLE,
            confidence=0.0,
            explanation="no repository root provided",
        )

    candidate = (repo_root / claim.subject.lstrip("/")).resolve()
    try:
        candidate.relative_to(Path(repo_root).resolve())
    except ValueError:
        # The claim tried to point outside the repository; do not follow it.
        return ClaimCheck(
            claim=claim,
            status=CheckStatus.CONTRADICTED,
            confidence=1.0,
            explanation="path resolves outside the repository root",
        )

    if candidate.exists():
        return ClaimCheck(
            claim=claim,
            status=CheckStatus.SUPPORTED,
            confidence=1.0,
            explanation="file exists",
            evidence=[Evidence(kind="file", locator=claim.subject)],
        )
    return ClaimCheck(
        claim=claim,
        status=CheckStatus.CONTRADICTED,
        confidence=1.0,
        explanation=f"no file at {claim.subject!r}",
    )


def check_decision_resurfacing(text: str, store: MemoryStore) -> list[ClaimCheck]:
    """Flag text that lexically resembles a decision already rejected or superseded.

    A heuristic, not a lookup — reported with a confidence below 1.0 always,
    because a shared phrase is weaker evidence than a graph edge and must
    never be displayed as if it carries the same certainty.
    """
    inactive = [
        d
        for d in store.decisions(include_inactive=True)
        if d.status in (DecisionStatus.REJECTED, DecisionStatus.SUPERSEDED)
    ]
    if not inactive:
        return []

    documents = [d.statement for d in inactive]
    ranks, scores = bm25_rank(text, documents)
    # Normalize against the best possible score in this small corpus so the
    # threshold means the same thing regardless of corpus size or wording.
    if not scores:
        return []
    max_score = max(scores.values())
    if max_score <= 0:
        return []

    checks: list[ClaimCheck] = []
    for idx, score in scores.items():
        normalized = score / max_score
        if normalized < _RESURFACING_THRESHOLD:
            continue
        decision = inactive[idx]
        checks.append(
            ClaimCheck(
                claim=Claim(
                    kind=ClaimKind.DECISION_ALIGNMENT,
                    subject=decision.statement,
                    raw_text=text[:200],
                ),
                status=CheckStatus.CONTRADICTED,
                confidence=round(min(normalized, 0.85), 2),
                explanation=(
                    f"resembles a {decision.status.value} decision: {decision.statement!r}"
                    + (f" ({decision.rationale})" if decision.rationale else "")
                ),
            )
        )
    return checks


def run_consistency_check(
    text: str,
    query: GraphQuery | None = None,
    store: MemoryStore | None = None,
    repo_root: Path | None = None,
) -> ConsistencyReport:
    """Extract claims from `text` and check every one that can be checked.

    Any backend that is missing degrades the corresponding checks to
    `UNVERIFIABLE` rather than skipping them silently — the report always
    accounts for every extracted claim.
    """
    claims = extract_claims(text)
    checks: list[ClaimCheck] = []
    degraded: list[str] = []

    if query is None:
        degraded.append("no code graph provided -- symbol and relation claims unverifiable")

    for claim in claims:
        if claim.kind is ClaimKind.SYMBOL_EXISTS:
            if query is None:
                checks.append(
                    ClaimCheck(
                        claim=claim,
                        status=CheckStatus.UNVERIFIABLE,
                        confidence=0.0,
                        explanation="no code graph provided",
                    )
                )
            else:
                checks.append(check_symbol_exists(claim, query))
        elif claim.kind is ClaimKind.SYMBOL_RELATION:
            if query is None:
                checks.append(
                    ClaimCheck(
                        claim=claim,
                        status=CheckStatus.UNVERIFIABLE,
                        confidence=0.0,
                        explanation="no code graph provided",
                    )
                )
            else:
                checks.append(check_symbol_relation(claim, query))
        elif claim.kind is ClaimKind.FILE_EXISTS:
            checks.append(check_file_exists(claim, repo_root))

    if store is not None:
        checks.extend(check_decision_resurfacing(text, store))
    else:
        degraded.append("no memory store provided -- decision resurfacing not checked")

    return ConsistencyReport(
        checks=checks,
        claims_extracted=len(claims),
        degraded_reason="; ".join(degraded) if degraded else None,
    )


def render_report(report: ConsistencyReport) -> str:
    """Format a consistency report for a terminal or an agent."""
    if not report.checks:
        base = f"No checkable claims found ({report.claims_extracted} extracted)."
        if report.degraded_reason:
            base += f"\ndegraded: {report.degraded_reason}"
        return base

    lines = []
    for check in report.checks:
        marker = {
            CheckStatus.SUPPORTED: "OK  ",
            CheckStatus.CONTRADICTED: "FAIL",
            CheckStatus.UNVERIFIABLE: "??? ",
        }[check.status]
        subject = check.claim.subject
        if check.claim.relation and check.claim.target:
            subject = f"{check.claim.subject} {check.claim.relation} {check.claim.target}"
        lines.append(f"  [{marker}] {subject}")
        lines.append(f"         {check.explanation} (confidence {check.confidence:.0%})")

    if report.degraded_reason:
        lines.append("")
        lines.append(f"  degraded: {report.degraded_reason}")

    lines.append("")
    lines.append(
        f"  {len(report.contradictions)} contradiction(s) of {len(report.checks)} checked claim(s)"
    )
    return "\n".join(lines)
