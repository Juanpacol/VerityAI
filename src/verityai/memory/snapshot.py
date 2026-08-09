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

import json
import subprocess
from pathlib import Path
from typing import Optional

from verityai.core.models import (
    Constraint,
    Decision,
    Discovery,
    Fact,
    Failure,
    Snapshot,
)
from verityai.memory.store import MemoryStore


def _git_sha(repo_root: Path) -> Optional[str]:
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

    def create(self, label: str = "") -> Snapshot:
        """Capture current state as a new numbered snapshot."""
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
        (target / "snapshot.json").write_text(
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return snapshot

    def get(self, number: int) -> Optional[Snapshot]:
        path = self._dir_for(number) / "snapshot.json"
        if not path.exists():
            return None
        try:
            return Snapshot(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list(self) -> list[Snapshot]:
        """All snapshots, oldest first. Unreadable ones are skipped."""
        if not self.snapshots_dir.is_dir():
            return []
        snapshots = []
        for entry in sorted(self.snapshots_dir.iterdir()):
            if entry.is_dir() and entry.name.isdigit():
                snapshot = self.get(int(entry.name))
                if snapshot is not None:
                    snapshots.append(snapshot)
        return snapshots

    def restore(self, number: int) -> Optional[Snapshot]:
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
