"""Tests for the trial harness (`bench/trial.py`, `bench/eval.py`).

The subject under test is the property Phase 0's truth-repair audit found
missing from every prior Family B pilot: a retained, re-derivable artifact
per trial, and a publishability gate that flags a degenerate noise floor
instead of letting it read as a strong result. `invoke_agent` is always an
injected lambda here (CLAUDE.md rule 3) -- no live agent, no model call.
"""

from verityai.bench.eval import run_eval
from verityai.bench.trial import metrics_by_condition, run_trials
from verityai.core.models import FailureMode, TrialSpec


def _fixture(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "value.txt").write_text("0\n")
    return fixture


def _scorer_that_checks_value(expected: str) -> str:
    """A shell command scoring a trial by the content of value.txt."""
    return f'test "$(cat value.txt)" = "{expected}"'


class TestRunTrials:
    def test_each_trial_gets_its_own_directory_and_artifact_hash(self, tmp_path):
        fixture = _fixture(tmp_path)
        spec = TrialSpec(
            name="t",
            fixture_path=str(fixture),
            conditions=["naive", "verity"],
            n=3,
            scorer_command=_scorer_that_checks_value("1"),
        )

        def invoke(trial_dir, condition, index):
            if condition == "verity":
                (trial_dir / "value.txt").write_text("1\n")

        records = run_trials(spec, invoke, work_root=tmp_path / "work")

        assert len(records) == 6  # 2 conditions x 3 trials
        trial_ids = {r.trial_id for r in records}
        assert trial_ids == {
            "naive_1",
            "naive_2",
            "naive_3",
            "verity_1",
            "verity_2",
            "verity_3",
        }
        # Every record has a real artifact hash -- the property three prior
        # pilots' trial directories lacked once they were destroyed.
        assert all(r.artifact_hash for r in records)

    def test_scorer_exit_code_is_ground_truth_not_agent_report(self, tmp_path):
        """The scorer, not `invoke_agent`, decides success -- an agent that
        claims success without actually changing anything must still fail."""
        fixture = _fixture(tmp_path)
        spec = TrialSpec(
            name="t",
            fixture_path=str(fixture),
            conditions=["claims_success"],
            n=1,
            scorer_command=_scorer_that_checks_value("1"),
        )

        def invoke_but_do_nothing(trial_dir, condition, index):
            pass  # an "agent" that changes nothing, unlike its own claim

        records = run_trials(spec, invoke_but_do_nothing, work_root=tmp_path / "work")

        assert records[0].scorer_exit_code != 0
        assert records[0].succeeded is False
        assert records[0].metrics["success"] == 0.0

    def test_fixture_itself_is_never_mutated(self, tmp_path):
        fixture = _fixture(tmp_path)
        spec = TrialSpec(
            name="t",
            fixture_path=str(fixture),
            conditions=["verity"],
            n=2,
            scorer_command=_scorer_that_checks_value("1"),
        )

        def invoke(trial_dir, condition, index):
            (trial_dir / "value.txt").write_text("1\n")

        run_trials(spec, invoke, work_root=tmp_path / "work")

        assert (fixture / "value.txt").read_text() == "0\n"

    def test_identical_trials_hash_identically(self, tmp_path):
        fixture = _fixture(tmp_path)
        spec = TrialSpec(
            name="t",
            fixture_path=str(fixture),
            conditions=["verity"],
            n=2,
            scorer_command=_scorer_that_checks_value("1"),
        )

        def invoke(trial_dir, condition, index):
            (trial_dir / "value.txt").write_text("same content\n")

        records = run_trials(spec, invoke, work_root=tmp_path / "work")

        assert records[0].artifact_hash == records[1].artifact_hash

    def test_custom_metric_fn_and_failure_classifier(self, tmp_path):
        fixture = _fixture(tmp_path)
        spec = TrialSpec(
            name="t",
            fixture_path=str(fixture),
            conditions=["verity"],
            n=1,
            scorer_command="true",
            metric_keys=["boundary_correct"],
        )

        def invoke(trial_dir, condition, index):
            (trial_dir / "value.txt").write_text("wrong\n")

        def metric_fn(trial_dir, exit_code):
            content = (trial_dir / "value.txt").read_text().strip()
            return {"boundary_correct": 1.0 if content == "right" else 0.0}

        def classify_failure(trial_dir, exit_code):
            content = (trial_dir / "value.txt").read_text().strip()
            return None if content == "right" else FailureMode.WRONG_BOUNDARY

        records = run_trials(
            spec,
            invoke,
            work_root=tmp_path / "work",
            metric_fn=metric_fn,
            classify_failure=classify_failure,
        )

        assert records[0].metrics == {"boundary_correct": 0.0}
        assert records[0].failure_mode is FailureMode.WRONG_BOUNDARY


class TestMetricsByCondition:
    def test_groups_by_condition_in_repetition_shape(self, tmp_path):
        fixture = _fixture(tmp_path)
        spec = TrialSpec(
            name="t",
            fixture_path=str(fixture),
            conditions=["naive", "verity"],
            n=2,
            scorer_command="true",
        )

        records = run_trials(spec, lambda *_: None, work_root=tmp_path / "work")
        grouped = metrics_by_condition(records)

        assert grouped["naive"] == [{"success": 1.0}, {"success": 1.0}]
        assert grouped["verity"] == [{"success": 1.0}, {"success": 1.0}]


class TestRunEval:
    def test_publishable_when_n_meets_minimum_and_floor_is_not_degenerate(self, tmp_path):
        fixture = _fixture(tmp_path)
        spec = TrialSpec(
            name="t",
            fixture_path=str(fixture),
            conditions=["naive", "verity"],
            n=5,
            scorer_command=_scorer_that_checks_value("1"),
        )
        outcomes = iter([0, 0, 0, 1, 1])  # naive: mixed results, real spread

        def invoke(trial_dir, condition, index):
            if condition == "naive":
                (trial_dir / "value.txt").write_text(f"{next(outcomes)}\n")
            else:
                (trial_dir / "value.txt").write_text("1\n")

        report = run_eval(
            spec,
            invoke,
            work_root=tmp_path / "work",
            evidence_root=tmp_path / "evidence",
        )

        assert report.is_publishable, report.warnings
        assert "success" in report.comparisons["verity"]

    def test_a_run_that_retained_nothing_is_not_publishable(self, tmp_path):
        """The gate ADR-0027 added. Before it, a run with no evidence root
        printed the same publishable-looking report as one that retained
        everything -- which is how three pilots' numbers outlived the only
        copy of their evidence. `n` and the noise floor are both fine here;
        the missing artifact alone must be disqualifying."""
        fixture = _fixture(tmp_path)
        spec = TrialSpec(
            name="t",
            fixture_path=str(fixture),
            conditions=["naive", "verity"],
            n=5,
            scorer_command=_scorer_that_checks_value("1"),
        )
        outcomes = iter([0, 0, 0, 1, 1])

        def invoke(trial_dir, condition, index):
            value = next(outcomes) if condition == "naive" else 1
            (trial_dir / "value.txt").write_text(f"{value}\n")

        report = run_eval(spec, invoke, work_root=tmp_path / "work")

        assert not report.is_publishable
        assert any("no evidence root" in w for w in report.warnings)
        assert report.evidence_root is None

    def test_not_publishable_below_minimum_n(self, tmp_path):
        fixture = _fixture(tmp_path)
        spec = TrialSpec(
            name="t",
            fixture_path=str(fixture),
            conditions=["naive", "verity"],
            n=2,
            scorer_command="true",
        )

        report = run_eval(spec, lambda *_: None, work_root=tmp_path / "work")

        assert not report.is_publishable
        assert any("n=2" in w for w in report.warnings)

    def test_degenerate_floor_produces_a_warning(self, tmp_path):
        """Every prior Family B pilot but one had a [0,0] or [1,1] floor for
        its headline metric (docs/MEASUREMENTS.md) -- this is the case the
        eval harness must flag rather than let read as conclusive."""
        fixture = _fixture(tmp_path)
        spec = TrialSpec(
            name="t",
            fixture_path=str(fixture),
            conditions=["naive", "verity"],
            n=5,
            scorer_command=_scorer_that_checks_value("1"),
        )

        def invoke(trial_dir, condition, index):
            (trial_dir / "value.txt").write_text("1\n" if condition == "verity" else "0\n")

        report = run_eval(spec, invoke, work_root=tmp_path / "work")

        assert not report.is_publishable
        assert any("degenerate" in w for w in report.warnings)
        assert report.comparisons["verity"]["success"]["conclusion"] == "likely_real_difference"
