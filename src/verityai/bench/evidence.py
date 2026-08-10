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
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

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
    (trial_evidence / "changes.diff").write_text(diff, encoding="utf-8")
    (trial_evidence / "scorer.txt").write_text(
        _scorer_log(scorer_stdout, scorer_stderr), encoding="utf-8"
    )

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


def write_run_evidence(evidence_root: Path, spec: TrialSpec, report_json: dict[str, Any]) -> None:
    """Retain the spec that produced a run, and the run's own report.

    Unconditional, not behind a `--json` flag. ADR-0022 stated that
    `verity eval` reproduced pilot 8's result, but the spec that did it was
    never committed -- so the claim about the reproduction was itself not
    re-derivable. Writing the spec beside its evidence is what stops that
    recurring.
    """
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / SPEC_NAME).write_text(
        json.dumps(json.loads(spec.model_dump_json()), indent=2) + "\n", encoding="utf-8"
    )
    (evidence_root / REPORT_NAME).write_text(
        json.dumps(report_json, indent=2) + "\n", encoding="utf-8"
    )


def read_manifest(evidence_root: Path) -> list[dict[str, Any]]:
    """Every manifest entry, in write order. Missing file -> empty list."""
    path = evidence_root / MANIFEST_NAME
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
