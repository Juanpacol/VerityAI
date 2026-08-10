"""Tests for retained trial evidence (`bench/evidence.py`).

The load-bearing test here is `test_the_retained_diff_re_derives_the_metric`.
Everything else in this file supports it.

Nothing in this repository previously tested the property invariant 7
actually claims. `test_identical_trials_hash_identically` checks that two
hashes match, which is a statement about the hash function, not about
whether a published number can be re-checked by someone who has only this
repository. ADR-0027's position is that the difference matters: a hash of a
directory that no longer exists re-derives nothing. So the test below does
what a skeptical third party would do -- copy the fixture, apply the
retained diff, run the scorer, and demand the manifest's number back.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from verityai.bench.eval import run_eval
from verityai.bench.evidence import hash_tree, read_manifest, tree_diff, verify_evidence
from verityai.core.models import TrialSpec


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except (OSError, FileNotFoundError):
        return False


requires_git = pytest.mark.skipif(
    not _git_available(),
    reason="git is needed to apply a retained diff, which is the point of the test",
)


def _fixture(tmp_path: Path) -> Path:
    """A fixture whose scorer reads a value the trial is expected to fix."""
    fixture = tmp_path / "fixture"
    (fixture / "pkg").mkdir(parents=True)
    (fixture / "pkg" / "calc.py").write_text("def value():\n    return 0\n")
    (fixture / "score.py").write_text(
        "import json\n"
        "from pkg.calc import value\n"
        "ok = value() == 1\n"
        'print(json.dumps({"correct": 1.0 if ok else 0.0}))\n'
        "raise SystemExit(0 if ok else 1)\n"
    )
    return fixture


# `sys.executable`, not a bare `python`: this machine has no `python` on
# PATH, and with one the scorer silently exited 127 with no output -- which
# the re-derivation test then "passed", because 127-with-no-output reproduced
# 127-with-no-output exactly. A test for re-derivability that is satisfied by
# both sides failing identically is the T6 lesson in miniature, so the
# assertions below also require the metric itself to come back.
_SCORER = f'"{sys.executable}" score.py'


def _spec(fixture: Path, n: int = 5) -> TrialSpec:
    return TrialSpec(
        name="evidence-probe",
        fixture_path=str(fixture),
        conditions=["naive", "verity"],
        n=n,
        scorer_command=_SCORER,
        metric_keys=["correct"],
    )


def _invoke(trial_dir: Path, condition: str, index: int) -> None:
    """`verity` fixes the value; `naive` fixes it only on later trials, so
    the baseline has a real spread rather than a degenerate floor."""
    fixed = condition == "verity" or index > 3
    if fixed:
        (trial_dir / "pkg" / "calc.py").write_text("def value():\n    return 1\n")


class TestInvariantSeven:
    @requires_git
    def test_the_retained_diff_re_derives_the_metric(self, tmp_path):
        """Invariant 7, demonstrated rather than asserted.

        For every trial: take the fixture, apply the retained diff, run the
        scorer, and require the exit code and the metric to match what the
        manifest published. If this passes, a third party with only this
        repository can re-check every number the run produced.
        """
        fixture = _fixture(tmp_path)
        evidence = tmp_path / "evidence"

        report = run_eval(
            _spec(fixture),
            _invoke,
            work_root=tmp_path / "work",
            evidence_root=evidence,
        )

        entries = read_manifest(evidence)
        assert len(entries) == 10, "5 trials x 2 conditions must each leave a manifest line"
        # Guard against the vacuous pass: if the scorer never reported the
        # metric, every comparison below would be trivially satisfied by both
        # sides producing nothing.
        assert all("correct" in e["metrics"] for e in entries), (
            "the scorer must actually report `correct`, or this test proves nothing"
        )
        assert {e["metrics_source"] for e in entries} == {"scorer_json"}

        for entry in entries:
            replay = tmp_path / "replay" / entry["trial_id"]
            replay.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(fixture, replay)

            diff = (evidence / entry["diff_path"]).read_text()
            if diff.strip():
                # Fed on stdin so the diff is applied exactly as retained.
                applied = subprocess.run(
                    ["git", "apply", "-p1"],
                    cwd=replay,
                    input=diff,
                    capture_output=True,
                    text=True,
                )
                assert applied.returncode == 0, (
                    f"retained diff for {entry['trial_id']} does not apply: {applied.stderr}"
                )

            rerun = subprocess.run(_SCORER, shell=True, cwd=replay, capture_output=True, text=True)

            assert rerun.returncode == entry["scorer_exit_code"], (
                f"{entry['trial_id']}: re-derived exit {rerun.returncode}, "
                f"manifest published {entry['scorer_exit_code']}"
            )
            re_derived = json.loads(rerun.stdout.strip().splitlines()[-1])
            assert re_derived["correct"] == entry["metrics"]["correct"], (
                f"{entry['trial_id']}: re-derived correct={re_derived['correct']}, "
                f"manifest published {entry['metrics']['correct']}"
            )

        assert report.is_publishable, report.warnings

    def test_the_manifest_names_evidence_that_exists_at_relative_paths(self, tmp_path):
        """An absolute /Users/... path is not third-party checkable, so every
        path in the manifest must be relative to the evidence root."""
        fixture = _fixture(tmp_path)
        evidence = tmp_path / "evidence"

        run_eval(_spec(fixture), _invoke, work_root=tmp_path / "work", evidence_root=evidence)

        for entry in read_manifest(evidence):
            for key in ("evidence_dir", "diff_path", "scorer_log_path"):
                assert not Path(entry[key]).is_absolute(), f"{key} must be relative"
                assert (evidence / entry[key]).exists(), f"{key} names something missing"
            assert entry["fixture_hash"].startswith("sha256:")
            assert entry["artifact_hash"].startswith("sha256:")

    def test_the_spec_and_report_are_retained_without_being_asked(self, tmp_path):
        """ADR-0022 claimed a reproduction whose spec was never committed, so
        the claim about the reproduction was itself not re-derivable."""
        fixture = _fixture(tmp_path)
        evidence = tmp_path / "evidence"

        run_eval(_spec(fixture), _invoke, work_root=tmp_path / "work", evidence_root=evidence)

        spec_back = TrialSpec.model_validate_json((evidence / "spec.json").read_text())
        assert spec_back.metric_keys == ["correct"]
        assert json.loads((evidence / "report.json").read_text())["evidence_root"]


class TestTreeDiff:
    @requires_git
    def test_added_modified_and_deleted_files_all_apply(self, tmp_path):
        """Where `/dev/null` header handling breaks if it is going to."""
        before = tmp_path / "before"
        (before / "sub").mkdir(parents=True)
        (before / "keep.py").write_text("keep = 1\n")
        (before / "edit.py").write_text("value = 1\n")
        (before / "sub" / "gone.py").write_text("removed = True\n")

        after = tmp_path / "after"
        shutil.copytree(before, after)
        (after / "edit.py").write_text("value = 2\n")
        (after / "sub" / "gone.py").unlink()
        (after / "added.py").write_text("fresh = True\n")

        diff, unreproducible = tree_diff(before, after)
        assert unreproducible == []

        replay = tmp_path / "replay"
        shutil.copytree(before, replay)
        applied = subprocess.run(
            ["git", "apply", "-p1"], cwd=replay, input=diff, capture_output=True, text=True
        )

        assert applied.returncode == 0, applied.stderr
        assert hash_tree(replay) == hash_tree(after), (
            "applying the diff must reproduce the tree byte for byte"
        )

    @requires_git
    def test_a_file_without_a_trailing_newline_still_applies(self, tmp_path):
        """difflib emits such a line verbatim; without git's explicit
        `\\ No newline at end of file` marker the next hunk header is
        misread and the whole diff fails to apply."""
        before = tmp_path / "before"
        before.mkdir()
        (before / "a.txt").write_text("one\ntwo")

        after = tmp_path / "after"
        after.mkdir()
        (after / "a.txt").write_text("one\nTWO")

        diff, _ = tree_diff(before, after)
        assert "\\ No newline at end of file" in diff

        replay = tmp_path / "replay"
        shutil.copytree(before, replay)
        applied = subprocess.run(
            ["git", "apply", "-p1"], cwd=replay, input=diff, capture_output=True, text=True
        )

        assert applied.returncode == 0, applied.stderr
        assert (replay / "a.txt").read_text() == "one\nTWO"

    def test_execution_artifacts_are_not_diffed(self, tmp_path):
        before = tmp_path / "before"
        before.mkdir()
        (before / "a.py").write_text("a = 1\n")

        after = tmp_path / "after"
        shutil.copytree(before, after)
        (after / "__pycache__").mkdir()
        (after / "__pycache__" / "a.pyc").write_bytes(b"\x00compiled")

        diff, unreproducible = tree_diff(before, after)

        assert diff == ""
        assert unreproducible == []

    def test_an_undecodable_file_is_declared_not_silently_dropped(self, tmp_path):
        """A diff that quietly skipped a binary the trial produced would read
        as complete while being unable to reconstruct the trial."""
        before = tmp_path / "before"
        before.mkdir()
        (before / "a.py").write_text("a = 1\n")

        after = tmp_path / "after"
        shutil.copytree(before, after)
        (after / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary")

        diff, unreproducible = tree_diff(before, after)

        assert [f["path"] for f in unreproducible] == ["blob.bin"]
        assert "not UTF-8" in unreproducible[0]["reason"]
        assert unreproducible[0]["sha256"].startswith("sha256:")
        assert "blob.bin" not in diff

    def test_an_undecodable_artifact_makes_the_run_unpublishable(self, tmp_path):
        fixture = _fixture(tmp_path)
        evidence = tmp_path / "evidence"

        def invoke_leaving_a_binary(trial_dir, condition, index):
            _invoke(trial_dir, condition, index)
            (trial_dir / "artifact.bin").write_bytes(b"\xff\xfe\x00binary")

        report = run_eval(
            _spec(fixture),
            invoke_leaving_a_binary,
            work_root=tmp_path / "work",
            evidence_root=evidence,
        )

        assert not report.is_publishable
        assert any("could not be represented in a diff" in w for w in report.warnings)


class TestFixtureDrift:
    def test_a_moved_base_is_detectable(self, tmp_path):
        """A diff is only re-derivable against a pinned base. The fixture is
        tracked but mutable, so a later edit would make an old diff apply to
        the wrong content -- a third party must learn the base moved rather
        than be misled by it."""
        fixture = _fixture(tmp_path)
        evidence = tmp_path / "evidence"

        run_eval(_spec(fixture), _invoke, work_root=tmp_path / "work", evidence_root=evidence)
        recorded = read_manifest(evidence)[0]["fixture_hash"]
        assert recorded == hash_tree(fixture)

        (fixture / "pkg" / "calc.py").write_text("def value():\n    return 99\n")

        assert hash_tree(fixture) != recorded, "fixture drift must change the hash"


class TestVerifyEvidence:
    """`verity verify` -- invariant 7 as an operation anyone can run, rather
    than a property demonstrated once inside a test on a fixture the test
    itself built."""

    def _run(self, tmp_path, spec_dir=None):
        fixture = _fixture(tmp_path)
        evidence = tmp_path / "evidence"
        run_eval(
            _spec(fixture),
            _invoke,
            work_root=tmp_path / "work",
            evidence_root=evidence,
            spec_dir=spec_dir,
        )
        return evidence

    @requires_git
    def test_every_trial_re_derives(self, tmp_path):
        evidence = self._run(tmp_path)

        results = verify_evidence(evidence, tmp_path / "replay")

        assert len(results) == 10
        assert all(r["ok"] for r in results), [r for r in results if not r["ok"]]

    @requires_git
    def test_verification_works_when_the_work_root_is_inside_a_git_repo(self, tmp_path):
        """The defect `verity verify` hit on its first real run.

        `git apply` resolves paths against the enclosing work tree's root,
        not the cwd. Inside a repository it finds nothing matching the patch
        and exits **0 having changed nothing** -- so every trial "verified"
        against an unmodified fixture and reported a mismatch whose stated
        reason (a scorer exit code) had nothing to do with the cause. The
        default work root lives under `.verity/`, inside this repository, so
        this was the normal case rather than an edge one.
        """
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
        evidence = self._run(tmp_path)

        results = verify_evidence(evidence, tmp_path / "inside_repo_replay")

        assert all(r["ok"] for r in results), (
            "verification must not depend on whether the scratch directory "
            "happens to sit inside a git work tree"
        )

    @requires_git
    def test_a_tampered_diff_is_caught(self, tmp_path):
        """The property that makes verification worth running at all."""
        evidence = self._run(tmp_path)
        target = evidence / "trials" / "verity_1" / "changes.diff"
        target.write_text(target.read_text().replace("return 1", "return 7"))

        results = verify_evidence(evidence, tmp_path / "replay")

        bad = [r for r in results if r["trial_id"] == "verity_1"]
        assert bad and not bad[0]["ok"]
        assert "correct" in bad[0]["reason"] or "exited" in bad[0]["reason"]

    def test_a_drifted_fixture_is_reported_as_drift_not_as_a_failed_check(self, tmp_path):
        """A diff may be perfectly valid against the base it was made from.
        Reporting that as a failed metric would blame the wrong thing."""
        fixture = _fixture(tmp_path)
        evidence = tmp_path / "evidence"
        run_eval(_spec(fixture), _invoke, work_root=tmp_path / "work", evidence_root=evidence)

        (fixture / "pkg" / "calc.py").write_text("def value():\n    return 42\n")
        results = verify_evidence(evidence, tmp_path / "replay")

        assert all(not r["ok"] for r in results)
        assert all("fixture has changed" in r["reason"] for r in results)

    def test_evidence_without_a_spec_cannot_be_verified_and_says_so(self, tmp_path):
        evidence = self._run(tmp_path)
        (evidence / "spec.json").unlink()

        results = verify_evidence(evidence, tmp_path / "replay")

        assert all(not r["ok"] for r in results)
        assert "not retained" in results[0]["reason"]

    @requires_git
    def test_only_the_latest_run_is_verified(self, tmp_path):
        """The manifest is append-only, so re-running a spec leaves several
        runs in it. Verifying all of them would re-check superseded trials
        against the current fixture and report history as failure."""
        evidence = self._run(tmp_path)
        run_eval(
            _spec(fixture=Path(json.loads((evidence / "spec.json").read_text())["fixture_path"])),
            _invoke,
            work_root=tmp_path / "work2",
            evidence_root=evidence,
        )

        assert len(read_manifest(evidence)) == 20
        assert len(verify_evidence(evidence, tmp_path / "replay")) == 10

    @requires_git
    def test_the_recorded_spec_dir_is_used_for_the_scorer(self, tmp_path):
        """`$VERITY_SPEC_DIR` is how a hidden scorer is reached, so evidence
        that does not record where the spec lived cannot be re-verified."""
        spec_home = tmp_path / "spec_home"
        spec_home.mkdir()
        evidence = self._run(tmp_path, spec_dir=spec_home)

        recorded = json.loads((evidence / "report.json").read_text())["spec_dir"]

        assert recorded
        assert (evidence / recorded).resolve() == spec_home.resolve()
