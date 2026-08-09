"""The `verity` command line interface.

The CLI is the primary surface, not a wrapper around a server. Everything the
MCP layer exposes to an agent is reachable here by a human, against the same
core objects — the core cannot tell which one is calling it. That is what
keeps the harness debuggable: when an agent gets a surprising context back,
you can reproduce it by hand.

Output style: token counts always carry their counting method, and any
degraded path says why it degraded. Both rules come from the same lesson —
a number without its provenance invites more confidence than it earned.
"""

import sys
from pathlib import Path
from typing import Optional

import typer

from verityai.context.classify import relevance_breakdown
from verityai.context.health import compute_health, critical_retention, render_health
from verityai.context.ingest import load
from verityai.context.prune import ContextPipeline
from verityai.context.tokenizer import TokenCounter
from verityai.core.models import Constraint, Decision, Discovery, Failure, Task
from verityai.memory.handoff import build_handoff
from verityai.memory.snapshot import SnapshotManager
from verityai.memory.store import MemoryStore

app = typer.Typer(
    name="verity",
    help="Agentic harness: give AI agents the right context, verify what they do.",
    no_args_is_help=True,
    add_completion=False,
)

remember_app = typer.Typer(help="Record task state that must survive a context reset.")
app.add_typer(remember_app, name="remember")


def _require_store() -> MemoryStore:
    """Locate `.verity/`, or exit with an actionable message."""
    store = MemoryStore.discover()
    if store is None:
        typer.secho(
            "No .verity/ directory found. Run `verity init` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    return store


def _read_input(source: Optional[str]) -> str:
    """Read from a file, or from stdin when `source` is '-' or omitted."""
    if source and source != "-":
        path = Path(source)
        if not path.exists():
            typer.secho(f"No such file: {source}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        return path.read_text(encoding="utf-8")

    if sys.stdin.isatty():
        typer.secho(
            "Nothing to read. Pass a file path, or pipe a transcript on stdin.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    return sys.stdin.read()


@app.command()
def init(
    path: Optional[Path] = typer.Argument(None, help="Repository root. Defaults to cwd."),
) -> None:
    """Create the .verity/ state directory."""
    store = MemoryStore.init(path)
    typer.secho(f"Initialized {store.root}", fg=typer.colors.GREEN)
    typer.echo("Add `.verity/` to .gitignore, or commit it to share state with your team.")


@app.command()
def ingest(
    source: Optional[str] = typer.Argument(None, help="Transcript file, or - for stdin."),
    model: Optional[str] = typer.Option(None, "--model", help="Model, for window sizing."),
) -> None:
    """Measure and classify a context without modifying it."""
    from verityai.context.classify import classify_all

    raw = _read_input(source)
    counter = TokenCounter(model=model)
    pipeline = ContextPipeline(counter=counter)

    items = [pipeline.measure(item, i) for i, item in enumerate(load(raw))]
    classified = classify_all(items)

    breakdown = relevance_breakdown(classified)
    total = sum(breakdown.values())

    typer.echo(f"\nContext: {total:,} tokens across {len(classified)} items")
    typer.echo(f"Counted with: {counter.method}\n")

    for bucket, tokens in sorted(breakdown.items(), key=lambda kv: -kv[1]):
        share = tokens / total if total else 0.0
        typer.echo(f"  {bucket:<12} {tokens:>9,}  {share:>6.1%}")

    typer.echo("")
    typer.echo(render_health(compute_health(classified, counter=counter)))


@app.command()
def context(
    source: Optional[str] = typer.Argument(None, help="Transcript file, or - for stdin."),
    budget: Optional[int] = typer.Option(None, "--budget", "-b", help="Target token count."),
    task: str = typer.Option("", "--task", "-t", help="Task description, for ranking."),
    model: Optional[str] = typer.Option(None, "--model", help="Model, for window sizing."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Write pruned context here."),
) -> None:
    """Prune a context toward a budget and report what it cost."""
    raw = _read_input(source)
    counter = TokenCounter(model=model)
    items = load(raw)

    result = ContextPipeline(counter=counter).run(items, task=task, budget=budget)

    typer.echo("\nCONTEXT PIPELINE\n")
    typer.echo(f"  {'stage':<22} {'items':>12} {'tokens':>16} {'saved':>9}")
    for stage in result.stages:
        items_col = f"{stage.items_before} -> {stage.items_after}"
        tokens_col = f"{stage.tokens_before:,} -> {stage.tokens_after:,}"
        typer.echo(f"  {stage.name:<22} {items_col:>12} {tokens_col:>16} {stage.tokens_saved:>9,}")

    typer.echo("")
    typer.echo(f"  Before:  {result.tokens_before:,} tokens")
    typer.echo(f"  After:   {result.tokens_after:,} tokens")
    typer.secho(
        f"  Saved:   {result.tokens_saved:,} tokens ({result.reduction_ratio:.1%})",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  Method:  {result.token_method}")

    retention = critical_retention(items, result.items)
    if retention < 1.0:
        # This is a bug in the budget stage, not a tuning outcome. Say so.
        typer.secho(
            f"\n  BUG: only {retention:.1%} of critical items survived. "
            "Critical items must never be dropped.",
            fg=typer.colors.RED,
        )

    if not result.budget_met:
        typer.secho(
            f"\n  Over budget by {result.tokens_after - (result.budget or 0):,} tokens. "
            "The remaining items are all critical and were not dropped.",
            fg=typer.colors.YELLOW,
        )

    if out:
        out.write_text("\n\n".join(item.content for item in result.items), encoding="utf-8")
        typer.echo(f"\n  Written to {out}")


@app.command()
def health(
    source: Optional[str] = typer.Argument(None, help="Transcript file, or - for stdin."),
    model: Optional[str] = typer.Option(None, "--model", help="Model, for window sizing."),
) -> None:
    """Report multi-dimensional context health."""
    from verityai.context.classify import classify_all

    counter = TokenCounter(model=model)
    pipeline = ContextPipeline(counter=counter)
    items = classify_all([pipeline.measure(i, n) for n, i in enumerate(load(_read_input(source)))])

    store = MemoryStore.discover()
    typer.echo(render_health(compute_health(items, counter=counter)))

    if store is not None:
        typer.echo("\nPERSISTED STATE")
        for key, value in store.summary().items():
            typer.echo(f"  {key:<20} {value:>5}")


@app.command()
def handoff(
    budget: Optional[int] = typer.Option(None, "--budget", "-b", help="Token ceiling."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Write the document here."),
) -> None:
    """Generate a structured handoff document from persisted state."""
    store = _require_store()
    document, report = build_handoff(store, budget=budget)

    if out:
        out.write_text(document, encoding="utf-8")
        typer.secho(f"Written to {out}", fg=typer.colors.GREEN)
    else:
        typer.echo(document)

    typer.echo(f"\n[{report['tokens']:,} tokens, {report['token_method']}]", err=True)
    if report["dropped_sections"]:
        typer.secho(
            f"[dropped to fit budget: {', '.join(report['dropped_sections'])}]",
            fg=typer.colors.YELLOW,
            err=True,
        )


@app.command()
def snapshot(
    label: str = typer.Argument("", help="Optional label for this snapshot."),
) -> None:
    """Capture current task state as a numbered snapshot."""
    manager = SnapshotManager(_require_store())
    snap = manager.create(label=label)
    typer.secho(f"Snapshot {snap.number:03d} created", fg=typer.colors.GREEN)
    if snap.git_sha:
        typer.echo(f"  git: {snap.git_sha[:12]}")


@app.command()
def restore(
    number: int = typer.Argument(..., help="Snapshot number to restore."),
) -> None:
    """Restore context state from a snapshot. Never touches your code."""
    manager = SnapshotManager(_require_store())
    snap = manager.restore(number)
    if snap is None:
        typer.secho(f"No snapshot {number:03d}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    typer.secho(f"Restored snapshot {snap.number:03d}", fg=typer.colors.GREEN)
    if snap.git_sha:
        typer.echo(f"\nThis context was captured at commit {snap.git_sha[:12]}.")
        typer.echo("Verity does not touch your working tree — revert the code yourself if needed.")


@app.command(name="snapshots")
def list_snapshots() -> None:
    """List all snapshots."""
    for snap in SnapshotManager(_require_store()).list():
        label = f"  {snap.label}" if snap.label else ""
        typer.echo(f"  {snap.number:03d}  {snap.created_at:%Y-%m-%d %H:%M}{label}")


@app.command()
def task(
    title: str = typer.Argument(..., help="What you are working on."),
    description: str = typer.Option("", "--description", "-d"),
    next_action: str = typer.Option("", "--next", "-n", help="The immediate next step."),
) -> None:
    """Set the current task."""
    store = _require_store()
    store.set_task(Task(title=title, description=description, next_action=next_action or None))
    typer.secho(f"Task set: {title}", fg=typer.colors.GREEN)


@remember_app.command("decision")
def remember_decision(
    statement: str = typer.Argument(..., help="What was decided."),
    why: str = typer.Option("", "--why", "-w", help="The rationale."),
) -> None:
    """Record a decision."""
    _require_store().append(Decision(statement=statement, rationale=why, source="cli"))
    typer.secho("Decision recorded", fg=typer.colors.GREEN)


@remember_app.command("constraint")
def remember_constraint(
    statement: str = typer.Argument(..., help="The constraint."),
    soft: bool = typer.Option(False, "--soft", help="Advisory rather than hard."),
) -> None:
    """Record a constraint the solution must respect."""
    _require_store().append(Constraint(statement=statement, hard=not soft, source="cli"))
    typer.secho("Constraint recorded", fg=typer.colors.GREEN)


@remember_app.command("discovery")
def remember_discovery(
    statement: str = typer.Argument(..., help="What was learned."),
) -> None:
    """Record something learned about the project."""
    _require_store().append(Discovery(statement=statement, source="cli"))
    typer.secho("Discovery recorded", fg=typer.colors.GREEN)


@remember_app.command("failure")
def remember_failure(
    attempted: str = typer.Argument(..., help="What was tried."),
    error: str = typer.Option("", "--error", "-e", help="How it failed."),
) -> None:
    """Record a dead end, so it is not walked twice."""
    _require_store().append(Failure(attempted=attempted, error=error, source="cli"))
    typer.secho("Failure recorded", fg=typer.colors.GREEN)


@app.command()
def bench(
    paths: list[Path] = typer.Argument(..., help="Transcript files to measure."),
    budget: Optional[int] = typer.Option(None, "--budget", "-b"),
    task: str = typer.Option("", "--task", "-t"),
    model: Optional[str] = typer.Option(None, "--model"),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write the report as JSON."),
) -> None:
    """Run the deterministic (Family A) benchmark over a corpus.

    Reports token savings with no model in the measured path, and refuses to
    call the result publishable when the corpus cannot support a claim. See
    docs/BENCHMARK_PROTOCOL.md.
    """
    from verityai.bench.deterministic import measure_corpus, render_report, to_json

    report = measure_corpus(
        list(paths), task=task, budget=budget, counter=TokenCounter(model=model)
    )

    typer.echo("")
    typer.echo(render_report(report))

    if json_out:
        json_out.write_text(to_json(report), encoding="utf-8")
        typer.echo(f"\n  JSON written to {json_out}")

    if not report.is_publishable:
        # Non-zero exit so this cannot pass silently in CI and have the number
        # scraped out of the log as if it were a result.
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
