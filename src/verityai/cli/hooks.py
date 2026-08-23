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
from pathlib import Path
from typing import Any

from verityai.context.classify import classify_all
from verityai.context.ingest_claude_code import is_claude_code_jsonl, parse_jsonl
from verityai.core.atomic import atomic_write_text
from verityai.core.models import Discovery, Relevance
from verityai.memory.handoff import build_handoff
from verityai.memory.snapshot import SnapshotManager
from verityai.memory.store import CorruptStateError, MemoryStore

_HOOK_TAG = "auto-captured"


def capture_precompact(payload: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Read the transcript this session is about to lose detail from,
    persist its CRITICAL items, and snapshot the result.

    Returns a result dict rather than raising or printing directly, so the
    CLI command and the tests can both inspect exactly what happened
    without parsing stdout.
    """
    store = MemoryStore.discover(root)
    if store is None:
        return {"skipped_reason": "no .verity/ found", "captured": 0, "snapshot_number": None}

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return {
            "skipped_reason": "no transcript_path in hook payload",
            "captured": 0,
            "snapshot_number": None,
        }

    path = Path(transcript_path)
    if not path.exists():
        return {
            "skipped_reason": f"transcript not found: {transcript_path}",
            "captured": 0,
            "snapshot_number": None,
        }

    raw = path.read_text(encoding="utf-8")
    if not is_claude_code_jsonl(raw):
        return {
            "skipped_reason": "transcript is not a recognized session format",
            "captured": 0,
            "snapshot_number": None,
        }

    items, _skipped = parse_jsonl(raw)
    classified = classify_all(items)
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
    try:
        snapshot = SnapshotManager(store).create(label="auto: pre-compact")
        snapshot_number = snapshot.number
    except CorruptStateError:
        # Corrupt state already has its own loud channel (verity health).
        # A capture hook is not the place to surface it a second time --
        # the newly-captured discoveries above are still real and kept.
        pass

    return {"skipped_reason": None, "captured": captured, "snapshot_number": snapshot_number}


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
_DIM = "\033[2m"
_RESET = "\033[0m"


def _age(delta_seconds: float) -> str:
    if delta_seconds < 60:
        return "just now"
    if delta_seconds < 3600:
        return f"{int(delta_seconds // 60)}m ago"
    if delta_seconds < 86400:
        return f"{int(delta_seconds // 3600)}h ago"
    return f"{int(delta_seconds // 86400)}d ago"


def render_statusline(payload: dict[str, Any], root: Path | None = None) -> str | None:
    """One line summarizing `.verity/` state, for Claude Code's status line.

    Returns `None` when there is nothing to show (no `.verity/`) so the
    installed statusline command degrades to silence rather than clutter in
    a project that never ran `verity init`.
    """
    from datetime import datetime, timezone

    cwd = payload.get("workspace", {}).get("current_dir") or payload.get("cwd")
    store = MemoryStore.discover(Path(cwd) if cwd else root)
    if store is None:
        return None

    summary = store.summary()
    parts = [
        f"{summary['decisions']} dec",
        f"{summary['discoveries']} disc",
        f"{summary['facts']} fact",
    ]
    if summary["failures"]:
        parts.append(f"{summary['failures']} fail")

    snapshots = SnapshotManager(store).list()
    if snapshots:
        latest = snapshots[-1]
        age = (datetime.now(timezone.utc) - latest.created_at).total_seconds()
        snap_text = f"snap {latest.number:03d} ({_age(age)})"
    else:
        snap_text = "no snapshots"

    line = f"verity: {' '.join(parts)} | {snap_text}"

    if summary["corrupt_lines"]:
        line += f" | {_YELLOW}⚠ {summary['corrupt_lines']} corrupt{_RESET}"

    return f"{_DIM}{line}{_RESET}"


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
