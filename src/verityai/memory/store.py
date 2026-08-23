"""Persistent task state on disk, as append-only JSONL under `.verity/`.

Why JSONL files and not the SQLite/SQLAlchemy stack the pre-pivot code used:

- **Append-only is the semantics we actually want.** A decision that gets
  superseded must remain readable, because "the agent re-proposed something we
  already rejected" is only detectable if the rejection is still there. An
  UPDATE-shaped store makes losing that the default.
- **It diffs.** The state lives inside the user's repository, so `git diff`
  shows what the agent learned during a session. That is the single best
  debugging tool this system can offer, and a binary database throws it away.
- **It survives the tool.** A developer can read, grep, and fix `.verity/`
  with an editor. Nothing here should require the harness to be running, or
  even installed, to recover the state it produced.

The cost is no transactions and O(n) reads. Both are acceptable at the scale
this operates on — a long task produces hundreds of records, not millions —
and the moment that stops being true, this module is the only thing that has
to change.

Layout:

    .verity/
    ├── config.toml
    ├── state/{task,decisions,constraints,discoveries,failures}.jsonl
    ├── memory/facts.jsonl
    └── snapshots/NNN/...
"""

import json
import os
from pathlib import Path
from typing import Optional, TypeVar
from uuid import UUID

from pydantic import ValidationError

from verityai.core.atomic import atomic_write_text
from verityai.core.models import (
    Constraint,
    Decision,
    DecisionStatus,
    Discovery,
    Fact,
    Failure,
    ParseReport,
    Record,
    Surfacing,
    Task,
)

VERITY_DIR = ".verity"


class CorruptStateError(Exception):
    """Raised when an operation refuses to build on unreadable state.

    `SnapshotManager.create()` is the one place a read defect would
    otherwise become a permanent write defect (ADR-0037) — everything else
    in this module tolerates a corrupt line and reports it via
    `ParseReport`.
    """


# Bound, not value-constrained: `SnapshotManager.restore` appends records it
# holds as a union of the five types, and a value-constrained TypeVar rejects
# a union outright. `append` only needs `type(record)` to be a key in
# `_FILES`, which every `Record` subclass registered below satisfies.
_T = TypeVar("_T", bound=Record)

# Each record type gets its own file. One combined log would need a
# discriminator field and a dispatch table on read, and would make `git diff`
# on a single category impossible.
_FILES: dict[type, tuple[str, str]] = {
    Decision: ("state", "decisions.jsonl"),
    Constraint: ("state", "constraints.jsonl"),
    Discovery: ("state", "discoveries.jsonl"),
    Failure: ("state", "failures.jsonl"),
    Fact: ("memory", "facts.jsonl"),
    # An observation stream (ADR-0023), not restorable task state -- kept
    # under memory/ alongside facts.jsonl, deliberately absent from
    # Snapshot/SnapshotManager.restore.
    Surfacing: ("memory", "surfacings.jsonl"),
}


class MemoryStore:
    """Reads and writes task state under a `.verity/` directory."""

    def __init__(self, root: Path):
        """Args:
        root: The `.verity` directory itself, not the repository root.
        """
        self.root = Path(root)

    # --- lifecycle -------------------------------------------------------

    @classmethod
    def discover(cls, start: Path | None = None) -> Optional["MemoryStore"]:
        """Find the nearest `.verity/` by walking up from `start`.

        Mirrors how git finds its own directory, so running `verity` from a
        subdirectory of a project works the way anyone would expect.
        """
        current = (Path(start) if start else Path.cwd()).resolve()
        for candidate in [current, *current.parents]:
            verity = candidate / VERITY_DIR
            if verity.is_dir():
                return cls(verity)
        return None

    @classmethod
    def init(cls, repo_root: Path | None = None) -> "MemoryStore":
        """Create `.verity/` and its subdirectories. Idempotent."""
        root = (Path(repo_root) if repo_root else Path.cwd()).resolve() / VERITY_DIR
        (root / "state").mkdir(parents=True, exist_ok=True)
        (root / "memory").mkdir(parents=True, exist_ok=True)
        (root / "snapshots").mkdir(parents=True, exist_ok=True)

        config = root / "config.toml"
        try:
            # O_EXCL makes "create only if absent" one atomic syscall rather
            # than a check-then-write race between two concurrent `init`s.
            fd = os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(
                    "# VerityAI harness configuration\n"
                    '[context]\nmodel = "claude-sonnet-5"\ndefault_budget = 20000\n'
                )
        except FileExistsError:
            pass
        return cls(root)

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    # --- generic record access -------------------------------------------

    def _path_for(self, model: type) -> Path:
        subdir, filename = _FILES[model]
        return self.root / subdir / filename

    def append(self, record: _T) -> _T:
        """Append one record to its log.

        Serialized with `mode="json"` so UUIDs and datetimes become strings
        rather than raising at write time — the same serialization discipline
        the pre-pivot event layer documented after a stray UUID silently
        dropped events.
        """
        path = self._path_for(type(record))
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return record

    def _source_name(self, model: type) -> str:
        subdir, filename = _FILES[model]
        return f"{subdir}/{filename}"

    def read_report(self, model: type) -> tuple[list, ParseReport]:
        """Read every record of one type, oldest first, plus a report of
        what could not be parsed.

        A malformed line is skipped rather than fatal — these files live in
        a user's repo and get hand-edited and merge-conflicted, so one bad
        line must not make the whole history unreadable. But skipping it
        silently would violate invariant 5 ("every degraded path says why")
        and invariant 6 ("parsing never loses input"); `ParseReport` is the
        channel that discharges both. `read()` is this with the report
        dropped, for the many call sites that don't need it.
        """
        source = self._source_name(model)
        path = self._path_for(model)
        if not path.exists():
            return [], ParseReport(source=source, exists=False)

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return [], ParseReport(source=source, exists=True, skipped={0: f"unreadable: {exc}"})

        records = []
        lines_seen = 0
        skipped: dict[int, str] = {}
        for lineno, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            lines_seen += 1
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                skipped[lineno] = f"invalid JSON: {exc.msg} (column {exc.colno})"
                continue
            if not isinstance(parsed, dict):
                skipped[lineno] = "not a JSON object"
                continue
            try:
                records.append(model(**parsed))
            except ValidationError as exc:
                first = exc.errors()[0]
                loc = ".".join(str(p) for p in first["loc"]) or "?"
                skipped[lineno] = f"does not match {model.__name__}: {loc}: {first['msg']}"

        return records, ParseReport(
            source=source, exists=True, lines_seen=lines_seen, parsed=len(records), skipped=skipped
        )

    def read(self, model: type) -> list:
        """Read every record of one type, oldest first. See `read_report()`
        for the corruption channel this drops."""
        return self.read_report(model)[0]

    def integrity(self) -> list[ParseReport]:
        """One `ParseReport` per backing file, including `state/task.json`.

        The single entry point `verity health`, the handoff footer, and the
        MCP surface use to tell a caller their view of `.verity/` is
        incomplete, rather than silently reporting a plausible smaller
        number.
        """
        reports = [self.read_report(model)[1] for model in _FILES]
        reports.append(self.task_report()[1])
        return reports

    # --- typed accessors -------------------------------------------------

    def decisions(self, include_inactive: bool = False) -> list[Decision]:
        """Decisions, newest last.

        Defaults to active only, because that is what belongs in a context.
        The superseded and rejected ones are what the Consistency Engine
        needs (`check_decision_resurfacing`) — hence the flag rather than a
        separate file.

        A decision named by another decision's `supersedes` is reclassified
        here, on read, to `SUPERSEDED` — regardless of the `status` its own
        record was written with. `supersede()` never rewrites the old
        record (append-only), so this is the only place the chain is
        actually followed; without it the old record stays `ACTIVE` forever.
        """
        all_decisions = self.read(Decision)
        superseded_ids = {d.supersedes for d in all_decisions if d.supersedes is not None}
        reclassified = [
            d.model_copy(update={"status": DecisionStatus.SUPERSEDED})
            if d.id in superseded_ids and d.status is DecisionStatus.ACTIVE
            else d
            for d in all_decisions
        ]
        if include_inactive:
            return reclassified
        return [d for d in reclassified if d.status is DecisionStatus.ACTIVE]

    def constraints(self) -> list[Constraint]:
        return self.read(Constraint)

    def discoveries(self) -> list[Discovery]:
        return self.read(Discovery)

    def failures(self, include_resolved: bool = True) -> list[Failure]:
        """Failures, newest last, with `resolve_failure()`'s markers
        applied and hidden.

        A marker record (`resolves is not None`) is metadata about the log,
        not a failure of its own, so it never appears in the returned list
        -- only the original it points at, reclassified to `resolved=True`.
        Without this, `resolve_failure()` would have no visible effect:
        `summary()["failures"]` (unresolved count) would grow forever and
        never shrink, which is exactly the gap found by reading this
        project's own `.verity/state/failures.jsonl` by hand.
        """
        all_failures = self.read(Failure)
        resolved_ids = {f.resolves for f in all_failures if f.resolves is not None}
        originals = [f for f in all_failures if f.resolves is None]
        reclassified = [
            f.model_copy(update={"resolved": True}) if f.id in resolved_ids else f
            for f in originals
        ]
        if include_resolved:
            return reclassified
        return [f for f in reclassified if not f.resolved]

    def resolve_failure(self, failure_id: UUID, note: str = "") -> Failure:
        """Mark an earlier failure resolved, mirroring `supersede()`:
        appends a marker rather than rewriting the original line.
        """
        original = next((f for f in self.read(Failure) if f.id == failure_id), None)
        attempted = original.attempted if original is not None else str(failure_id)
        marker = Failure(attempted=attempted, error=note, resolved=True, resolves=failure_id)
        return self.append(marker)

    def facts(self) -> list[Fact]:
        return self.read(Fact)

    def surfacings(self) -> list[Surfacing]:
        """Every recorded surfacing event, oldest first.

        Read-only from the outside on purpose: nothing besides
        `memory/handoff.py` and `consistency/check.py`'s resurfacing check
        writes here (ADR-0023) -- this accessor is for measurement, not for
        a caller to append through directly.
        """
        return self.read(Surfacing)

    def supersede(self, decision_id: UUID, replacement: Decision) -> Decision:
        """Record `replacement` as superseding an earlier decision.

        Appends rather than rewriting: the old record keeps its `ACTIVE`
        status in the log and is reclassified on read by following the
        `supersedes` chain. Rewriting history in place would defeat the whole
        reason this store is append-only.
        """
        replacement.supersedes = decision_id
        return self.append(replacement)

    # --- task ------------------------------------------------------------

    @property
    def _task_path(self) -> Path:
        return self.root / "state" / "task.json"

    def task_report(self) -> tuple[Task | None, ParseReport]:
        """The current task, plus a report distinguishing "none set" from
        "the record is corrupt" — `task()` collapsed both to `None`."""
        source = "state/task.json"
        path = self._task_path
        if not path.exists():
            return None, ParseReport(source=source, exists=False)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            reason = f"invalid JSON: {exc.msg} (column {exc.colno})"
            return None, ParseReport(source=source, lines_seen=1, skipped={1: reason})
        try:
            return Task(**raw), ParseReport(source=source, lines_seen=1, parsed=1)
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first["loc"]) or "?"
            reason = f"does not match Task: {loc}: {first['msg']}"
            return None, ParseReport(source=source, lines_seen=1, skipped={1: reason})

    def task(self) -> Task | None:
        """The current task, or None if none has been set (or is corrupt --
        see `task_report()` to tell the two apart)."""
        return self.task_report()[0]

    def set_task(self, task: Task) -> Task:
        """Write the current task. The one place this store overwrites.

        Justified because there is exactly one active task and its identity is
        what every other record hangs off; an append-only task log would just
        mean reading the whole file to find the last line.
        """
        atomic_write_text(
            self._task_path,
            json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )
        return task

    # --- reporting -------------------------------------------------------

    def summary(self) -> dict[str, int]:
        """Record counts per category, for `verity health` and the CLI.

        `corrupt_lines`/`corrupt_files` are always present, `0` when clean —
        a field that appears only on failure is a field nobody's code path
        exercises. See `integrity()` for the per-file detail.
        """
        reports = self.integrity()
        return {
            "decisions": len(self.decisions()),
            "decisions_total": len(self.read(Decision)),
            "constraints": len(self.constraints()),
            "discoveries": len(self.discoveries()),
            "failures": len(self.failures(include_resolved=False)),
            "failures_total": len(self.read(Failure)),
            "facts": len(self.facts()),
            "surfacings": len(self.surfacings()),
            "corrupt_lines": sum(len(r.skipped) for r in reports),
            "corrupt_files": sum(1 for r in reports if not r.clean),
        }
