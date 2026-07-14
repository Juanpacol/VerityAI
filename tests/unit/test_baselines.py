"""Unit tests for evaluation/baselines.py."""

from pathlib import Path

from tests.fakes import FakeLLMClient, wrap_code
from verityai.evaluation.baselines import (
    BenchmarkTask,
    classify_ground_truth,
    load_benchmark_tasks,
    run_all_baselines,
    run_raw_llm_baseline,
    run_single_shot_z3_baseline,
    run_verityai_full_baseline,
)
from verityai.ontology.models import VerificationStatus

BENCHMARKS_DIR = (
    Path(__file__).parent.parent.parent / "src" / "verityai" / "evaluation" / "benchmarks"
)

REFERENCE = "def add_two():\n    a = 3\n    b = 4\n    result = a + b\n    assert result == 7\n    return result\n"
BUGGY = "def add_two():\n    a = 3\n    b = 4\n    result = a - b\n    assert result == 7\n    return result\n"

TASK = BenchmarkTask(
    id="t1",
    prompt="add two numbers",
    category="correctness",
    bug_class="wrong_operator",
    reference_solution=REFERENCE,
    known_buggy_variant=BUGGY,
)


class TestClassifyGroundTruth:
    def test_matches_reference_is_correct(self):
        assert classify_ground_truth(TASK, REFERENCE) == "correct"

    def test_matches_buggy_variant_is_buggy(self):
        assert classify_ground_truth(TASK, BUGGY) == "buggy"

    def test_matches_neither_is_novel(self):
        assert classify_ground_truth(TASK, "def something_else():\n    pass\n") == "novel"

    def test_whitespace_differences_still_match(self):
        assert classify_ground_truth(TASK, "  " + REFERENCE + "  \n") == "correct"


class TestLoadBenchmarkTasks:
    def test_loads_correctness_benchmarks(self):
        tasks = load_benchmark_tasks(str(BENCHMARKS_DIR / "correctness_benchmarks.json"))
        assert len(tasks) == 22  # Lote 1 (12) + Lote 2 (10)
        assert all(t.category == "correctness" for t in tasks)
        assert all(t.reference_solution and t.known_buggy_variant for t in tasks)

    def test_loads_security_benchmarks(self):
        tasks = load_benchmark_tasks(str(BENCHMARKS_DIR / "security_benchmarks.json"))
        assert len(tasks) == 6  # Lote 1 (3) + Lote 2 (3)
        assert all(t.category == "security" for t in tasks)


class TestRunRawLlmBaseline:
    def test_never_flags_anything_regardless_of_ground_truth(self):
        llm = FakeLLMClient([wrap_code(BUGGY)])
        outcome = run_raw_llm_baseline(llm, TASK)

        assert outcome.predicted_status == VerificationStatus.PASS
        assert outcome.ground_truth == "buggy"  # it IS buggy, baseline just never checks
        assert outcome.attempts == 1


class TestRunSingleShotZ3Baseline:
    def test_catches_the_bug_when_llm_produces_buggy_code(self):
        llm = FakeLLMClient([wrap_code(BUGGY)])
        outcome = run_single_shot_z3_baseline(llm, TASK)

        assert outcome.predicted_status == VerificationStatus.FAIL
        assert outcome.ground_truth == "buggy"

    def test_passes_correct_code(self):
        llm = FakeLLMClient([wrap_code(REFERENCE)])
        outcome = run_single_shot_z3_baseline(llm, TASK)

        assert outcome.predicted_status == VerificationStatus.PASS
        assert outcome.ground_truth == "correct"

    def test_does_not_retry_even_when_buggy(self):
        llm = FakeLLMClient([wrap_code(BUGGY)])
        run_single_shot_z3_baseline(llm, TASK)
        assert llm.call_count == 1


class TestRunVerityaiFullBaseline:
    def test_recovers_when_llm_self_corrects_on_retry(self):
        """Simulates an LLM whose first attempt has the known bug, but which
        fixes it once the failure reason is fed back -- the scenario this
        architecture's retry loop specifically targets."""
        llm = FakeLLMClient([wrap_code(BUGGY), wrap_code(REFERENCE)])
        outcome = run_verityai_full_baseline(llm, TASK, max_attempts=3)

        assert outcome.predicted_status == VerificationStatus.PASS
        assert outcome.ground_truth == "correct"
        assert outcome.attempts == 2

    def test_exhausts_retries_when_llm_never_corrects(self):
        llm = FakeLLMClient([wrap_code(BUGGY)] * 3)
        outcome = run_verityai_full_baseline(llm, TASK, max_attempts=3)

        assert outcome.predicted_status == VerificationStatus.FAIL
        assert outcome.ground_truth == "buggy"
        assert outcome.attempts == 3


class TestRunAllBaselines:
    def test_runs_all_three_baselines_over_every_task(self):
        tasks = [TASK]

        def factory(task, baseline_name):
            if baseline_name == "verityai_full":
                return FakeLLMClient([wrap_code(BUGGY), wrap_code(REFERENCE)])
            return FakeLLMClient([wrap_code(BUGGY)])

        results = run_all_baselines(factory, tasks)

        assert set(results.keys()) == {"raw_llm", "single_shot_z3", "verityai_full"}
        assert len(results["raw_llm"]) == 1
        assert len(results["single_shot_z3"]) == 1
        assert len(results["verityai_full"]) == 1
        assert results["verityai_full"][0].predicted_status == VerificationStatus.PASS
