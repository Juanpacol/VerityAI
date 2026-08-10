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
from pathlib import Path
from typing import Optional, TypeVar
from uuid import UUID

from verityai.core.models import (
    Constraint,
    Decision,
    DecisionStatus,
    Discovery,
    Fact,
    Failure,
    Record,
    Task,
)

VERITY_DIR = ".verity"

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
        if not config.exists():
            config.write_text(
                "# VerityAI harness configuration\n"
                '[context]\nmodel = "claude-sonnet-5"\ndefault_budget = 20000\n',
                encoding="utf-8",
            )
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

    def read(self, model: type) -> list:
        """Read every record of one type, oldest first.

        A malformed line is skipped rather than fatal. These files live in a
        user's repo and get hand-edited and merge-conflicted; one bad line
        must not make the whole history unreadable.
        """
        path = self._path_for(model)
        if not path.exists():
            return []

        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(model(**json.loads(line)))
            except Exception:
                continue
        return records

    # --- typed accessors -------------------------------------------------

    def decisions(self, include_inactive: bool = False) -> list[Decision]:
        """Decisions, newest last.

        Defaults to active only, because that is what belongs in a context.
        The superseded and rejected ones are what the Consistency Engine will
        need in Phase 3 — hence the flag rather than a separate file.
        """
        all_decisions = self.read(Decision)
        if include_inactive:
            return all_decisions
        return [d for d in all_decisions if d.status is DecisionStatus.ACTIVE]

    def constraints(self) -> list[Constraint]:
        return self.read(Constraint)

    def discoveries(self) -> list[Discovery]:
        return self.read(Discovery)

    def failures(self, include_resolved: bool = True) -> list[Failure]:
        failures = self.read(Failure)
        if include_resolved:
            return failures
        return [f for f in failures if not f.resolved]

    def facts(self) -> list[Fact]:
        return self.read(Fact)

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

    def task(self) -> Task | None:
        """The current task, or None if none has been set."""
        path = self._task_path
        if not path.exists():
            return None
        try:
            return Task(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def set_task(self, task: Task) -> Task:
        """Write the current task. The one place this store overwrites.

        Justified because there is exactly one active task and its identity is
        what every other record hangs off; an append-only task log would just
        mean reading the whole file to find the last line.
        """
        path = self._task_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return task

    # --- reporting -------------------------------------------------------

    def summary(self) -> dict[str, int]:
        """Record counts per category, for `verity health` and the CLI."""
        return {
            "decisions": len(self.decisions()),
            "decisions_total": len(self.read(Decision)),
            "constraints": len(self.constraints()),
            "discoveries": len(self.discoveries()),
            "failures": len(self.failures(include_resolved=False)),
            "failures_total": len(self.read(Failure)),
            "facts": len(self.facts()),
        }
