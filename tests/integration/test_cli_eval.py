"""End-to-end tests for `verity eval`.

There was no CLI test for this command at all before ADR-0027, which is part
of why two defects in it survived: the default `--work-root` pointed inside
git-ignored `.verity/`, and the report was only written if the operator
remembered `--json`. Both are the kind of thing only an end-to-end test sees,
because each component was individually correct.

`test_the_default_evidence_root_is_not_git_ignored` is the one that matters
most. It is the only test in the suite that would have caught the original
failure mode -- evidence written where git will never track it -- and, unlike
a comment in `.gitignore`, it keeps catching it.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from verityai.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

REPO_ROOT = Path(__file__).parents[2]


def _write_spec(tmp_path: Path, *, n: int = 5) -> Path:
    """A spec whose scorer prints JSON, so metrics beyond `success` arrive."""
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "value.txt").write_text("0\n")
    (fixture / "score.py").write_text(
        "import json, pathlib\n"
        'ok = pathlib.Path("value.txt").read_text().strip() == "1"\n'
        'print(json.dumps({"fixed": 1.0 if ok else 0.0}))\n'
        "raise SystemExit(0 if ok else 1)\n"
    )

    spec = {
        "name": "cli-eval-probe",
        "fixture_path": str(fixture),
        "conditions": ["naive", "verity"],
        "n": n,
        "scorer_command": f'"{sys.executable}" score.py',
        "metric_keys": ["fixed"],
        # Only the `verity` arm fixes the value; `naive` is the untouched
        # fixture, so this produces the 0/5 vs 5/5 shape the pilots used.
        "condition_commands": {"verity": "echo 1 > value.txt"},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec, indent=2))
    return spec_path


class TestEvidenceIsRetained:
    def test_evidence_is_written_without_being_asked_for(self, tmp_path):
        """No `--json`, no extra flags: spec, report and manifest must all
        land. Previously the report survived only if the operator remembered
        a flag, so an ordinary run published numbers and retained nothing."""
        spec_path = _write_spec(tmp_path)
        evidence = tmp_path / "evidence"

        result = runner.invoke(
            app,
            [
                "eval",
                str(spec_path),
                "--work-root",
                str(tmp_path / "work"),
                "--evidence-root",
                str(evidence),
            ],
        )

        assert (evidence / "spec.json").exists()
        assert (evidence / "report.json").exists()
        assert (evidence / "manifest.jsonl").exists()

        lines = [
            json.loads(line)
            for line in (evidence / "manifest.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert len(lines) == 10
        for entry in lines:
            assert (evidence / entry["diff_path"]).exists()
            assert (evidence / entry["scorer_log_path"]).exists()

        assert "Evidence retained in" in result.output

    def test_the_scorers_json_metric_reaches_the_comparison(self, tmp_path):
        """The gap ADR-0027 found: `run_eval` was called with no `metric_fn`,
        so a spec asking for any metric but `success` got `insufficient_data`
        inside a report that still looked publishable."""
        spec_path = _write_spec(tmp_path)
        evidence = tmp_path / "evidence"

        runner.invoke(
            app,
            [
                "eval",
                str(spec_path),
                "--work-root",
                str(tmp_path / "work"),
                "--evidence-root",
                str(evidence),
            ],
        )

        report = json.loads((evidence / "report.json").read_text())
        fixed = report["comparisons"]["verity"]["fixed"]

        assert fixed["conclusion"] != "insufficient_data", report["warnings"]
        assert all(t["metrics_source"] == "scorer_json" for t in report["trials"])
        assert all("fixed" in t["metrics"] for t in report["trials"])

    def test_a_degenerate_floor_still_exits_nonzero(self, tmp_path):
        """The `naive` arm never varies here, so the floor is [0,0] -- the
        condition seven of nine prior pilots were in. It must be reported,
        not passed over because everything else looks good."""
        spec_path = _write_spec(tmp_path)

        result = runner.invoke(
            app,
            [
                "eval",
                str(spec_path),
                "--work-root",
                str(tmp_path / "work"),
                "--evidence-root",
                str(tmp_path / "evidence"),
            ],
        )

        assert result.exit_code == 1
        assert "NOT PUBLISHABLE" in result.output
        assert "degenerate" in result.output


class TestTheRetentionPathIsActuallyTracked:
    """`.gitignore` is the mechanism that defeated invariant 7 twice. These
    assert the current arrangement rather than trusting the comment there."""

    @staticmethod
    def _is_ignored(path: Path) -> bool:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        return result.returncode == 0

    def test_the_default_evidence_root_is_not_git_ignored(self):
        """The original defect: `--work-root` defaulted into `.verity/`, which
        `.gitignore` excludes, so the harness built to satisfy invariant 7
        wrote its artifact where git could never see it. The default evidence
        root must be tracked, and this is the check that keeps it so."""
        default_evidence = REPO_ROOT / "experiments" / "some-spec-name" / "evidence"

        assert not self._is_ignored(default_evidence / "manifest.jsonl"), (
            "the default evidence root is git-ignored -- published numbers would "
            "again have no artifact a third party could fetch"
        )

    def test_scratch_trial_directories_are_still_ignored(self):
        """The inverse must also hold, or every run adds copied fixtures and
        __pycache__ to the diff and reviewers stop reading -- which is the
        pressure that got these directories ignored in the first place."""
        assert self._is_ignored(
            REPO_ROOT / "experiments" / "family_b_pilot_8_arbitrary_tiebreak" / "trials" / "naive_1"
        )

    def test_a_pilots_committed_evidence_is_not_ignored(self):
        assert not self._is_ignored(
            REPO_ROOT
            / "experiments"
            / "family_b_pilot_8_arbitrary_tiebreak"
            / "evidence"
            / "manifest.jsonl"
        )
