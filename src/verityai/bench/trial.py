"""Run a `TrialSpec` for real, and retain what it produced.

This module exists because of an audited failure (ADR-0021's sibling
finding, Phase 0 truth repair): every "N trials per condition" measurement
in this project's history before `verity eval` was hand-run and hand-scored
-- `bench/repetition.py` (the statistics) has always been solid, but nothing
fed it real, retained data. Three pilots' published numbers were destroyed
when a later re-run of their own setup script wiped the only copy of the
post-trial code, because nothing under `trials/` was ever git-tracked.

This module runs the trials and seals each one with a content hash. The
hash alone is *not* the fix, and an earlier version of this docstring
claimed it was: a fingerprint can prove a tree is unchanged but cannot
re-derive a number from a tree that no longer exists. The retained artifact
lives in `bench/evidence.py` (diff + scorer output + manifest, pinned to a
hashed fixture); `run_trials` writes it whenever `evidence_root` is given,
and `bench/eval.py` refuses to call a run publishable when it is not.
See ADR-0027.

No model call happens in this file. `invoke_agent` is injected (CLAUDE.md
rule 3: "make the model injectable so tests pass a lambda") -- it may be a
real agent invocation, or, like `experiments/lib/setup_phase_a.sh`'s
deliberately fabricated phase-A state, a scripted stand-in. Scoring is
always a real subprocess (`pytest`, a hidden-test module), never the
agent's own report -- the same discipline `docs/MEASUREMENTS.md` states for
every prior pilot.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from verityai.bench.evidence import (
    EXCLUDE_DIRS,
    hash_tree,
    parse_metrics,
    write_trial_evidence,
)
from verityai.core.models import FailureMode, TrialRecord, TrialSpec


def _scorer_env(
    trial_dir: Path,
    fixture: Path,
    condition: str,
    index: int,
    spec_dir: Path | None,
) -> dict[str, str]:
    """The scorer's environment: the inherited one plus where it is running.

    A scorer runs with `cwd=trial_dir` under a scratch work root, so it has
    no stable relative path back to anything committed. Without
    `VERITY_SPEC_DIR` a hidden test would have to live inside the fixture --
    where the agent being measured could read it, which would invalidate the
    measurement rather than merely inconvenience it.
    """
    env = dict(os.environ)
    env.update(
        {
            "VERITY_TRIAL_DIR": str(trial_dir.resolve()),
            "VERITY_FIXTURE": str(fixture.resolve()),
            "VERITY_CONDITION": condition,
            "VERITY_TRIAL_INDEX": str(index),
        }
    )
    if spec_dir is not None:
        env["VERITY_SPEC_DIR"] = str(spec_dir.resolve())
    return env


def run_trials(
    spec: TrialSpec,
    invoke_agent: Callable[[Path, str, int], None],
    work_root: Path,
    metric_fn: Callable[[Path, int], dict[str, float]] | None = None,
    classify_failure: Callable[[Path, int], FailureMode | None] | None = None,
    evidence_root: Path | None = None,
    spec_dir: Path | None = None,
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
      4. Hash the resulting tree, and -- when `evidence_root` is given --
         write the diff, the scorer's output and a manifest line that make
         the trial's metric re-derivable by someone who has only this
         repository (`bench/evidence.py`, ADR-0027).

    `work_root` is scratch: copied fixtures, `__pycache__`, rewritten on
    every run, and git-ignored. `evidence_root` is the retained, tracked
    artifact. Keeping them separate is the fix for the failure that lost
    three pilots' evidence -- the ignored copy was the only copy.

    `metric_fn(trial_dir, exit_code) -> dict` takes precedence for callers
    that score in-process; without it, metrics come from JSON on the
    scorer's stdout, falling back to `success` from the exit code
    (`parse_metrics`).

    The scorer runs with `VERITY_TRIAL_DIR`, `VERITY_FIXTURE`,
    `VERITY_CONDITION`, `VERITY_TRIAL_INDEX` and -- when `spec_dir` is given
    -- `VERITY_SPEC_DIR` in its environment. That last one is what lets a
    hidden scorer live beside the spec instead of inside the fixture, where
    an agent under test would be able to read it.
    """
    work_root.mkdir(parents=True, exist_ok=True)
    fixture = Path(spec.fixture_path)
    fixture_hash = hash_tree(fixture)
    run_id = uuid4().hex[:12]
    records: list[TrialRecord] = []

    for condition in spec.conditions:
        for index in range(1, spec.n + 1):
            trial_id = f"{condition}_{index}"
            trial_dir = work_root / trial_id
            if trial_dir.exists():
                shutil.rmtree(trial_dir)
            # Ignore execution artifacts at copy time: a fixture polluted by
            # an earlier local run would otherwise make hashes and diffs
            # differ for reasons that are not about the trial.
            shutil.copytree(fixture, trial_dir, ignore=shutil.ignore_patterns(*EXCLUDE_DIRS))

            invoke_agent(trial_dir, condition, index)

            result = subprocess.run(
                spec.scorer_command,
                shell=True,
                cwd=trial_dir,
                capture_output=True,
                text=True,
                env=_scorer_env(trial_dir, fixture, condition, index, spec_dir),
            )
            exit_code = result.returncode

            if metric_fn is not None:
                metrics = metric_fn(trial_dir, exit_code)
                metrics_source, metrics_reason = "metric_fn", None
            else:
                metrics, metrics_source, metrics_reason = parse_metrics(result.stdout, exit_code)

            transcript_path = trial_dir / "transcript.txt"

            record = TrialRecord(
                trial_id=trial_id,
                condition=condition,
                scorer_exit_code=exit_code,
                metrics=metrics,
                metrics_source=metrics_source,
                metrics_source_reason=metrics_reason,
                transcript_path=str(transcript_path) if transcript_path.exists() else None,
                artifact_hash=hash_tree(trial_dir),
                failure_mode=(
                    classify_failure(trial_dir, exit_code) if classify_failure is not None else None
                ),
            )
            records.append(record)

            if evidence_root is not None:
                write_trial_evidence(
                    evidence_root,
                    record,
                    fixture,
                    trial_dir,
                    run_id=run_id,
                    fixture_hash=fixture_hash,
                    scorer_stdout=result.stdout,
                    scorer_stderr=result.stderr,
                    metrics_source=metrics_source,
                    metrics_source_reason=metrics_reason,
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
