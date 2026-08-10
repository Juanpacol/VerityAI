"""Run a `TrialSpec` for real, and retain what it produced.

This module exists because of an audited failure (ADR-0021's sibling
finding, Phase 0 truth repair): every "N trials per condition" measurement
in this project's history before `verity eval` was hand-run and hand-scored
-- `bench/repetition.py` (the statistics) has always been solid, but nothing
fed it real, retained data. Three pilots' published numbers were destroyed
when a later re-run of their own setup script wiped the only copy of the
post-trial code, because nothing under `trials/` was ever git-tracked. This
module is the fix: every `TrialRecord` carries a content hash of the
post-trial tree, so "this result is still checkable" is a property of the
record, not a hope about the filesystem.

No model call happens in this file. `invoke_agent` is injected (CLAUDE.md
rule 3: "make the model injectable so tests pass a lambda") -- it may be a
real agent invocation, or, like `experiments/lib/setup_phase_a.sh`'s
deliberately fabricated phase-A state, a scripted stand-in. Scoring is
always a real subprocess (`pytest`, a hidden-test module), never the
agent's own report -- the same discipline `docs/MEASUREMENTS.md` states for
every prior pilot.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from verityai.core.models import FailureMode, TrialRecord, TrialSpec

# Directories that are execution artifacts, not trial content -- hashing
# them would make two runs of an identical trial hash differently for
# reasons that have nothing to do with what the trial actually produced.
_HASH_EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".verity"}


def _hash_tree(root: Path) -> str:
    """A deterministic content hash of every file under `root`.

    Sorted paths and streamed content, not `os.walk`'s arbitrary order --
    two directory trees with identical content must hash identically
    regardless of filesystem iteration order.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _HASH_EXCLUDE_DIRS for part in path.relative_to(root).parts):
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def run_trials(
    spec: TrialSpec,
    invoke_agent: Callable[[Path, str, int], None],
    work_root: Path,
    metric_fn: Callable[[Path, int], dict[str, float]] | None = None,
    classify_failure: Callable[[Path, int], FailureMode | None] | None = None,
) -> list[TrialRecord]:
    """Run every (condition, trial) pair in `spec` and retain the result.

    For each of `spec.n` trials per condition:
      1. Copy `spec.fixture_path` into a fresh trial directory under
         `work_root` (never mutate the fixture itself).
      2. Call `invoke_agent(trial_dir, condition, index)` to produce whatever
         that condition means -- a live agent call, or a scripted stand-in.
         Its return value is ignored; its effect is whatever it left in
         `trial_dir`.
      3. Run `spec.scorer_command` inside `trial_dir` and record its exit
         code as ground truth -- never the agent's own account of success.
      4. Hash the resulting tree so the record can be checked against the
         directory later, or flagged if the directory no longer matches.

    `metric_fn(trial_dir, exit_code) -> dict` lets a caller extract metrics
    beyond bare pass/fail (e.g. pilot 7/8's hidden-test boundary/tie check);
    the default metric is `{"success": 1.0 if exit_code == 0 else 0.0}`.
    """
    work_root.mkdir(parents=True, exist_ok=True)
    fixture = Path(spec.fixture_path)
    records: list[TrialRecord] = []

    for condition in spec.conditions:
        for index in range(1, spec.n + 1):
            trial_id = f"{condition}_{index}"
            trial_dir = work_root / trial_id
            if trial_dir.exists():
                shutil.rmtree(trial_dir)
            shutil.copytree(fixture, trial_dir)

            invoke_agent(trial_dir, condition, index)

            result = subprocess.run(
                spec.scorer_command,
                shell=True,
                cwd=trial_dir,
                capture_output=True,
                text=True,
            )
            exit_code = result.returncode

            metrics = (
                metric_fn(trial_dir, exit_code)
                if metric_fn is not None
                else {"success": 1.0 if exit_code == 0 else 0.0}
            )

            transcript_path = trial_dir / "transcript.txt"

            records.append(
                TrialRecord(
                    trial_id=trial_id,
                    condition=condition,
                    scorer_exit_code=exit_code,
                    metrics=metrics,
                    transcript_path=str(transcript_path) if transcript_path.exists() else None,
                    artifact_hash=_hash_tree(trial_dir),
                    failure_mode=(
                        classify_failure(trial_dir, exit_code)
                        if classify_failure is not None
                        else None
                    ),
                )
            )

    return records


def command_invoker(spec: TrialSpec) -> Callable[[Path, str, int], None]:
    """Build an `invoke_agent` from `spec.condition_commands`.

    For the CLI, which has no way to launch a live agent itself: each
    condition's command runs as a shell command inside the trial directory,
    exactly the shape `experiments/lib/setup_phase_a.sh` used by hand for
    its `verity` condition. A condition missing from `condition_commands`
    is a no-op -- e.g. a `naive` baseline that is just the unmodified
    fixture, nothing to run.
    """

    def invoke(trial_dir: Path, condition: str, index: int) -> None:
        command = spec.condition_commands.get(condition)
        if command is None:
            return
        subprocess.run(command, shell=True, cwd=trial_dir, check=False)

    return invoke


def metrics_by_condition(records: list[TrialRecord]) -> dict[str, list[dict[str, float]]]:
    """Group trial metrics by condition, in the shape `repetition.py` expects.

    `bench/repetition.py` takes `list[dict[str, float]]` per configuration
    (ADR-0010) -- this is the one place that shape gets assembled from real
    trial records rather than a hand-typed JSON file.
    """
    grouped: dict[str, list[dict[str, float]]] = {}
    for record in records:
        grouped.setdefault(record.condition, []).append(record.metrics)
    return grouped
