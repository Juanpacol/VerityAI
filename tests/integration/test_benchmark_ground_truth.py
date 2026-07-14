"""Regression test: every benchmark's ground truth must hold against the
real verifier, not just at the moment the dataset was written.

This is the permanent form of the throwaway validation script used while
building each Lote — if a future change to ast_to_smt.py/verify.py shifts
behavior, this catches a benchmark whose reference_solution stops PASSing
or whose known_buggy_variant stops FAILing, before it silently corrupts
every metric computed from it.
"""

from pathlib import Path

import pytest

from verityai.evaluation.baselines import load_benchmark_tasks
from verityai.ontology.models import VerificationStatus
from verityai.symbolic.verify import verify_python_snippet

BENCHMARKS_DIR = (
    Path(__file__).parent.parent.parent / "src" / "verityai" / "evaluation" / "benchmarks"
)

ALL_TASKS = load_benchmark_tasks(
    str(BENCHMARKS_DIR / "correctness_benchmarks.json")
) + load_benchmark_tasks(str(BENCHMARKS_DIR / "security_benchmarks.json"))


class TestBenchmarkGroundTruth:
    @pytest.mark.parametrize("task", ALL_TASKS, ids=[t.id for t in ALL_TASKS])
    def test_reference_solution_verifies_pass(self, task):
        result = verify_python_snippet(task.reference_solution)
        assert result.status == VerificationStatus.PASS, (
            f"{task.id}'s reference_solution no longer verifies PASS "
            f"(got {result.status.value}) -- ground truth is now wrong"
        )

    @pytest.mark.parametrize("task", ALL_TASKS, ids=[t.id for t in ALL_TASKS])
    def test_known_buggy_variant_verifies_fail(self, task):
        result = verify_python_snippet(task.known_buggy_variant)
        assert result.status == VerificationStatus.FAIL, (
            f"{task.id}'s known_buggy_variant no longer verifies FAIL "
            f"(got {result.status.value}) -- ground truth is now wrong"
        )
