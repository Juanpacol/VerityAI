"""Checking the codebase's actual dependencies against its declared policy.

This is the canonical "deterministic first" example from the original design
document: *circular dependency? a graph algorithm, not a model call.* Import
cycles are already covered by `graph.query.import_cycles` (Phase 2). This
module adds the check that matters more in practice — not just "is there a
cycle" but "does this specific import go somewhere the architecture says it
shouldn't" — checked against the exact policy CLAUDE.md's "Dependency rule"
section states in prose, made executable instead of aspirational.

The check reuses the Phase 2 graph's `IMPORTS` edges rather than re-parsing
imports itself. Two engines analyzing import statements independently would
be redundant work with two chances to disagree about what "an import" means;
one graph, checked by two different policies (the code-structure questions in
`graph/query.py`, the architecture-conformance question here), is the reuse
this whole layered design is meant to produce.

**Found on the first real run against this repository**: `memory/handoff.py`
imports `context.tokenizer`, which the *diagram* in CLAUDE.md did not list —
it said `memory` depended on `core` alone. The need is real (a handoff
document has to fit a token budget, which means counting tokens), so the fix
was to correct the documented policy to match the legitimate dependency,
not to break the import. This module exists specifically to catch the next
case where the correct call is the opposite one.
"""

from pathlib import Path

from verityai.core.models import (
    ArchitecturePolicy,
    Finding,
    NodeKind,
    ReliabilityReport,
    VerificationStatus,
)
from verityai.graph.store import EdgeKind, GraphStore

# The policy CLAUDE.md's "Dependency rule" section states in prose. "core" is
# never listed as a target -- it is always allowed, for everyone, and listing
# it everywhere would just be noise. "*" means "may import any package,"
# reserved for the two that sit above every engine by design.
DEFAULT_POLICY = ArchitecturePolicy(
    allowed_imports={
        "core": [],
        "context": [],
        "memory": ["context"],  # handoff.py needs token counting for its budget
        "graph": ["context"],  # query.py reuses context.rank's BM25 ranker
        "consistency": ["graph", "context", "memory"],
        "reliability": ["graph", "analysis"],
        "bench": ["context"],
        "analysis": [],
        "observability": [],
        "cli": ["*"],
        "mcp": ["*"],
    }
)


def top_package(qualname: str) -> str | None:
    """The engine package a module belongs to, or None if it is not one.

    Only qualnames under `verityai.` are policy subjects — test files, ad-hoc
    scripts, and anything else living elsewhere in the graph are out of
    scope for an *internal* dependency policy, the same way Phase 2 declared
    non-Python files out of scope for ingestion rather than silently
    misreading them.
    """
    if not qualname.startswith("verityai."):
        return None
    parts = qualname.split(".")
    return parts[1] if len(parts) >= 2 else None


def check_architecture(
    store: GraphStore,
    policy: ArchitecturePolicy | None = None,
) -> ReliabilityReport:
    """Check every resolved cross-package import against `policy`.

    Only *resolved* IMPORTS edges are checked — an edge still pointing at an
    `EXTERNAL` placeholder is a third-party dependency, which is a different
    question (and not one this repository restricts) from whether one
    first-party engine may depend on another.
    """
    policy = policy or DEFAULT_POLICY
    findings: list[Finding] = []
    files = store.all_nodes(NodeKind.FILE)
    scanned = 0

    for file_node in files:
        source_top = top_package(file_node.qualname)
        if source_top is None:
            continue
        scanned += 1

        for edge in store.edges_from(file_node.id, kind=EdgeKind.IMPORTS):
            if not edge.resolved:
                continue

            target = store.get_node(edge.target)
            if target is None or target.kind is not NodeKind.FILE:
                continue

            target_top = top_package(target.qualname)
            if target_top is None or target_top in (source_top, "core"):
                continue

            allowed = policy.allowed_imports.get(source_top, [])
            if "*" in allowed or target_top in allowed:
                continue

            findings.append(
                Finding(
                    rule_id="architecture-dependency",
                    rule_name="Declared dependency direction",
                    status=VerificationStatus.FAIL,
                    severity="high",
                    message=(
                        f"'{source_top}' imports '{target_top}', which the declared "
                        f"policy does not allow (allowed: {allowed or 'nothing beyond core'})"
                    ),
                    path=file_node.path,
                    line=edge.line,
                )
            )

    return ReliabilityReport(findings=findings, files_scanned=scanned)


def check_architecture_at(
    root: Path,
    policy: ArchitecturePolicy | None = None,
) -> ReliabilityReport:
    """Convenience entry point: build a throwaway in-memory graph and check it.

    For a one-shot CLI/MCP call where the caller doesn't already have a
    `.verity/graph.db` open. Callers who do (the CLI's persistent graph
    command) should use `check_architecture` directly against it instead of
    re-ingesting.
    """
    from verityai.graph.ingest import ingest_repo

    with GraphStore() as store:
        ingest_repo(root, store)
        return check_architecture(store, policy=policy)
