"""Claude Code hook integration: automatic capture, not agent-remembered capture.

Every other path into `.verity/` (`verity remember`, the MCP `remember` op)
depends on the agent deciding, mid-session, to write something down. Nothing
enforces that it does — the fresh-agent pilots in `experiments/` show an
agent that never calls `remember` leaves nothing behind, and even an agent
that means to can simply not get to it before a compaction erases whatever
it was about to say. `PreCompact` fires right before that erasure, with the
one thing this module can still reach that the agent's own head cannot
after compaction: `transcript_path`, the full session log. This module reads
it through the same classifier the pruning pipeline uses, and persists what
that classifier calls CRITICAL — independent of whether the agent ever
called `remember` at all.

The counterpart, `resume_context`, answers the other half: a session that
resumes after compaction (`SessionStart` with `source="compact"`) has no
reason to think to call `verity handoff` unless told to. Printing the
handoff as that hook's stdout means it never has to think of it.

Both are read-only with respect to the agent's own history: neither ever
blocks compaction (`exit 2` is available per Claude Code's hook contract but
is deliberately never used here — a capture failure should degrade to
"nothing extra was saved," never to "the user's session cannot proceed").
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from verityai.context.classify import classify_all
from verityai.context.health import compute_health
from verityai.context.ingest_claude_code import is_claude_code_jsonl, parse_jsonl
from verityai.context.prune import ContextPipeline
from verityai.context.tokenizer import TokenCounter
from verityai.core.atomic import atomic_write_text
from verityai.core.models import ContextHealth, Discovery, Relevance
from verityai.memory.handoff import build_handoff
from verityai.memory.snapshot import SnapshotManager
from verityai.memory.store import CorruptStateError, MemoryStore

_HOOK_TAG = "auto-captured"


def _classify_transcript(
    transcript_path: str | None,
) -> tuple[list, TokenCounter | None, str | None]:
    """Read and classify a session transcript, or say why it could not.

    Shared by `capture_precompact` (which only needs the CRITICAL items)
    and `render_statusline` (which needs the full classified set to
    compute health) -- one parse path, so the two can never disagree about
    what a transcript contains.

    Returns `(classified_items, counter, error_reason)`. `counter` is the
    `TokenCounter` used to measure the items, needed downstream by
    `compute_health` to report its counting method; it is `None` exactly
    when `error_reason` is set.
    """
    if not transcript_path:
        return [], None, "no transcript_path in hook payload"

    path = Path(transcript_path)
    if not path.exists():
        return [], None, f"transcript not found: {transcript_path}"

    raw = path.read_text(encoding="utf-8")
    if not is_claude_code_jsonl(raw):
        return [], None, "transcript is not a recognized session format"

    items, _skipped = parse_jsonl(raw)
    counter = TokenCounter()
    pipeline = ContextPipeline(counter=counter)
    measured = [pipeline.measure(item, i) for i, item in enumerate(items)]
    return classify_all(measured), counter, None


def capture_precompact(payload: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Read the transcript this session is about to lose detail from,
    persist its CRITICAL items, and snapshot the result.

    Returns a result dict rather than raising or printing directly, so the
    CLI command and the tests can both inspect exactly what happened
    without parsing stdout.
    """
    store = MemoryStore.discover(root)
    if store is None:
        return {
            "skipped_reason": "no .verity/ found",
            "captured": 0,
            "snapshot_number": None,
            "snapshot_path": None,
        }

    classified, _counter, error = _classify_transcript(payload.get("transcript_path"))
    if error:
        return {
            "skipped_reason": error,
            "captured": 0,
            "snapshot_number": None,
            "snapshot_path": None,
        }

    critical = [i for i in classified if i.relevance is Relevance.CRITICAL]

    already = {d.statement for d in store.discoveries()}
    captured = 0
    for item in critical:
        statement = item.content.strip()
        if not statement or statement in already:
            continue
        store.append(Discovery(statement=statement, source="hook:precompact", tags=[_HOOK_TAG]))
        already.add(statement)
        captured += 1

    snapshot_number = None
    snapshot_path = None
    manager = SnapshotManager(store)
    try:
        snapshot = manager.create(label="auto: pre-compact")
        snapshot_number = snapshot.number
        snapshot_path = str(manager.path_for(snapshot.number))
    except CorruptStateError:
        # Corrupt state already has its own loud channel (verity health).
        # A capture hook is not the place to surface it a second time --
        # the newly-captured discoveries above are still real and kept.
        pass

    return {
        "skipped_reason": None,
        "captured": captured,
        "snapshot_number": snapshot_number,
        "snapshot_path": snapshot_path,
    }


def resume_context(payload: dict[str, Any], root: Path | None = None) -> str | None:
    """The text to print as `SessionStart` stdout when this session just
    resumed from a compaction, or `None` if there is nothing to add.

    Only acts when `source == "compact"` -- a normal `startup`/`resume`
    session has its full history already and printing a handoff on every
    session start would be noise, not recovery.
    """
    if payload.get("source") != "compact":
        return None

    store = MemoryStore.discover(root)
    if store is None:
        return None

    document, _report = build_handoff(store)
    return (
        "# VERITYAI: this session was just compacted\n\n"
        "The context above was auto-summarized. Before assuming anything is "
        "missing, here is the persisted state VerityAI captured just before "
        "compaction happened:\n\n" + document
    )


_YELLOW = "\033[33m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_DIM = "\033[2m"
_RESET = "\033[0m"

# Editorial, not empirical -- same disclaimer ContextHealth.score's own
# docstring already carries for its weights. Chosen to make "critical" mean
# something has actually gone wrong (corruption, lost critical context),
# "degraded" mean attention is warranted soon, "healthy" mean neither.
_DEGRADED_WINDOW_USAGE = 0.85
_DEGRADED_REDUNDANCY = 0.25
_STALE_SNAPSHOT_DAYS = 7

_STATUS_COLOR = {"critical": _RED, "degraded": _YELLOW, "healthy": _GREEN}
_STATUS_DOT = {"critical": "●", "degraded": "●", "healthy": "●"}


def _age(delta_seconds: float) -> str:
    if delta_seconds < 60:
        return "just now"
    if delta_seconds < 3600:
        return f"{int(delta_seconds // 60)}m ago"
    if delta_seconds < 86400:
        return f"{int(delta_seconds // 3600)}h ago"
    return f"{int(delta_seconds // 86400)}d ago"


def latest_snapshot_age_days(store: MemoryStore) -> float | None:
    snapshots = SnapshotManager(store).list()
    if not snapshots:
        return None
    latest = snapshots[-1]
    return (datetime.now(timezone.utc) - latest.created_at).total_seconds() / 86400


def verdict(
    health: ContextHealth | None,
    summary: dict[str, int],
    snapshot_age_days: float | None,
) -> tuple[str, list[tuple[str, str]]]:
    """One word -- `healthy`, `degraded`, or `critical` -- plus
    `(reason, action)` pairs: what's wrong, and the specific `verity`
    command that addresses it. A verdict a developer can't act on is just
    an number dressed up as a word, which is the same complaint that made
    the raw-counts statusline (ADR-0040) not useful in the first place.

    The single source of truth for both the status line's dot and `verity
    status`'s expanded view, so the two can never disagree about whether
    something is wrong.

    Thresholds are a stated editorial judgement (same disclaimer
    `ContextHealth.score` already carries), not a measured cutoff: critical
    means something has demonstrably gone wrong; degraded means attention
    is warranted soon, not that anything broke.

    Deliberately does not fold in `ContextHealth.contradiction_count` --
    nothing in this codebase currently computes a real value for it (see
    ADR-0041), and displaying a bare `0` next to genuine signals would
    imply "checked, none found" for a dimension that was never checked.

    Also deliberately does not use `ContextHealth.critical_retained` as a
    trigger: `compute_health()` hardcodes it to `1.0` whenever called on an
    unpruned transcript (its own docstring says so -- "nothing has been
    pruned yet at measurement time"), which is every call this module
    makes. Treating it as a live signal here would repeat the exact
    always-passing-checker mistake this project's own T6 finding warns
    about, just less visibly than an unwired zero -- it shipped briefly in
    ADR-0042's first version and was found and removed in ADR-0043.
    """
    reasons: list[tuple[str, str]] = []

    if summary["corrupt_lines"]:
        reasons.append(
            (
                f"{summary['corrupt_lines']} corrupt line(s) in .verity/",
                "run `verity health` to see which file/line, then fix or delete it by hand",
            )
        )
    if reasons:
        return "critical", reasons

    if health is not None and health.window_usage >= _DEGRADED_WINDOW_USAGE:
        reasons.append(
            (
                f"context window {health.window_usage:.0%} full",
                "run `verity context <transcript> --budget N --task '...'` to prune now, "
                "or let the PreCompact hook auto-capture before the next compaction",
            )
        )
    if health is not None and health.redundancy >= _DEGRADED_REDUNDANCY:
        reasons.append(
            (
                f"{health.redundancy:.0%} of context is redundant/obsolete",
                "run `verity context <transcript> --budget N --task '...'` to prune it out",
            )
        )
    if snapshot_age_days is not None and snapshot_age_days >= _STALE_SNAPSHOT_DAYS:
        reasons.append(
            (
                f"latest snapshot is {int(snapshot_age_days)}d old",
                "run `verity snapshot` to capture current state",
            )
        )
    if reasons:
        return "degraded", reasons

    return "healthy", []


def render_statusline(payload: dict[str, Any], root: Path | None = None) -> str | None:
    """One compact line: is Verity healthy, and is the agent still working
    with good context -- not a dump of every internal metric.

    `verity ● healthy | ctx 63% | 4 crit | 12D 10F | 0⚠`

    A non-healthy verdict appends `-> verity status` -- detecting a problem
    without pointing at how to address it just moves the "what do I do
    with this number" question from the counts (ADR-0040's complaint) to
    the word. `verity status` prints each reason paired with the specific
    command that addresses it (`verdict()`'s `action` half).

    Returns `None` only when there is no `.verity/` at all, so the
    installed statusline command degrades to silence rather than clutter
    in a project that never ran `verity init`.
    """
    cwd = payload.get("workspace", {}).get("current_dir") or payload.get("cwd")
    store = MemoryStore.discover(Path(cwd) if cwd else root)
    if store is None:
        return None

    summary = store.summary()
    classified, counter, error = _classify_transcript(payload.get("transcript_path"))
    health = compute_health(classified, counter=counter) if not error and classified else None
    snapshot_age = latest_snapshot_age_days(store)
    status, _reasons = verdict(health, summary, snapshot_age)

    color = _STATUS_COLOR[status]
    segments = [f"{color}{_STATUS_DOT[status]} {status}{_RESET}"]

    window = payload.get("context_window") or {}
    used_pct = window.get("used_percentage")
    if used_pct is not None:
        segments.append(f"ctx {used_pct:.0f}%")
    if classified:
        critical_now = sum(1 for i in classified if i.relevance is Relevance.CRITICAL)
        segments.append(f"{critical_now} crit")

    segments.append(f"{summary['decisions']}D {summary['failures']}F")

    alerts = summary["corrupt_lines"] + (
        1 if snapshot_age is not None and snapshot_age >= _STALE_SNAPSHOT_DAYS else 0
    )
    alert_color = _YELLOW if alerts else ""
    alert_reset = _RESET if alerts else ""
    segments.append(f"{alert_color}{alerts}⚠{alert_reset}")

    line = f"{_DIM}verity{_RESET} " + f" {_DIM}|{_RESET} ".join(segments)
    if status != "healthy":
        line += f"  {_DIM}-> verity status{_RESET}"
    return line


def install(project_root: Path) -> Path:
    """Merge PreCompact/SessionStart hook entries into
    `.claude/settings.json`, preserving whatever is already there.

    A blind overwrite would be the kind of destructive default this
    project's own CLAUDE.md warns against; this reads the existing file
    (if any), adds only the two hook arrays this module needs, and leaves
    every other key -- permissions, other hooks, anything -- untouched.
    """
    settings_path = project_root / ".claude" / "settings.json"
    settings: dict[str, Any] = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))

    hooks = settings.setdefault("hooks", {})

    precompact_entry = {
        "matcher": "manual|auto",
        "hooks": [{"type": "command", "command": "verity hooks precompact", "timeout": 30}],
    }
    session_start_entry = {
        "matcher": "compact",
        "hooks": [{"type": "command", "command": "verity hooks session-start", "timeout": 30}],
    }

    for event, entry, command_suffix in (
        ("PreCompact", precompact_entry, "hooks precompact"),
        ("SessionStart", session_start_entry, "hooks session-start"),
    ):
        existing = hooks.setdefault(event, [])
        if not any(
            command_suffix in h.get("command", "")
            for group in existing
            for h in group.get("hooks", [])
        ):
            existing.append(entry)

    atomic_write_text(settings_path, json.dumps(settings, indent=2) + "\n")
    return settings_path


def install_statusline(project_root: Path) -> tuple[Path, bool]:
    """Set `statusLine` in `.claude/settings.json` to `verity hooks
    statusline`, unless one is already configured.

    Returns `(settings_path, installed)`. Never overwrites an existing
    `statusLine` -- unlike the two hook arrays in `install()`, this is a
    single slot a user may already have pointed at their own script (git
    branch, a cost tracker, ...), and clobbering it silently would cost
    them something `verity` has no way to know the value of.
    """
    settings_path = project_root / ".claude" / "settings.json"
    settings: dict[str, Any] = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))

    if "statusLine" in settings:
        return settings_path, False

    settings["statusLine"] = {"type": "command", "command": "verity hooks statusline"}
    atomic_write_text(settings_path, json.dumps(settings, indent=2) + "\n")
    return settings_path, True
