"""Retained, re-derivable evidence for a trial run.

This module exists because ADR-0022 claimed to satisfy invariant 7 ("no
published metric without a retained, re-derivable artifact") and did not.
`TrialRecord.artifact_hash` is a *fingerprint*: it can prove a directory has
not changed, and it can prove two trials produced identical trees. It cannot
re-derive a number. Once the directory is gone -- which is exactly what
happened to three pilots on 2026-08-10 -- a hash establishes nothing a third
party could re-check, and invariant 7 asks for precisely that (ADR-0027).

What is retained here instead, per trial:

- **`changes.diff`** -- a unified diff from the fixture to the post-trial
  tree, `git apply -p1`-compatible. The fixture is git-tracked and the
  commands live in the spec, so fixture + diff + scorer reconstructs the
  trial. This is the artifact; the hash is the seal on it.
- **`scorer.txt`** -- the scorer's own stdout/stderr. With metrics now
  parseable from scorer stdout (`trial.py::parse_metrics`), discarding it
  would leave the published numbers unbacked again, one level up.
- **one `manifest.jsonl` line** -- metrics, exit code, both hashes, and the
  paths of the above, all relative to the evidence root. An absolute
  `/Users/...` path is not third-party checkable.

`fixture_hash` is not decoration. A diff is only re-derivable against a
pinned base: the fixture is tracked but mutable, so a later fixture edit
would make an old diff apply against the wrong content, or fail
confusingly. Recording the base's hash means a third party learns the base
moved instead of being misled by it.

Declared limits, because a silent gap here would be the original failure in
miniature: only UTF-8 text can be diffed, so undecodable files are reported
in `unreproducible_files` (and make the run unpublishable) rather than
dropped; file modes, symlinks and empty directories are not captured.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from verityai.core.atomic import atomic_write_text
from verityai.core.models import TrialRecord, TrialSpec

# Directories that are execution artifacts, not trial content. Hashing or
# diffing them would make two runs of an identical trial differ for reasons
# that have nothing to do with what the trial produced. One definition,
# shared by the hash and the diff -- if they disagreed, a file could be
# sealed by the hash but missing from the artifact.
EXCLUDE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".verity"})

MANIFEST_NAME = "manifest.jsonl"
SPEC_NAME = "spec.json"
REPORT_NAME = "report.json"
TRIALS_DIR = "trials"


def _is_excluded(relative: Path) -> bool:
    return any(part in EXCLUDE_DIRS for part in relative.parts)


def _tracked_files(root: Path) -> dict[str, Path]:
    """Every diffable file under `root`, keyed by its POSIX-relative path.

    POSIX separators regardless of platform: the key becomes a path inside a
    unified diff, and `git apply` expects forward slashes.
    """
    return {
        p.relative_to(root).as_posix(): p
        for p in sorted(root.rglob("*"))
        if p.is_file() and not _is_excluded(p.relative_to(root))
    }


def hash_tree(root: Path) -> str:
    """A deterministic content hash of every file under `root`.

    Sorted paths and streamed content, not `os.walk`'s arbitrary order: two
    trees with identical content must hash identically regardless of
    filesystem iteration order.

    Full-length and prefixed `sha256:`. The earlier 16-hex-character
    truncation was 64 bits -- thin for a tamper claim -- and a bare hex
    string does not say what produced it, which is the same objection
    invariant 3 raises against a bare token count.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _is_excluded(relative):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def _read_lines(path: Path) -> list[str] | None:
    """Text lines with line endings kept, or `None` if not UTF-8 text."""
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (UnicodeDecodeError, OSError):
        return None


def _file_sha(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _mark_missing_newlines(diff_lines: Iterable[str]) -> list[str]:
    """Append git's `\\ No newline at end of file` where a line lacks one.

    `difflib.unified_diff` emits the content line verbatim, so a file whose
    last line has no trailing newline yields a diff line without one --
    which silently corrupts every following hunk header when applied. git
    marks that case explicitly; so must this.
    """
    out: list[str] = []
    for line in diff_lines:
        if line.endswith("\n"):
            out.append(line)
        else:
            out.append(line + "\n")
            out.append("\\ No newline at end of file\n")
    return out


def _file_diff(
    relative: str,
    before: list[str],
    after: list[str],
    *,
    added: bool,
    deleted: bool,
) -> str:
    """One file's diff, with the headers `git apply -p1` needs."""
    header = [f"diff --git a/{relative} b/{relative}\n"]
    if added:
        header.append("new file mode 100644\n")
    elif deleted:
        header.append("deleted file mode 100644\n")

    body = difflib.unified_diff(
        before,
        after,
        fromfile="/dev/null" if added else f"a/{relative}",
        tofile="/dev/null" if deleted else f"b/{relative}",
    )
    return "".join(header) + "".join(_mark_missing_newlines(body))


def tree_diff(fixture: Path, trial_dir: Path) -> tuple[str, list[dict[str, str]]]:
    """A unified diff from `fixture` to `trial_dir`, plus what it could not
    represent.

    The diff applies with `git apply -p1` from inside a fresh copy of the
    fixture, which is what makes the trial's metric re-derivable by someone
    who has only this repository. Added and deleted files are represented
    with `/dev/null` on the appropriate side.

    The second return value lists files that are not UTF-8 text, each with
    its own content hash and the reason. They are *declared*, never silently
    omitted -- a diff that quietly skipped a binary the trial produced would
    read as complete while being unable to reconstruct the trial.
    """
    before_files = _tracked_files(fixture)
    after_files = _tracked_files(trial_dir)

    chunks: list[str] = []
    unreproducible: list[dict[str, str]] = []

    for relative in sorted(before_files.keys() | after_files.keys()):
        old_path = before_files.get(relative)
        new_path = after_files.get(relative)

        before: list[str] = []
        if old_path is not None:
            read = _read_lines(old_path)
            if read is None:
                unreproducible.append(
                    {
                        "path": relative,
                        "sha256": _file_sha(old_path),
                        "side": "fixture",
                        "reason": "not UTF-8 text; a unified diff cannot represent it",
                    }
                )
                continue
            before = read

        after: list[str] = []
        if new_path is not None:
            read = _read_lines(new_path)
            if read is None:
                unreproducible.append(
                    {
                        "path": relative,
                        "sha256": _file_sha(new_path),
                        "side": "trial",
                        "reason": "not UTF-8 text; a unified diff cannot represent it",
                    }
                )
                continue
            after = read

        if before == after:
            continue

        chunks.append(
            _file_diff(
                relative,
                before,
                after,
                added=old_path is None,
                deleted=new_path is None,
            )
        )

    return "".join(chunks), unreproducible


def parse_metrics(stdout: str, exit_code: int) -> tuple[dict[str, float], str, str | None]:
    """Metrics for one trial, plus where they came from and why.

    Returns `(metrics, source, reason)`. Three sources, in precedence order
    -- `metric_fn` is handled by the caller, so this covers the two the CLI
    can reach:

    - **`scorer_json`** -- the scorer printed a JSON object on stdout. This
      exists because the CLI previously could not express any metric except
      `success`: `run_eval` was called without a `metric_fn`, so a spec
      asking for `tie_correct` got `insufficient_data` and a report that
      still looked publishable (ADR-0027). A scorer already runs as a real
      subprocess and is already ground truth for pass/fail; letting it also
      report *what* it measured keeps scoring out of the agent's hands.
    - **`exit_code`** -- the fallback, `success = exit_code == 0`.

    `success` is always seeded first so it cannot vanish from a run just
    because a scorer reported other keys. When stdout looked like JSON but
    was rejected, the reason is returned rather than discarded -- invariant
    5: every degraded path says why.
    """
    metrics: dict[str, float] = {"success": 1.0 if exit_code == 0 else 0.0}

    candidate = next(
        (line for line in reversed(stdout.strip().splitlines()) if line.strip()),
        "",
    ).strip()
    if not candidate.startswith("{"):
        return metrics, "exit_code", None

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return metrics, "exit_code", f"scorer stdout looked like JSON but did not parse: {exc}"

    if not isinstance(parsed, dict):
        return metrics, "exit_code", "scorer stdout parsed as JSON but was not an object"

    rejected = {
        key: value
        for key, value in parsed.items()
        if not isinstance(value, (int, float, bool)) or isinstance(value, str)
    }
    if rejected:
        return (
            metrics,
            "exit_code",
            f"scorer stdout had non-numeric values for {sorted(rejected)}; "
            "a metric must be a number a noise floor can be computed over",
        )

    metrics.update({key: float(value) for key, value in parsed.items()})
    return metrics, "scorer_json", None


def _scorer_log(stdout: str, stderr: str) -> str:
    return f"--- scorer stdout ---\n{stdout}\n--- scorer stderr ---\n{stderr}\n"


def write_trial_evidence(
    evidence_root: Path,
    record: TrialRecord,
    fixture: Path,
    trial_dir: Path,
    *,
    run_id: str,
    fixture_hash: str,
    scorer_stdout: str = "",
    scorer_stderr: str = "",
    metrics_source: str = "exit_code",
    metrics_source_reason: str | None = None,
) -> dict[str, Any]:
    """Write one trial's evidence and append its `manifest.jsonl` line.

    Returns the manifest entry, so a caller can report what it retained
    without re-reading the file.
    """
    trial_evidence = evidence_root / TRIALS_DIR / record.trial_id
    trial_evidence.mkdir(parents=True, exist_ok=True)

    diff, unreproducible = tree_diff(fixture, trial_dir)
    atomic_write_text(trial_evidence / "changes.diff", diff)
    atomic_write_text(trial_evidence / "scorer.txt", _scorer_log(scorer_stdout, scorer_stderr))

    transcript_relative: str | None = None
    if record.transcript_path:
        source = Path(record.transcript_path)
        if source.exists():
            shutil.copy2(source, trial_evidence / "transcript.txt")
            transcript_relative = f"{TRIALS_DIR}/{record.trial_id}/transcript.txt"

    entry: dict[str, Any] = {
        "run_id": run_id,
        "id": str(record.id),
        "trial_id": record.trial_id,
        "condition": record.condition,
        "created_at": record.created_at.isoformat(),
        "scorer_exit_code": record.scorer_exit_code,
        "metrics": record.metrics,
        "metrics_source": metrics_source,
        "metrics_source_reason": metrics_source_reason,
        "artifact_hash": record.artifact_hash,
        "fixture_hash": fixture_hash,
        "evidence_dir": f"{TRIALS_DIR}/{record.trial_id}",
        "diff_path": f"{TRIALS_DIR}/{record.trial_id}/changes.diff",
        "diff_lines": diff.count("\n"),
        "scorer_log_path": f"{TRIALS_DIR}/{record.trial_id}/scorer.txt",
        "transcript_path": transcript_relative,
        "failure_mode": record.failure_mode.value if record.failure_mode else None,
        "unreproducible_files": unreproducible,
    }

    evidence_root.mkdir(parents=True, exist_ok=True)
    with (evidence_root / MANIFEST_NAME).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")

    return entry


def write_run_evidence(
    evidence_root: Path,
    spec: TrialSpec,
    report_json: dict[str, Any],
    spec_dir: Path | None = None,
) -> None:
    """Retain the spec that produced a run, and the run's own report.

    Unconditional, not behind a `--json` flag. ADR-0022 stated that
    `verity eval` reproduced pilot 8's result, but the spec that did it was
    never committed -- so the claim about the reproduction was itself not
    re-derivable. Writing the spec beside its evidence is what stops that
    recurring.
    """
    evidence_root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        evidence_root / SPEC_NAME,
        json.dumps(json.loads(spec.model_dump_json()), indent=2) + "\n",
    )
    # Recorded relative to the evidence root, so the artifact stays portable
    # and self-describing. A scorer reached through `$VERITY_SPEC_DIR` cannot
    # be re-run later unless the evidence says where that directory was --
    # `verity verify` found this the first time it ran, by failing on a
    # scorer it could not locate.
    payload = dict(report_json)
    if spec_dir is not None:
        payload["spec_dir"] = os.path.relpath(spec_dir.resolve(), evidence_root.resolve())
    atomic_write_text(evidence_root / REPORT_NAME, json.dumps(payload, indent=2) + "\n")


def verify_evidence(
    evidence_root: Path, work_root: Path, run_id: str | None = None
) -> list[dict[str, Any]]:
    """Re-derive every trial's metrics from what was retained.

    This is invariant 7 as an operation rather than a promise: take the
    fixture, apply the retained diff, run the spec's own scorer, and compare
    what comes back with what the manifest published. A third party with only
    this repository can run it, which is the whole point -- until now the
    property was demonstrated once, in a test, on a fixture the test built.

    Returns one result per manifest entry with `ok` plus the specific
    mismatch. Never raises on a bad trial: a verification tool that dies on
    the first discrepancy cannot tell you how many there are.
    """
    entries = read_manifest(evidence_root)
    if not entries:
        return []

    # The manifest is append-only, so re-running a spec leaves several runs
    # in it. Verifying all of them would re-check superseded trials against
    # the current fixture and report failures that are really just history.
    target = run_id or entries[-1].get("run_id")
    if target is not None:
        entries = [e for e in entries if e.get("run_id") == target]

    spec_path = evidence_root / SPEC_NAME
    if not spec_path.exists():
        return [
            {
                "trial_id": entry["trial_id"],
                "ok": False,
                "reason": f"no {SPEC_NAME} beside the manifest -- the fixture and scorer "
                "that produced these numbers were not retained, so nothing can be re-run",
            }
            for entry in entries
        ]

    spec = TrialSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    fixture = Path(spec.fixture_path)
    scorer_dir = _recorded_spec_dir(evidence_root)
    results: list[dict[str, Any]] = []

    fixture_now = hash_tree(fixture) if fixture.is_dir() else None
    work_root.mkdir(parents=True, exist_ok=True)

    for entry in entries:
        trial_id = entry["trial_id"]
        result: dict[str, Any] = {"trial_id": trial_id, "ok": False, "reason": ""}

        if fixture_now is None:
            result["reason"] = f"fixture {spec.fixture_path!r} no longer exists"
            results.append(result)
            continue
        if fixture_now != entry.get("fixture_hash"):
            # Not a failed check -- a moved base. Saying which it is matters:
            # the diff may be perfectly valid against the fixture it was made
            # from, and applying it to a drifted one would mislead.
            result["reason"] = (
                "the fixture has changed since this trial ran "
                f"(recorded {str(entry.get('fixture_hash'))[:23]}..., "
                f"now {fixture_now[:23]}...), so the retained diff no longer "
                "describes a reachable state"
            )
            results.append(result)
            continue

        replay = work_root / trial_id
        if replay.exists():
            shutil.rmtree(replay)
        shutil.copytree(fixture, replay, ignore=shutil.ignore_patterns(*EXCLUDE_DIRS))
        # `git apply` resolves paths against the enclosing work tree's root,
        # not the cwd. Run inside a repository -- which the default work root
        # under `.verity/` is -- it finds nothing matching and exits **0 having
        # changed nothing**. Making the replay its own toplevel is what keeps
        # verification independent of where the caller put its scratch.
        subprocess.run(["git", "init", "-q"], cwd=replay, capture_output=True)

        diff = (evidence_root / entry["diff_path"]).read_text(encoding="utf-8")
        if diff.strip():
            applied = subprocess.run(
                ["git", "apply", "-p1"],
                cwd=replay,
                input=diff,
                capture_output=True,
                text=True,
            )
            if applied.returncode != 0:
                result["reason"] = (
                    f"the retained diff does not apply: {applied.stderr.strip().splitlines()[0]}"
                    if applied.stderr.strip()
                    else "the retained diff does not apply"
                )
                results.append(result)
                continue

        rerun = subprocess.run(
            spec.scorer_command,
            shell=True,
            cwd=replay,
            capture_output=True,
            text=True,
            env=_verify_env(replay, fixture, scorer_dir, entry),
        )
        metrics, _, _ = parse_metrics(rerun.stdout, rerun.returncode)

        published = entry.get("metrics", {})
        differing = {
            key: (published[key], metrics.get(key))
            for key in published
            if key in metrics and published[key] != metrics[key]
        }
        missing = [key for key in published if key not in metrics]

        if rerun.returncode != entry["scorer_exit_code"]:
            result["reason"] = (
                f"scorer exited {rerun.returncode}, manifest published {entry['scorer_exit_code']}"
            )
        elif differing:
            result["reason"] = "; ".join(
                f"{key}: re-derived {got}, manifest published {want}"
                for key, (want, got) in sorted(differing.items())
            )
        elif missing:
            result["reason"] = f"scorer no longer reports {', '.join(sorted(missing))}"
        else:
            result["ok"] = True
            result["metrics"] = metrics

        results.append(result)

    return results


def _recorded_spec_dir(evidence_root: Path) -> Path:
    """Where the spec lived when the run happened.

    `$VERITY_SPEC_DIR` is how a hidden scorer is reached, so evidence that
    does not record it cannot be re-verified -- the scorer is simply not
    found and every trial fails for a reason that has nothing to do with the
    numbers. Falls back to the evidence root's parent, which is the layout
    `verity eval` defaults to (`experiments/<name>/evidence`).
    """
    report = evidence_root / REPORT_NAME
    if report.exists():
        recorded = json.loads(report.read_text(encoding="utf-8")).get("spec_dir")
        if recorded:
            return (evidence_root / recorded).resolve()
    return evidence_root.parent.resolve()


def _verify_env(
    replay: Path, fixture: Path, spec_dir: Path, entry: dict[str, Any]
) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "VERITY_TRIAL_DIR": str(replay.resolve()),
            "VERITY_FIXTURE": str(fixture.resolve()),
            "VERITY_CONDITION": str(entry.get("condition", "")),
            "VERITY_TRIAL_INDEX": str(entry.get("trial_id", "")).rsplit("_", 1)[-1],
            "VERITY_SPEC_DIR": str(spec_dir.resolve()),
        }
    )
    return env


def read_manifest(evidence_root: Path) -> list[dict[str, Any]]:
    """Every manifest entry, in write order. Missing file -> empty list."""
    path = evidence_root / MANIFEST_NAME
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
