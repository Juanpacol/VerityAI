"""MCP server: the same harness core, exposed to an agent instead of a human.

Every tool here is a thin wrapper over a function the CLI also calls. That is
the design constraint, not an accident — when an agent gets a surprising
context back, a person must be able to reproduce it with `verity context` and
see the same thing. A tool with logic of its own would break that.

What this integration can and cannot do, stated plainly because it bounds
every claim the project can make:

- Verity **cannot see the agent's real context window.** MCP is a cooperative
  protocol; the agent calls a tool and gets an answer. There is no interception
  point, so "we pruned your context" is only true of context the agent chose to
  hand over.
- Verity **can** hold state the agent would otherwise lose, and hand back a
  reconstruction after a reset. That is genuinely useful and does not require
  seeing the window.

Tool descriptions are written for a model to read. They say when to call the
tool, not just what it does, because a tool an agent never thinks to call is
worth nothing.
"""

from pathlib import Path

from verityai.context.classify import classify_all, relevance_breakdown
from verityai.context.health import compute_health, render_health
from verityai.context.ingest import load
from verityai.context.prune import ContextPipeline
from verityai.context.tokenizer import TokenCounter
from verityai.core.models import Constraint, Decision, Discovery, Failure, Task
from verityai.memory.handoff import build_handoff
from verityai.memory.snapshot import SnapshotManager
from verityai.memory.store import MemoryStore

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - import guard
    FastMCP = None  # type: ignore[assignment]


def _store(root: str | None = None) -> MemoryStore:
    """Resolve the store, creating `.verity/` if the agent has not run init.

    Auto-creating is right here even though the CLI refuses to: an agent
    calling `save_decision` cannot usefully react to "run verity init first",
    and losing the decision is worse than creating a directory.
    """
    start = Path(root) if root else None
    found = MemoryStore.discover(start)
    return found if found is not None else MemoryStore.init(start)


def build_server(name: str = "verity"):
    """Construct the MCP server.

    A factory rather than a module-level singleton so tests can build an
    isolated instance, and so importing this module never starts anything.
    """
    if FastMCP is None:
        raise RuntimeError(
            "The MCP SDK is not installed. Install it with: pip install 'verityai[mcp]'"
        )

    server = FastMCP(name)

    # --- context -----------------------------------------------------------

    @server.tool()
    def optimize_context(
        transcript: str,
        task: str = "",
        budget: int = 20000,
    ) -> str:
        """Prune a context down to a token budget, keeping what matters.

        Call this when a conversation has grown long, when tool output has
        filled the window, or before handing work to another agent. Pass the
        transcript as JSON messages or plain text.

        Returns the pruned context plus a stage-by-stage ledger of what was
        removed and why. Items marked critical are never dropped, even if that
        means exceeding the budget.
        """
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

    @server.tool()
    def context_health(transcript: str) -> str:
        """Assess the quality of a context, not just how full it is.

        Call this when work has been going on for a while and you want to know
        whether the context is still good — high redundancy, heavy tool noise
        or low relevance density all mean it is time to prune or hand off.

        Reports each dimension separately. Treat the aggregate score as a
        summary of the dimensions, not as a measurement in its own right.
        """
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

    # --- memory ------------------------------------------------------------

    @server.tool()
    def set_task(title: str, description: str = "", next_action: str = "") -> str:
        """Record what you are currently working on.

        Call this at the start of a task. Everything else recorded afterwards
        hangs off it, and it is the first section of any handoff document.
        """
        store = _store()
        store.set_task(Task(title=title, description=description, next_action=next_action or None))
        return f"Task set: {title}"

    @server.tool()
    def save_decision(statement: str, why: str = "") -> str:
        """Record a decision and its rationale, permanently.

        Call this the moment you choose between approaches — especially when
        you reject one. Decisions are never deleted, so a rejected approach
        stays visible and will not be quietly re-proposed later.
        """
        _store().append(Decision(statement=statement, rationale=why, source="mcp"))
        return f"Decision recorded: {statement}"

    @server.tool()
    def save_constraint(statement: str, hard: bool = True) -> str:
        """Record a rule the solution must respect.

        Call this for anything that invalidates the work if violated: a
        dependency you must not add, an interface you must not break, a policy
        you must follow. Set hard=False for a preference rather than a rule.
        """
        _store().append(Constraint(statement=statement, hard=hard, source="mcp"))
        return f"Constraint recorded: {statement}"

    @server.tool()
    def save_discovery(statement: str) -> str:
        """Record something you learned about the project.

        Call this when a tool call teaches you something non-obvious — how a
        module is wired, where a behaviour actually lives. This is information
        you paid tool calls for, and it is expensive to rediscover.
        """
        _store().append(Discovery(statement=statement, source="mcp"))
        return f"Discovery recorded: {statement}"

    @server.tool()
    def save_failure(attempted: str, error: str = "") -> str:
        """Record something you tried that did not work.

        Call this on every dead end. It is the single most valuable thing to
        remember on a long task, and the easiest to forget — without it, the
        same approach gets attempted again several hours later.
        """
        _store().append(Failure(attempted=attempted, error=error, source="mcp"))
        return f"Failure recorded: {attempted}"

    @server.tool()
    def get_state() -> str:
        """Retrieve everything recorded about the current task.

        Call this when starting fresh on an existing task, after a context
        reset, or whenever you are unsure whether something was already
        decided or already tried.
        """
        document, report = build_handoff(_store())
        return f"{document}\n[{report['tokens']:,} tokens, {report['token_method']}]"

    @server.tool()
    def handoff(budget: int = 2000) -> str:
        """Produce a structured handoff document for a fresh session.

        Call this when the context is degrading or you are about to hand work
        to another agent. The document is self-contained: task, state,
        decisions, constraints, discoveries, failures, files, next action.

        Sections are dropped in a fixed order if the budget is tight, and the
        response says which ones went.
        """
        document, report = build_handoff(_store(), budget=budget)
        dropped = (
            f"\n[dropped to fit budget: {', '.join(report['dropped_sections'])}]"
            if report["dropped_sections"]
            else ""
        )
        return f"{document}\n[{report['tokens']:,} tokens, {report['token_method']}]{dropped}"

    # --- code graph --------------------------------------------------------

    def _graph():
        from verityai.graph.store import GraphStore

        return GraphStore.for_verity_dir(_store().root)

    @server.tool()
    def find_relevant_code(task: str, limit: int = 15) -> str:
        """Find code related to a task, by relationship as well as by name.

        Call this before reading files, whenever you need to know what parts
        of the codebase a piece of work touches. It seeds on text and then
        follows call, containment, inheritance and test edges — so it surfaces
        the function that has nothing to do with your search terms but is
        called by one that does, which grep and embedding search both miss.

        Every result says why it was included. Requires `build_code_graph`
        to have been run.
        """
        from verityai.graph.query import GraphQuery, render_relevant

        with _graph() as graph:
            if not graph.stats().get("nodes.total"):
                return "The code graph is empty. Call build_code_graph first."
            return render_relevant(GraphQuery(graph).context_for(task, limit=limit))

    @server.tool()
    def check_symbol_exists(name: str) -> str:
        """Check whether a function, class or method actually exists.

        Call this before asserting that some API is available, and whenever
        you are about to act on a memory of the codebase rather than something
        you just read. It is far cheaper than opening files, and it is the
        difference between "I believe there is a refresh_token method" and
        knowing.
        """
        from verityai.graph.query import GraphQuery

        with _graph() as graph:
            if not graph.stats().get("nodes.total"):
                return "The code graph is empty. Call build_code_graph first."

            matches = GraphQuery(graph).define(name)
            if not matches:
                return (
                    f"NOT FOUND: no definition of {name!r} in this repository. "
                    "Do not assume it exists."
                )

            lines = [f"FOUND: {len(matches)} definition(s) of {name!r}."]
            for node in matches[:10]:
                lines.append(f"  {node.kind.value} {node.qualname or node.name}")
                lines.append(f"    {node.path}:{node.line}")
                if node.signature:
                    lines.append(f"    {node.signature}")
            return "\n".join(lines)

    @server.tool()
    def impact_of_changing(name: str) -> str:
        """See what depends on a symbol before you change it.

        Call this before editing any shared function or class. Returns what
        calls it and which tests exercise it — the blast radius, derived from
        edges rather than from a text search for the name.
        """
        from verityai.graph.query import GraphQuery

        with _graph() as graph:
            if not graph.stats().get("nodes.total"):
                return "The code graph is empty. Call build_code_graph first."

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

    @server.tool()
    def build_code_graph(force: bool = False) -> str:
        """Index the repository into a queryable code graph.

        Call this once at the start of a session, and again after substantial
        edits. It is incremental — unchanged files are skipped — so re-running
        it is cheap.

        The response states how much of the tree is actually represented.
        Python only in this version; other languages are reported as not read
        rather than silently missing.
        """
        from verityai.graph.ingest import ingest_repo

        store = _store()
        with _graph() as graph:
            report = ingest_repo(store.root.parent, graph, force=force)
            stats = graph.stats()

        return (
            f"{report.coverage_note}\n"
            f"{stats['nodes.total']:,} nodes, {stats['edges.total']:,} edges "
            f"in {report.duration_seconds}s.\n"
            f"{stats['edges.unresolved']:,} calls unresolved (builtins, methods on "
            "untyped locals) -- kept, not discarded."
        )

    # --- snapshots ---------------------------------------------------------

    @server.tool()
    def snapshot(label: str = "") -> str:
        """Capture the current task state as a restorable snapshot.

        Call this before anything risky. Snapshots cover context only — code
        rollback is git's job, and Verity never modifies the working tree.
        """
        snap = SnapshotManager(_store()).create(label=label)
        return f"Snapshot {snap.number:03d} created" + (f" ({label})" if label else "")

    @server.tool()
    def restore(number: int) -> str:
        """Restore task state from a snapshot.

        Call this after going down a wrong path, when the current state has
        become confused, or when you need to return to a known-good point.

        Restores context only. If the code also needs reverting, do that
        yourself with git — this tool will not touch your files.
        """
        manager = SnapshotManager(_store())
        snap = manager.restore(number)
        if snap is None:
            return f"No snapshot {number:03d}. Use list_snapshots to see what exists."

        advice = (
            f"\n\nThis state was captured at commit {snap.git_sha[:12]}. "
            "Verity has not touched your working tree; revert the code yourself if needed."
            if snap.git_sha
            else ""
        )
        return f"Restored snapshot {snap.number:03d}.{advice}"

    @server.tool()
    def list_snapshots() -> str:
        """List available snapshots.

        Call this before `restore` to find the right number.
        """
        snapshots = SnapshotManager(_store()).list()
        if not snapshots:
            return "No snapshots yet."
        return "\n".join(
            f"  {s.number:03d}  {s.created_at:%Y-%m-%d %H:%M}  {s.label}" for s in snapshots
        )

    return server


def main() -> None:
    """Run the server over stdio."""
    build_server().run()


if __name__ == "__main__":
    main()
