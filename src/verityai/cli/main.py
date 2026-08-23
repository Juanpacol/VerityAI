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

import json
import sys
from pathlib import Path

import typer

from verityai.context.classify import relevance_breakdown
from verityai.context.health import compute_health, critical_retention, render_health
from verityai.context.ingest import load, load_report
from verityai.context.prune import ContextPipeline
from verityai.context.tokenizer import TokenCounter
from verityai.core.atomic import atomic_write_text
from verityai.core.models import Constraint, Decision, Discovery, Failure, Task
from verityai.memory.handoff import build_handoff, render_token_footer
from verityai.memory.snapshot import SnapshotManager
from verityai.memory.store import CorruptStateError, MemoryStore

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


def _read_input(source: str | None) -> str:
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
    path: Path | None = typer.Argument(None, help="Repository root. Defaults to cwd."),
) -> None:
    """Create the .verity/ state directory."""
    store = MemoryStore.init(path)
    typer.secho(f"Initialized {store.root}", fg=typer.colors.GREEN)
    typer.echo("Add `.verity/` to .gitignore, or commit it to share state with your team.")


@app.command()
def ingest(
    source: str | None = typer.Argument(None, help="Transcript file, or - for stdin."),
    model: str | None = typer.Option(None, "--model", help="Model, for window sizing."),
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


def _adaptive_prepass(items, task: str, counter) -> list:
    """Decide whether to surface memory into this context, and say why.

    Returns the items to prepend -- possibly empty. Every branch prints its
    own reasoning: a trigger with the threshold it crossed, a budget with its
    `basis`, each surfaced record with its source id, and the count of what
    did not fit. "Nothing surfaced" and "nothing to surface" are different
    outcomes and are reported differently (invariant 5).
    """
    from verityai.context.adaptive import no_trigger_reason, plan_budget, select, should_surface
    from verityai.context.classify import classify_all
    from verityai.memory.surface import candidates_for

    if not task:
        typer.secho(
            "  --adaptive requires --task: selection ranks candidates against the task, "
            "and with no task every candidate scores zero and drop order collapses to "
            "newest-first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    measured = [ContextPipeline(counter=counter).measure(item, n) for n, item in enumerate(items)]
    health = compute_health(classify_all(measured), counter=counter)

    typer.echo("\nADAPTIVE PRE-PASS\n")
    typer.echo(render_health(health))
    typer.echo("")

    trigger = should_surface(health)
    if trigger is None:
        typer.echo(f"  no trigger     {no_trigger_reason(health)}")
        typer.echo("  nothing surfaced; the pipeline below ran on the transcript alone")
        return []

    store = MemoryStore.discover()
    if store is None:
        typer.secho(
            "  degraded: triggered, but no .verity/ was found -- there is nothing to "
            "surface from. Run `verity init` first.",
            fg=typer.colors.YELLOW,
        )
        return []

    candidates = candidates_for(store, task, counter)
    plan = plan_budget(counter, health)
    decision = select(candidates, task, plan, trigger=trigger)

    typer.echo(f"  trigger        {trigger.reason}")
    typer.echo(f"  candidates     {len(candidates)} records from .verity/state")
    typer.echo(f"  budget         {plan.budget:,} of {plan.window:,} tokens")
    typer.echo(f"  basis          {plan.basis}")
    if decision.degraded_reason:
        typer.secho(f"  degraded       {decision.degraded_reason}", fg=typer.colors.YELLOW)

    surfaced_tokens = sum(item.token_count for item in decision.items)
    typer.echo(
        f"  surfaced       {len(decision.items)} of {len(candidates)} candidates, "
        f"{surfaced_tokens:,} tokens  [{counter.method}]"
    )
    typer.echo("")
    for n, item in enumerate(decision.items, start=1):
        preview = " ".join(item.content.split())[:72]
        typer.echo(f"    {n:>2}. {item.token_count:>5} tok  {preview}")
    withheld = len(candidates) - len(decision.items)
    if withheld:
        typer.echo(f"\n    withheld     {withheld} candidate(s) ranked below the budget cut")

    return decision.items


@app.command()
def context(
    source: str | None = typer.Argument(None, help="Transcript file, or - for stdin."),
    budget: int | None = typer.Option(None, "--budget", "-b", help="Target token count."),
    task: str = typer.Option("", "--task", "-t", help="Task description, for ranking."),
    model: str | None = typer.Option(None, "--model", help="Model, for window sizing."),
    out: Path | None = typer.Option(None, "--out", "-o", help="Write pruned context here."),
    adaptive: bool = typer.Option(
        False,
        "--adaptive",
        help="Before pruning, decide from context health whether to surface memory "
        "records into the context. Requires --task and a .verity/ directory.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="With --adaptive: show what would be surfaced, then stop."
    ),
) -> None:
    """Prune a context toward a budget and report what it cost.

    With `--adaptive`, memory records may be surfaced into the context first.
    That is strictly a pre-pass: the surfaced items are merged into the input
    list and the whole thing goes through the same `ContextPipeline.run`.
    Nothing is injected between stages, because the token ledger chains only
    because `_stage` is its sole writer (ADR-0025, invariant 2).
    """
    raw = _read_input(source)
    counter = TokenCounter(model=model)
    items = load(raw)

    surfaced = _adaptive_prepass(items, task, counter) if adaptive else []
    if adaptive and dry_run:
        raise typer.Exit(0)

    # Merged, then pruned once -- never pruned separately and stitched.
    merged = surfaced + items
    result = ContextPipeline(counter=counter).run(merged, task=task, budget=budget)

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

    # Measured against `merged`, not the original transcript. Surfaced items
    # are ItemKind.MEMORY, which classify.py protects as CRITICAL
    # unconditionally -- comparing against `items` would exclude exactly the
    # items most likely to be dropped, so the check would pass vacuously on
    # the only case worth checking.
    retention = critical_retention(merged, result.items)
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
        atomic_write_text(out, "\n\n".join(item.content for item in result.items))
        typer.echo(f"\n  Written to {out}")


@app.command()
def health(
    source: str | None = typer.Argument(None, help="Transcript file, or - for stdin."),
    model: str | None = typer.Option(None, "--model", help="Model, for window sizing."),
) -> None:
    """Report multi-dimensional context health."""
    from verityai.context.classify import classify_all

    counter = TokenCounter(model=model)
    pipeline = ContextPipeline(counter=counter)
    raw_items, skipped = load_report(_read_input(source))
    items = classify_all([pipeline.measure(i, n) for n, i in enumerate(raw_items)])

    store = MemoryStore.discover()
    typer.echo(render_health(compute_health(items, counter=counter)))

    if skipped:
        total_skipped = sum(skipped.values())
        by_reason = ", ".join(f"{reason} {n}" for reason, n in skipped.items())
        unparseable = skipped.get("unparseable", 0)
        color = typer.colors.YELLOW if unparseable else None
        typer.secho(f"\n  [session: {total_skipped} lines skipped — {by_reason}]", fg=color)

    if store is not None:
        typer.echo("\nPERSISTED STATE")
        for key, value in store.summary().items():
            typer.echo(f"  {key:<20} {value:>5}")

        bad = [r for r in store.integrity() if not r.clean]
        if bad:
            n = sum(len(r.skipped) for r in bad)
            typer.echo()
            typer.secho(
                f"  CORRUPTION — {n} line{'s' if n != 1 else ''} could not be read. "
                "Counts above are incomplete.",
                fg=typer.colors.YELLOW,
            )
            for report in bad:
                typer.secho(f"    {report.note}", fg=typer.colors.YELLOW)
            typer.echo("    Fix or delete the line by hand; .verity/ is plain JSONL on purpose.")


@app.command()
def handoff(
    budget: int | None = typer.Option(None, "--budget", "-b", help="Token ceiling."),
    out: Path | None = typer.Option(None, "--out", "-o", help="Write the document here."),
) -> None:
    """Generate a structured handoff document from persisted state.

    With no `--budget`, this is unabridged -- the same document `verity
    state` returns, under a name a reader looking for "the handoff document"
    will find first. `--budget` trims it in a fixed section order (see
    `memory/handoff.py`), and the report says what was dropped.
    """
    store = _require_store()
    document, report = build_handoff(store, budget=budget)

    if out:
        atomic_write_text(out, document)
        typer.secho(f"Written to {out}", fg=typer.colors.GREEN)
    else:
        typer.echo(document)

    footer = render_token_footer(report)
    if report["dropped_sections"]:
        first, _, rest = footer.partition("\n")
        typer.echo(f"\n{first}", err=True)
        typer.secho(rest, fg=typer.colors.YELLOW, err=True)
    else:
        typer.echo(f"\n{footer}", err=True)


@app.command()
def state() -> None:
    """Retrieve everything recorded about the current task, unabridged.

    Equivalent to `verity handoff` with no `--budget` -- named for whoever is
    looking for "what does Verity currently think is going on" rather than
    "produce a handoff document." This is the CLI counterpart of the MCP
    `session(op="state")` tool: a person must be able to reproduce by hand
    whatever an agent saw there.
    """
    store = _require_store()
    document, report = build_handoff(store)
    typer.echo(document)
    typer.echo(f"\n{render_token_footer(report)}", err=True)


@app.command()
def recall(
    task: str = typer.Option(..., "--task", "-t", help="Task description, for ranking."),
    sample: str | None = typer.Option(
        None, "--sample", "-s", help="Context file to compute a trigger against, or - for stdin."
    ),
) -> None:
    """Ask whether now is the moment to pull saved decisions back in.

    Without `--sample`, lists what is on file with no trigger computed. With
    it, reports the trigger and its threshold, the budget and its basis, and
    the records worth surfacing -- or says plainly that nothing crossed a
    threshold, which is a different answer from "there is nothing saved"
    (invariant 5). This is the CLI counterpart of the MCP `context(op="recall")`
    tool, sharing its rendering (`context.adaptive.describe_recall`) so the
    two surfaces cannot drift on wording.
    """
    from verityai.context.adaptive import describe_recall
    from verityai.memory.surface import candidates_for

    store = _require_store()
    counter = TokenCounter()
    candidates = candidates_for(store, task, counter)
    context_sample = _read_input(sample) if sample else ""
    typer.echo(
        describe_recall(candidates, task, context_sample, counter, see_all_hint="verity state")
    )


@app.command()
def snapshot(
    label: str = typer.Argument("", help="Optional label for this snapshot."),
    force: bool = typer.Option(
        False, "--force", help="Snapshot over corrupt state anyway (not recommended)."
    ),
) -> None:
    """Capture current task state as a numbered snapshot."""
    manager = SnapshotManager(_require_store())
    try:
        snap = manager.create(label=label, force=force)
    except CorruptStateError as exc:
        typer.secho(f"Refused: {exc}", fg=typer.colors.RED, err=True)
        typer.echo("Fix the corrupt line(s) by hand, or pass --force to snapshot anyway.", err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"Snapshot {snap.number:03d} created", fg=typer.colors.GREEN)
    if snap.git_sha:
        typer.echo(f"  git: {snap.git_sha[:12]}")


@app.command()
def restore(
    number: int = typer.Argument(..., help="Snapshot number to restore."),
) -> None:
    """Restore context state from a snapshot. Never touches your code."""
    manager = SnapshotManager(_require_store())
    _, report = manager.get_report(number)
    if not report.exists:
        typer.secho(f"No snapshot {number:03d}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if not report.clean:
        typer.secho(
            f"Snapshot {number:03d} exists but is unreadable: {report.note}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    snap = manager.restore(number)
    assert snap is not None  # report.exists and report.clean already confirmed it
    typer.secho(f"Restored snapshot {snap.number:03d}", fg=typer.colors.GREEN)
    if snap.git_sha:
        typer.echo(f"\nThis context was captured at commit {snap.git_sha[:12]}.")
        typer.echo("Verity does not touch your working tree — revert the code yourself if needed.")


@app.command(name="snapshots")
def list_snapshots() -> None:
    """List all snapshots."""
    manager = SnapshotManager(_require_store())
    for snap in manager.list():
        label = f"  {snap.label}" if snap.label else ""
        typer.echo(f"  {snap.number:03d}  {snap.created_at:%Y-%m-%d %H:%M}{label}")
    for report in manager.integrity():
        typer.secho(f"  ! {report.source}: {report.note}", fg=typer.colors.YELLOW)


@app.command()
def task(
    title: str = typer.Argument(..., help="What you are working on."),
    description: str = typer.Option("", "--description", "-d"),
    next_action: str = typer.Option("", "--next", "-n", help="The immediate next step."),
    # Added after the first real handoff came out with RELEVANT FILES empty
    # and no way to fill it -- found by using the tool on its own development.
    files: list[str] = typer.Option(
        [], "--file", "-f", help="A file this task touches. Repeatable."
    ),
) -> None:
    """Set the current task."""
    store = _require_store()
    store.set_task(
        Task(
            title=title,
            description=description,
            next_action=next_action or None,
            relevant_files=list(files),
        )
    )
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
    budget: int | None = typer.Option(None, "--budget", "-b"),
    task: str = typer.Option("", "--task", "-t"),
    model: str | None = typer.Option(None, "--model"),
    json_out: Path | None = typer.Option(None, "--json", help="Write the report as JSON."),
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
        atomic_write_text(json_out, to_json(report))
        typer.echo(f"\n  JSON written to {json_out}")

    if not report.is_publishable:
        # Non-zero exit so this cannot pass silently in CI and have the number
        # scraped out of the log as if it were a result.
        raise typer.Exit(1)


@app.command(name="noise-floor")
def noise_floor(
    within: Path = typer.Argument(
        ..., help="JSON list of repeat metric dicts, all from ONE configuration."
    ),
    between: Path = typer.Argument(
        ..., help="JSON list of repeat metric dicts from the OTHER configuration."
    ),
    metric: str = typer.Option(..., "--metric", "-m", help="Metric key to compare."),
) -> None:
    """Family B, step by step: is a between-config difference real, or noise?

    Each input file is a JSON array of objects like `{"success": 1.0}` --
    one object per repeat of a fixed task under a fixed configuration. Never
    compares on a single repeat: the whole point of
    docs/BENCHMARK_PROTOCOL.md's procedure is that a noise floor needs
    repeats of the SAME configuration before any cross-configuration
    comparison means anything.
    """
    from verityai.bench.repetition import compare_to_noise_floor, summarize_metric_variance

    within_repeats = json.loads(within.read_text(encoding="utf-8"))
    between_repeats = json.loads(between.read_text(encoding="utf-8"))

    typer.echo(f"\nWITHIN  ({within.name}, {len(within_repeats)} repeat(s))")
    within_summary = summarize_metric_variance(within_repeats)
    if metric in within_summary:
        stats = within_summary[metric]
        typer.echo(
            f"  {metric}: mean={stats['mean']} stdev={stats['stdev']} "
            f"range=[{stats['min']}, {stats['max']}] (n={stats['n']})"
        )

    typer.echo(f"\nBETWEEN ({between.name}, {len(between_repeats)} repeat(s))")
    between_summary = summarize_metric_variance(between_repeats)
    if metric in between_summary:
        stats = between_summary[metric]
        typer.echo(f"  {metric}: mean={stats['mean']} (n={stats['n']})")

    result = compare_to_noise_floor(within_repeats, between_repeats, metric)

    typer.echo("")
    if result["conclusion"] == "insufficient_data":
        typer.secho(f"  insufficient_data: {result['reason']}", fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    typer.echo(f"  noise floor: [{result['noise_floor_min']}, {result['noise_floor_max']}]")
    typer.echo(f"  between-config mean: {result['between_config_mean']}")
    color = typer.colors.GREEN if result["outside_noise_floor"] else typer.colors.YELLOW
    typer.secho(f"  conclusion: {result['conclusion']}", fg=color)


@app.command(name="eval")
def eval_command(
    spec_path: Path = typer.Argument(..., help="JSON-encoded TrialSpec."),
    work_root: Path = typer.Option(
        Path(".verity/eval"),
        "--work-root",
        help="Scratch: where trial directories are created. Git-ignored.",
    ),
    evidence_root: Path | None = typer.Option(
        None,
        "--evidence-root",
        help="Where the retained, re-derivable artifact is written. "
        "Defaults to experiments/<spec name>/evidence, which is tracked.",
    ),
    json_out: Path | None = typer.Option(
        None, "--json", help="Also write the report to this path (report.json is written anyway)."
    ),
) -> None:
    """Run a real, retained Family B trial harness. See docs/BENCHMARK_PROTOCOL.md.

    Not exposed over MCP -- measurement stays human-invoked, matching `bench`
    and `noise-floor`. `spec_path` is a JSON object matching `TrialSpec`
    (name, fixture_path, conditions, n, scorer_command, metric_keys,
    condition_commands).

    Two roots, deliberately separate (ADR-0027):

    - `--work-root` is scratch. Copied fixtures and `__pycache__`, rewritten
      every run, git-ignored.
    - `--evidence-root` is the artifact. Per trial: a `changes.diff` that
      applies onto a fresh fixture copy with `git apply -p1`, the scorer's
      own output, and a `manifest.jsonl` line pinned to the fixture's hash.
      Plus the spec that produced the run and its report.

    An earlier version of this command wrote only to the scratch root and
    called the content hash the retention mechanism. That is what let three
    pilots' numbers outlive their evidence: the ignored copy was the only
    copy. A run given no evidence root still works, and is reported as NOT
    PUBLISHABLE (invariant 7, CLAUDE.md).
    """
    from verityai.bench.eval import render_report, run_eval
    from verityai.bench.eval import to_json as eval_to_json
    from verityai.bench.trial import command_invoker
    from verityai.core.models import TrialSpec

    spec = TrialSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    if evidence_root is None:
        evidence_root = Path("experiments") / spec.name / "evidence"
    report = run_eval(
        spec,
        command_invoker(spec),
        work_root,
        evidence_root=evidence_root,
        # So a hidden scorer can live beside the spec rather than inside the
        # fixture, where the agent under test could read it.
        spec_dir=spec_path.parent,
    )

    typer.echo("")
    typer.echo(render_report(report))

    if json_out:
        atomic_write_text(json_out, json.dumps(eval_to_json(report), indent=2))
        typer.echo(f"\n  JSON written to {json_out}")

    if not report.is_publishable:
        # Non-zero exit for the same reason `bench` does this: a NOT
        # PUBLISHABLE result must not pass silently in CI.
        raise typer.Exit(1)


@app.command(name="verify")
def verify_command(
    evidence_root: Path = typer.Argument(..., help="An experiments/<name>/evidence directory."),
    work_root: Path = typer.Option(
        Path(".verity/verify"), "--work-root", help="Scratch space for the replays."
    ),
) -> None:
    """Re-derive every published number from its retained evidence.

    Invariant 7 as an operation rather than a promise. For each trial in the
    manifest: copy the fixture, apply the retained `changes.diff`, run the
    spec's own scorer, and compare what comes back against what was
    published. Exits non-zero if anything disagrees.

    This is the check a skeptical reader would perform, and until it existed
    the property was only ever demonstrated inside a test, on a fixture the
    test itself had built. Needs `git` (to apply the diffs) and the fixture
    still present at the hash the manifest recorded -- if the fixture has
    drifted, that is reported as drift rather than as a failed check, since
    the diff may be perfectly valid against the base it was made from.
    """
    from verityai.bench.evidence import verify_evidence

    results = verify_evidence(evidence_root, work_root)
    if not results:
        typer.secho(
            f"  No manifest found under {evidence_root}. Nothing to verify.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(2)

    typer.echo(f"\nVERIFY: {evidence_root}\n")
    failed = [r for r in results if not r["ok"]]
    for result in results:
        if result["ok"]:
            metrics = " ".join(f"{k}={v:g}" for k, v in sorted(result["metrics"].items()))
            typer.secho(f"  ok    {result['trial_id']:<14} {metrics}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"  FAIL  {result['trial_id']:<14} {result['reason']}", fg=typer.colors.RED)

    typer.echo("")
    if failed:
        typer.secho(
            f"  {len(failed)} of {len(results)} trial(s) could not be re-derived. "
            "The published numbers for those trials are not currently checkable.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    typer.secho(
        f"  All {len(results)} trial(s) re-derived from the retained artifact "
        "(invariant 7 holds for this run).",
        fg=typer.colors.GREEN,
    )


graph_app = typer.Typer(help="Build and query the code graph.")
app.add_typer(graph_app, name="graph")


def _open_graph():
    """Open the graph belonging to the nearest `.verity/`."""
    from verityai.graph.store import GraphStore

    return GraphStore.for_verity_dir(_require_store().root)


@graph_app.command("build")
def graph_build(
    root: Path | None = typer.Argument(None, help="Repository root. Defaults to cwd."),
    force: bool = typer.Option(False, "--force", help="Re-parse every file."),
) -> None:
    """Ingest the repository into the code graph.

    Incremental by content hash, so re-running after a small change is cheap.
    """
    from verityai.graph.ingest import ingest_repo

    store = _require_store()
    target = Path(root) if root else store.root.parent

    with _open_graph() as graph:
        report = ingest_repo(target, graph, force=force)
        stats = graph.stats()

    typer.secho(f"\n  {report.coverage_note}", fg=typer.colors.GREEN)
    typer.echo(
        f"  {stats['nodes.total']:,} nodes, {stats['edges.total']:,} edges "
        f"in {report.duration_seconds}s"
    )
    typer.echo(
        f"  {stats['edges.unresolved']:,} calls could not be resolved to a definition "
        "(mostly builtins and methods on locals; kept, not discarded)"
    )
    if report.failed:
        typer.secho(f"\n  {report.failed} file(s) failed to parse:", fg=typer.colors.YELLOW)
        for path, reason in report.skipped.items():
            if "syntax error" in reason or "unreadable" in reason:
                typer.echo(f"    {path}: {reason}")


@graph_app.command("stats")
def graph_stats() -> None:
    """Show what is in the graph."""
    with _open_graph() as graph:
        stats = graph.stats()

    if not stats.get("nodes.total"):
        typer.secho("Graph is empty. Run `verity graph build`.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    for key in sorted(stats):
        typer.echo(f"  {key:<22} {stats[key]:>8,}")


@graph_app.command("find")
def graph_find(
    name: str = typer.Argument(..., help="Symbol name to locate."),
) -> None:
    """Find where a symbol is defined.

    Reports every match rather than guessing between them.
    """
    from verityai.graph.query import GraphQuery

    with _open_graph() as graph:
        matches = GraphQuery(graph).define(name)

    if not matches:
        typer.secho(f"No definition of {name!r} in the graph.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    for node in matches:
        typer.echo(f"  {node.kind.value:<9} {node.qualname or node.name}")
        typer.echo(f"            {node.path}:{node.line}")
        if node.signature:
            typer.echo(f"            {node.signature}")


@graph_app.command("context")
def graph_context(
    task_text: str = typer.Argument(..., help="What you are working on."),
    limit: int = typer.Option(15, "--limit", "-n"),
    depth: int = typer.Option(2, "--depth", help="How many hops to expand."),
) -> None:
    """Find code relevant to a task, by relationship as well as by text.

    Seeds lexically, then walks the graph. Code that shares no vocabulary with
    the task still surfaces when an edge connects it to something that does —
    which is the thing text search structurally cannot do.
    """
    from verityai.graph.query import GraphQuery, render_relevant

    with _open_graph() as graph:
        results = GraphQuery(graph).context_for(task_text, limit=limit, max_depth=depth)

    typer.echo("")
    typer.echo(render_relevant(results))


@graph_app.command("deps")
def graph_deps(
    path: str = typer.Argument(..., help="File path, relative to the repo root."),
) -> None:
    """Show what a file imports and what imports it."""
    from verityai.graph.query import GraphQuery

    with _open_graph() as graph:
        deps = GraphQuery(graph).file_dependencies(path)

    typer.echo(f"\n  {path}\n")
    typer.echo("  imports:")
    for name in deps["imports"] or ["    (none)"]:
        typer.echo(f"    {name}")
    typer.echo("\n  imported by:")
    for name in deps["imported_by"] or ["    (none)"]:
        typer.echo(f"    {name}")


@graph_app.command("cycles")
def graph_cycles() -> None:
    """Report circular imports as full paths.

    A graph algorithm, never a model call. Exits non-zero when any cycle
    exists, so this is usable as a CI check.
    """
    from verityai.graph.query import GraphQuery

    with _open_graph() as graph:
        cycles = GraphQuery(graph).import_cycles()

    if not cycles:
        typer.secho("  No circular imports.", fg=typer.colors.GREEN)
        return

    for cycle in cycles:
        typer.secho("  cycle:", fg=typer.colors.RED)
        for node in cycle:
            typer.echo(f"    {node.path or node.qualname}")
    raise typer.Exit(1)


@graph_app.command("untested")
def graph_untested(
    limit: int = typer.Option(30, "--limit", "-n"),
) -> None:
    """List public symbols no test edge reaches. Over-reports, by construction.

    Only sees calls the ingester could resolve, so code exercised indirectly
    shows up here despite being tested. The caveat is printed with the result;
    this is a place to start looking, not a coverage measurement.
    """
    from verityai.graph.query import GraphQuery

    with _open_graph() as graph:
        query = GraphQuery(graph)
        untested = query.untested()
        caveat = query.untested_caveat()

    if not untested:
        typer.secho("  No public symbol lacks a direct test edge.", fg=typer.colors.GREEN)
        return

    typer.echo(f"\n  {len(untested)} public symbols with no direct test edge:\n")
    for node in untested[:limit]:
        typer.echo(f"    {node.kind.value:<9} {node.qualname or node.name}  ({node.path})")
    if len(untested) > limit:
        typer.echo(f"\n    ... and {len(untested) - limit} more")

    typer.secho(f"\n  {caveat}", fg=typer.colors.YELLOW)


@app.command()
def check(
    source: str | None = typer.Argument(None, help="Text file to check, or - for stdin."),
) -> None:
    """Check agent-produced text against the code graph and memory.

    Extracts checkable claims from backtick-quoted spans and relation
    phrases ("`A` calls `B`"), then checks each against the code graph
    (`verity graph build` first) and rejected/superseded decisions in
    `.verity/`. No model is involved -- a claim this cannot extract is
    simply not checked, never guessed at. Exits non-zero on any
    contradiction, so this is usable as a CI or pre-merge check.
    """
    from verityai.consistency.check import render_report, run_consistency_check
    from verityai.graph.query import GraphQuery
    from verityai.graph.store import GraphStore

    text = _read_input(source)
    store = MemoryStore.discover()
    repo_root = store.root.parent if store else Path.cwd()

    graph = GraphStore.for_verity_dir(store.root) if store is not None else None
    try:
        query = (
            GraphQuery(graph) if graph is not None and graph.stats().get("nodes.total") else None
        )
        report = run_consistency_check(text, query=query, store=store, repo_root=repo_root)
    finally:
        if graph is not None:
            graph.close()

    typer.echo("")
    typer.echo(render_report(report))

    if report.contradictions:
        raise typer.Exit(1)


reliability_app = typer.Typer(help="Architecture and security checks over the codebase.")
app.add_typer(reliability_app, name="reliability")


def _changed_python_files(root: Path) -> list[str]:
    """Python files `git diff --name-only HEAD` reports, as git prints them.

    git already emits repo-relative, `./`-free paths -- exactly the form the
    ingester stored -- so these are passed through untouched. Resolving them
    "helpfully" would break every graph lookup silently (ADR-0028).
    """
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        first_line = (result.stderr or "").strip().splitlines()
        typer.secho(
            "  degraded: could not read changed files from git "
            f"({first_line[0] if first_line else 'unknown error'}) -- pass paths explicitly.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(1)
    return [line for line in result.stdout.splitlines() if line.endswith(".py")]


@reliability_app.command("security")
def reliability_security(
    root: Path | None = typer.Argument(None, help="Repository root. Defaults to cwd."),
) -> None:
    """Scan for the built-in security patterns: SQL injection, check-then-act races.

    Deterministic pattern matching over AST facts, not a model and not a
    solver -- narrow by design, see `reliability/security.py`. A hit means
    "worth a human look," not a proof; a miss means "not this exact shape,"
    not "no vulnerabilities." Exits non-zero on any finding.
    """
    from verityai.reliability.report import render_report
    from verityai.reliability.security import caveats_for, scan_repo

    target = Path(root) if root else Path.cwd()
    report = scan_repo(target)

    typer.echo("")
    typer.echo(render_report(report, title="SECURITY", caveats=caveats_for(report.violations)))

    if report.violations:
        raise typer.Exit(1)


@reliability_app.command("risk")
def reliability_risk(
    paths: list[str] = typer.Argument(
        None, help="Changed files, repo-relative as `verity graph build` stored them."
    ),
    changed: bool = typer.Option(
        False, "--changed", help="Tier what `git diff --name-only HEAD` reports instead."
    ),
    show_rules: bool = typer.Option(
        False, "--show-rules", help="Also show which built-in security rules each tier admits."
    ),
) -> None:
    """Tier changed files low/medium/high from graph signals, with reasons.

    Blast radius, fan-in, untested public symbols and path convention -- all
    already in the code graph, so `verity graph build` must have run. A tier
    is a *verification depth*, not a finding: this never exits non-zero on a
    high tier.

    It also does not gate anything yet, deliberately. Both built-in security
    rules are medium/high tier, so `rules_for_tier("low")` admits none of
    them -- a risk-gated scan would check nothing at all on a low-tier file
    while reporting no violations, which is the T6 mistake (a checker that
    cannot fail) shipped on purpose. `--show-rules` prints that hole instead
    of hiding it behind a gate. See ADR-0026.
    """
    from verityai.graph.query import GraphQuery
    from verityai.reliability.risk import classify_paths, rules_for_tier
    from verityai.reliability.security import BUILTIN_SECURITY_RULES

    store = _require_store()
    root = store.root.parent

    targets = _changed_python_files(root) if changed else [p for p in (paths or []) if p]
    if not targets:
        typer.secho(
            "  Nothing to tier. Pass paths, or --changed to read them from git.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(2)

    with _open_graph() as graph:
        stats = graph.stats()
        if not stats.get("nodes.total"):
            # Refusing beats answering: with an empty graph every file tiers
            # `low` for lack of signals, which reads as "nothing needs deep
            # verification" (ADR-0028).
            typer.secho(
                "  degraded: the code graph is empty -- run `verity graph build` first. "
                "Every file would tier 'low' for lack of signals, which would read as a "
                "clean verdict rather than an absent one.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(1)
        verdicts = classify_paths(targets, GraphQuery(graph), repo_root=root)

    typer.echo("\nRISK TIERS\n")
    order = {"high": 0, "medium": 1, "low": 2}
    colors = {"high": typer.colors.RED, "medium": typer.colors.YELLOW, "low": typer.colors.GREEN}
    for path, (tier, reasons) in sorted(verdicts.items(), key=lambda kv: (order[kv[1][0]], kv[0])):
        typer.secho(f"  [{tier.upper():<6}] {path}", fg=colors[tier])
        for reason in reasons:
            typer.echo(f"            {reason}")
        typer.echo("")

    typer.echo(
        f"  {len(verdicts)} file(s) tiered against {stats['nodes.total']:,} nodes / "
        f"{stats['edges.total']:,} edges."
    )

    if show_rules:
        typer.echo("\n  RULES ADMITTED BY TIER")
        total = len(BUILTIN_SECURITY_RULES)
        for tier in ("high", "medium", "low"):
            admitted = rules_for_tier(tier, BUILTIN_SECURITY_RULES)
            names = ", ".join(f"{r.id}[{r.risk_tier}]" for r in admitted) or "-- nothing"
            typer.echo(f"    {tier:<7} {len(admitted)}/{total}   {names}")
        low_count = len(rules_for_tier("low", BUILTIN_SECURITY_RULES))
        coverage = (
            "would currently be checked by no rule at all"
            if low_count == 0
            else f"is currently checked by only {low_count}/{total} rule(s)"
        )
        typer.echo(
            f"\n    A low-tier file {coverage}, which is "
            "why tiers are reported and not yet used to gate scans (ADR-0026)."
        )


@reliability_app.command("architecture")
def reliability_architecture(
    root: Path | None = typer.Argument(None, help="Repository root. Defaults to cwd."),
) -> None:
    """Check that every import respects the declared dependency policy.

    A graph algorithm, not a model call -- see CLAUDE.md's "Dependency rule"
    for the policy this checks and ADR-0008 for why it exists. Exits non-zero
    on any violation, so this is usable as a CI gate against architecture
    drift.
    """
    from verityai.reliability.architecture import check_architecture_at
    from verityai.reliability.report import render_report

    target = Path(root) if root else Path.cwd()
    report = check_architecture_at(target)

    typer.echo("")
    typer.echo(render_report(report, title="ARCHITECTURE"))

    if report.violations:
        raise typer.Exit(1)


hooks_app = typer.Typer(
    help="Claude Code hook integration: automatic capture, not agent-remembered."
)
app.add_typer(hooks_app, name="hooks")


def _read_hook_payload() -> dict:
    """Claude Code sends a hook its JSON payload on stdin."""
    try:
        parsed = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@hooks_app.command("precompact")
def hooks_precompact() -> None:
    """PreCompact hook body: persist CRITICAL transcript items before they degrade.

    Never blocks compaction (always exits 0) -- a capture failure here must
    degrade to "nothing extra was saved," not to a stuck session.
    """
    from verityai.cli.hooks import capture_precompact

    payload = _read_hook_payload()
    result = capture_precompact(payload, root=Path(payload.get("cwd") or Path.cwd()))
    if result["skipped_reason"]:
        typer.echo(f"verity: {result['skipped_reason']}", err=True)
    elif result["snapshot_number"]:
        typer.echo(
            f"verity: captured {result['captured']} item(s), snapshot {result['snapshot_number']:03d}"
        )
    else:
        typer.echo(f"verity: captured {result['captured']} item(s)")


@hooks_app.command("session-start")
def hooks_session_start() -> None:
    """SessionStart hook body: re-inject the handoff after a compaction resume."""
    from verityai.cli.hooks import resume_context

    payload = _read_hook_payload()
    context = resume_context(payload, root=Path(payload.get("cwd") or Path.cwd()))
    if context:
        typer.echo(context)


@hooks_app.command("install")
def hooks_install(
    path: Path | None = typer.Argument(None, help="Repository root. Defaults to cwd."),
) -> None:
    """Register the PreCompact/SessionStart hooks in .claude/settings.json."""
    from verityai.cli.hooks import install

    target = (Path(path) if path else Path.cwd()).resolve()
    written = install(target)
    typer.secho(f"Hooks registered in {written}", fg=typer.colors.GREEN)
    typer.echo(
        "Automatic capture before compaction, and handoff re-injection after, are now active."
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
