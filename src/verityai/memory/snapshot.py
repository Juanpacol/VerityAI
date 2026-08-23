"""Numbered, restorable captures of task state.

Scope is deliberately narrow: **context only, never code.** The harness may
tell you "restore snapshot 14 and revert commit abc123", but it does not touch
your working tree. Git already does code rollback correctly, and a tool that
silently reverted a developer's files would be unusable at any level of
reliability short of perfect.

Restoring is itself append-only. `restore()` does not delete the records
written since the snapshot — it writes the snapshot's contents forward as new
records, so the act of restoring is visible in the log. A restore that erased
history would destroy the evidence needed to understand why it was necessary.
"""

from __future__ import annotations

import builtins
import json
import subprocess
from pathlib import Path

from pydantic import ValidationError

from verityai.core.atomic import atomic_write_text
from verityai.core.models import (
    Constraint,
    Decision,
    Discovery,
    Fact,
    Failure,
    ParseReport,
    Snapshot,
)
from verityai.memory.store import CorruptStateError, MemoryStore


def _git_sha(repo_root: Path) -> str | None:
    """Current HEAD sha, or None outside a repo.

    Recorded so a restored context can be paired with the code state it was
    captured against. Failure is expected and non-fatal — `.verity/` is useful
    outside a git repository too.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


class SnapshotManager:
    """Creates, lists, and restores snapshots under `.verity/snapshots/`."""

    def __init__(self, store: MemoryStore):
        self.store = store
        self.snapshots_dir = store.root / "snapshots"

    def _dir_for(self, number: int) -> Path:
        # Zero-padded so shell glob order matches numeric order — `010` before
        # `9` is the kind of thing that only bites once you have ten snapshots.
        return self.snapshots_dir / f"{number:03d}"

    def path_for(self, number: int) -> Path:
        """Where snapshot `number`'s file lives, whether or not it exists
        yet -- the thing a caller actually wants to tell a user, since
        "snapshot 003 created" without the path forces a `find` to answer
        the next question."""
        return self._dir_for(number) / "snapshot.json"

    def next_number(self) -> int:
        """One past the highest existing snapshot number.

        Derived from the directory rather than a counter file: a counter can
        drift out of sync with reality after a manual delete, and the
        directory is the reality.
        """
        if not self.snapshots_dir.is_dir():
            return 1
        numbers = [
            int(entry.name)
            for entry in self.snapshots_dir.iterdir()
            if entry.is_dir() and entry.name.isdigit()
        ]
        return max(numbers, default=0) + 1

    def create(self, label: str = "", force: bool = False) -> Snapshot:
        """Capture current state as a new numbered snapshot.

        Refuses on corrupt source state unless `force=True` (ADR-0037): a
        snapshot built from a truncated line would write a shortened
        history forward as a *clean-looking* artifact, and `restore()`
        would later re-append that shortened history as new records —
        turning a read defect into a permanent write defect. Every other
        reader in this module tolerates corruption and reports it; this is
        the one write path that must refuse instead.
        """
        if not force:
            bad = [r for r in self.store.integrity() if not r.clean]
            if bad:
                raise CorruptStateError(
                    "refusing to snapshot over corrupt state: " + "; ".join(r.note for r in bad)
                )

        snapshot = Snapshot(
            number=self.next_number(),
            label=label,
            task=self.store.task(),
            decisions=self.store.decisions(),
            constraints=self.store.constraints(),
            discoveries=self.store.discoveries(),
            failures=self.store.failures(),
            facts=self.store.facts(),
            git_sha=_git_sha(self.store.root.parent),
        )

        target = self._dir_for(snapshot.number)
        target.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            target / "snapshot.json",
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )
        return snapshot

    def get_report(self, number: int) -> tuple[Snapshot | None, ParseReport]:
        """A snapshot, plus a report distinguishing "never existed" from
        "exists but is unreadable" — `get()` collapsed both to `None`."""
        source = f"snapshots/{number:03d}/snapshot.json"
        path = self._dir_for(number) / "snapshot.json"
        if not path.exists():
            return None, ParseReport(source=source, exists=False)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            reason = f"invalid JSON: {exc.msg} (column {exc.colno})"
            return None, ParseReport(source=source, lines_seen=1, skipped={1: reason})
        try:
            return Snapshot(**raw), ParseReport(source=source, lines_seen=1, parsed=1)
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first["loc"]) or "?"
            reason = f"does not match Snapshot: {loc}: {first['msg']}"
            return None, ParseReport(source=source, lines_seen=1, skipped={1: reason})

    def get(self, number: int) -> Snapshot | None:
        """A snapshot, or None if missing or corrupt — see `get_report()`
        to tell the two apart."""
        return self.get_report(number)[0]

    def _numbers(self) -> list[int]:
        if not self.snapshots_dir.is_dir():
            return []
        return sorted(
            int(entry.name)
            for entry in self.snapshots_dir.iterdir()
            if entry.is_dir() and entry.name.isdigit()
        )

    def list(self) -> list[Snapshot]:
        """All readable snapshots, oldest first. Unreadable ones are
        skipped here — see `integrity()` to learn about them."""
        return [s for n in self._numbers() if (s := self.get(n)) is not None]

    def integrity(self) -> builtins.list[ParseReport]:
        """One `ParseReport` per snapshot directory that failed to parse.

        Return type spelled `builtins.list` because this class already
        defines a method named `list`, which shadows the builtin for name
        resolution in the rest of this class body -- both at runtime and
        for mypy, `from __future__ import annotations` notwithstanding.
        """
        return [r for n in self._numbers() if not (r := self.get_report(n)[1]).clean]

    def restore(self, number: int) -> Snapshot | None:
        """Re-apply a snapshot's state by appending it forward.

        Records already present (matched by id) are not duplicated, so
        restoring twice is idempotent and restoring a snapshot that is already
        current is a no-op rather than a doubling.

        Returns the snapshot, or None if it does not exist.
        """
        snapshot = self.get(number)
        if snapshot is None:
            return None

        if snapshot.task is not None:
            self.store.set_task(snapshot.task)

        for model, records in (
            (Decision, snapshot.decisions),
            (Constraint, snapshot.constraints),
            (Discovery, snapshot.discoveries),
            (Failure, snapshot.failures),
            (Fact, snapshot.facts),
        ):
            existing = {record.id for record in self.store.read(model)}
            for record in records:
                if record.id not in existing:
                    self.store.append(record)

        return snapshot
