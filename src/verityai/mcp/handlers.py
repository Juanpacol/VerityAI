"""The implementations behind the five MCP tools.

`server.py` holds the tool surface -- names, schemas and the descriptions a
model reads -- and nothing else. Everything it dispatches to lives here, one
function per operation, so that changing what a tool *says* and changing what
it *does* are separate edits to separate files.

Each function is still a thin wrapper over something the CLI also calls. That
is the design constraint from `server.py`'s docstring, and moving the bodies
here does not relax it.
"""

from pathlib import Path

from verityai.context.classify import classify_all, relevance_breakdown
from verityai.context.health import compute_health, render_health
from verityai.context.ingest import load
from verityai.context.prune import ContextPipeline
from verityai.context.tokenizer import TokenCounter
from verityai.core.models import Constraint, Decision, Discovery, Failure, Task
from verityai.memory.handoff import build_handoff, render_token_footer
from verityai.memory.snapshot import SnapshotManager
from verityai.memory.store import CorruptStateError, MemoryStore

EMPTY_GRAPH = 'The code graph is empty. Call code(op="index") first.'


def store_at(root: str | None = None) -> MemoryStore:
    """Resolve the store, creating `.verity/` if the agent has not run init.

    Auto-creating is right here even though the CLI refuses to: an agent
    calling `remember` cannot usefully react to "run verity init first", and
    losing the decision is worse than creating a directory. Which directory,
    though, is not a detail -- `root` comes from `verity-mcp --root` so the
    state lands in the project, not in whatever cwd the client happened to
    launch the server from.
    """
    start = Path(root) if root else None
    found = MemoryStore.discover(start)
    return found if found is not None else MemoryStore.init(start)


def graph_at(root: str | None):
    from verityai.graph.store import GraphStore

    return GraphStore.for_verity_dir(store_at(root).root)


def _empty(graph) -> bool:
    """True when the graph holds nothing, so a query would answer from silence."""
    return not graph.stats().get("nodes.total")


# --- context ---------------------------------------------------------------


def optimize_context(transcript: str, task: str, budget: int) -> str:
    counter = TokenCounter()
    result = ContextPipeline(counter=counter).run(load(transcript), task=task, budget=budget)

    ledger = "\n".join(
        f"  {stage.name:<22} {stage.tokens_before:>8,} -> {stage.tokens_after:>8,}"
        f"  (saved {stage.tokens_saved:,})"
        for stage in result.stages
    )
    body = "\n\n".join(item.content for item in result.items)

    over = (
        ""
        if result.budget_met
        else "\n\nNOTE: over budget. The remaining items are all critical and were not dropped."
    )

    return (
        f"Pruned {result.tokens_before:,} -> {result.tokens_after:,} tokens "
        f"({result.reduction_ratio:.1%} saved, counted with {result.token_method}).\n\n"
        f"{ledger}{over}\n\n--- CONTEXT ---\n\n{body}"
    )


def context_health(transcript: str) -> str:
    counter = TokenCounter()
    pipeline = ContextPipeline(counter=counter)
    items = classify_all([pipeline.measure(i, n) for n, i in enumerate(load(transcript))])

    breakdown = relevance_breakdown(items)
    total = sum(breakdown.values())
    table = "\n".join(
        f"  {bucket:<12} {tokens:>8,}  {(tokens / total if total else 0):>6.1%}"
        for bucket, tokens in sorted(breakdown.items(), key=lambda kv: -kv[1])
    )

    return f"{render_health(compute_health(items, counter=counter))}\n\nBY RELEVANCE\n{table}"


def recall(root: str | None, task: str, context_sample: str) -> str:
    from verityai.context.adaptive import describe_recall
    from verityai.memory.surface import candidates_for

    store = store_at(root)
    counter = TokenCounter()
    candidates = candidates_for(store, task, counter)
    return describe_recall(
        candidates, task, context_sample, counter, see_all_hint='session(op="state")'
    )


# --- memory ----------------------------------------------------------------


def remember(root: str | None, kind: str, statement: str, why: str, hard: bool) -> str:
    store = store_at(root)
    if kind == "decision":
        store.append(Decision(statement=statement, rationale=why, source="mcp"))
    elif kind == "constraint":
        store.append(Constraint(statement=statement, hard=hard, source="mcp"))
    elif kind == "discovery":
        store.append(Discovery(statement=statement, source="mcp"))
    elif kind == "failure":
        store.append(Failure(attempted=statement, error=why, source="mcp"))
    else:  # pragma: no cover - the Literal makes this unreachable
        raise AssertionError(f"unhandled kind {kind!r}")
    return f"{kind.capitalize()} recorded: {statement}"


def set_task(root: str | None, title: str, description: str, next_action: str) -> str:
    store_at(root).set_task(
        Task(title=title, description=description, next_action=next_action or None)
    )
    return f"Task set: {title}"


def state(root: str | None) -> str:
    document, report = build_handoff(store_at(root))
    return f"{document}\n{render_token_footer(report)}"


def handoff(root: str | None, budget: int) -> str:
    document, report = build_handoff(store_at(root), budget=budget)
    return f"{document}\n{render_token_footer(report)}"


# --- snapshots -------------------------------------------------------------


def snapshot(root: str | None, label: str) -> str:
    manager = SnapshotManager(store_at(root))
    try:
        snap = manager.create(label=label)
    except CorruptStateError as exc:
        return f"Refused: {exc}\nFix the corrupt line(s) by hand, or call again with force=true."
    return f"Snapshot {snap.number:03d} created" + (f" ({label})" if label else "")


def restore(root: str | None, number: int) -> str:
    manager = SnapshotManager(store_at(root))
    _, report = manager.get_report(number)
    if not report.exists:
        return f'No snapshot {number:03d}. Use session(op="list") to see what exists.'
    if not report.clean:
        return f"Snapshot {number:03d} exists but is unreadable: {report.note}"

    snap = manager.restore(number)
    assert snap is not None  # report.exists and report.clean already confirmed it
    advice = (
        f"\n\nThis state was captured at commit {snap.git_sha[:12]}. "
        "Verity has not touched your working tree; revert the code yourself if needed."
        if snap.git_sha
        else ""
    )
    return f"Restored snapshot {snap.number:03d}.{advice}"


def list_snapshots(root: str | None) -> str:
    manager = SnapshotManager(store_at(root))
    snapshots = manager.list()
    lines = [f"  {s.number:03d}  {s.created_at:%Y-%m-%d %H:%M}  {s.label}" for s in snapshots]
    lines += [f"  ! {r.source}: {r.note}" for r in manager.integrity()]
    return "\n".join(lines) if lines else "No snapshots yet."


# --- code graph ------------------------------------------------------------


def index(root: str | None, force: bool) -> str:
    from verityai.graph.ingest import ingest_repo

    store = store_at(root)
    with graph_at(root) as graph:
        report = ingest_repo(store.root.parent, graph, force=force)
        stats = graph.stats()

    return (
        f"{report.coverage_note}\n"
        f"{stats['nodes.total']:,} nodes, {stats['edges.total']:,} edges "
        f"in {report.duration_seconds}s.\n"
        f"{stats['edges.unresolved']:,} calls unresolved (builtins, methods on "
        "untyped locals) -- kept, not discarded."
    )


def find(root: str | None, task: str, limit: int) -> str:
    from verityai.graph.query import GraphQuery, render_relevant

    with graph_at(root) as graph:
        if _empty(graph):
            return EMPTY_GRAPH
        return render_relevant(GraphQuery(graph).context_for(task, limit=limit))


def define(root: str | None, name: str) -> str:
    from verityai.graph.query import GraphQuery

    with graph_at(root) as graph:
        if _empty(graph):
            return EMPTY_GRAPH

        matches = GraphQuery(graph).define(name)
        if not matches:
            return (
                f"NOT FOUND: no definition of {name!r} in this repository. Do not assume it exists."
            )

        lines = [f"FOUND: {len(matches)} definition(s) of {name!r}."]
        for node in matches[:10]:
            lines.append(f"  {node.kind.value} {node.qualname or node.name}")
            lines.append(f"    {node.path}:{node.line}")
            if node.signature:
                lines.append(f"    {node.signature}")
        return "\n".join(lines)


def impact(root: str | None, name: str) -> str:
    from verityai.graph.query import GraphQuery

    with graph_at(root) as graph:
        if _empty(graph):
            return EMPTY_GRAPH

        query = GraphQuery(graph)
        matches = query.define(name)
        if not matches:
            return f"No definition of {name!r} found."

        node = matches[0]
        callers = query.callers(node.id)
        tests = query.tests_for(node.id)

        lines = [
            f"{node.kind.value} {node.qualname or node.name} ({node.path}:{node.line})",
            "",
        ]
        lines.append(f"Called by ({len(callers)}):")
        lines.extend(f"  {c.qualname or c.name}  ({c.path})" for c in callers[:20] or [])
        if not callers:
            lines.append("  nothing -- it may be dead code, or reached indirectly")
        lines.append("")
        lines.append(f"Tested by ({len(tests)}):")
        lines.extend(f"  {t.qualname or t.name}  ({t.path})" for t in tests[:20] or [])
        if not tests:
            lines.append(
                "  no direct test edge. Note this over-reports: a test driving this "
                "indirectly would not show up here."
            )
        return "\n".join(lines)


# --- verification ----------------------------------------------------------


def check_claims(root: str | None, text: str) -> str:
    from verityai.consistency.check import render_report, run_consistency_check
    from verityai.graph.query import GraphQuery

    store = store_at(root)
    with graph_at(root) as graph:
        query = None if _empty(graph) else GraphQuery(graph)
        report = run_consistency_check(text, query=query, store=store, repo_root=store.root.parent)

    if not report.checks:
        return f"No checkable claims found in that text ({report.claims_extracted} extracted)."

    result = render_report(report)
    if report.contradictions:
        return f"FOUND {len(report.contradictions)} CONTRADICTION(S):\n\n{result}"
    return f"All claims check out.\n\n{result}"


def check_security(root: str | None) -> str:
    from verityai.reliability.report import render_report
    from verityai.reliability.security import caveats_for, scan_repo

    report = scan_repo(store_at(root).root.parent)
    return render_report(report, title="SECURITY", caveats=caveats_for(report.violations))


def check_architecture(root: str | None) -> str:
    from verityai.reliability.architecture import check_architecture_at
    from verityai.reliability.report import render_report

    report = check_architecture_at(store_at(root).root.parent)
    return render_report(report, title="ARCHITECTURE")


def risk(root: str | None, paths: list[str]) -> str:
    from verityai.graph.query import GraphQuery
    from verityai.reliability.risk import classify_paths

    store = store_at(root)
    with graph_at(root) as graph:
        if _empty(graph):
            return (
                "The code graph is empty, so every file would tier 'low' for lack of "
                "signals -- which would read as 'nothing needs scrutiny' when nothing "
                'was measured. Call code(op="index") first.'
            )
        verdicts = classify_paths(paths, GraphQuery(graph), repo_root=store.root.parent)

    order = {"high": 0, "medium": 1, "low": 2}
    lines: list[str] = []
    for path, (tier, reasons) in sorted(verdicts.items(), key=lambda kv: (order[kv[1][0]], kv[0])):
        lines.append(f"[{tier.upper()}] {path}")
        lines.extend(f"    {reason}" for reason in reasons)
    return "\n".join(lines)
