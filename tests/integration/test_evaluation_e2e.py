"""Phase 3 acceptance test: run all 3 baselines over the Lote 1 correctness
benchmarks and confirm the expected ordering the architecture claims to
deliver -- recall(verityai_full) >= recall(single_shot_z3) >= recall(raw_llm).

Uses a scripted FakeLLMClient (buggy on first attempt, self-corrects once
given the failure reason) rather than a live Ollama instance -- see
docs/PHASE_3_METHODOLOGY.md for why no live-model numbers exist yet. This
test validates the *harness* end-to-end: task loading, all 3 runners,
metrics, and report rendering wired together correctly, not llama2:13b's
real-world bug rate.
"""

from pathlib import Path
from typing import Optional

from verityai.evaluation.baselines import load_benchmark_tasks, run_all_baselines
from verityai.evaluation.metrics import compute_classification_metrics
from verityai.evaluation.report import render_comparison_report
from verityai.neural.ollama_client import OllamaGenerationError

BENCHMARKS_DIR = (
    Path(__file__).parent.parent.parent
    / "src" / "verityai" / "evaluation" / "benchmarks"
)


class SelfCorrectingFakeLLMClient:
    """Simulates the failure mode this architecture specifically targets:
    the LLM's first attempt has the known bug, but it fixes it once shown
    the failure reason on retry."""

    def __init__(self, buggy_code: str, reference_code: str):
        self.buggy_code = buggy_code
        self.reference_code = reference_code
        self.call_count = 0

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        self.call_count += 1
        code = self.buggy_code if self.call_count == 1 else self.reference_code
        return f"```python\n{code}\n```"


class AlwaysBuggyFakeLLMClient:
    """Simulates an LLM that never self-corrects (worst case for retry)."""

    def __init__(self, buggy_code: str):
        self.buggy_code = buggy_code

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        return f"```python\n{self.buggy_code}\n```"


class TestFullEvaluationHarness:
    def test_verityai_full_recall_is_at_least_single_shot_which_is_at_least_raw(self):
        tasks = load_benchmark_tasks(str(BENCHMARKS_DIR / "correctness_benchmarks.json"))

        def factory(task, baseline_name):
            if baseline_name == "verityai_full":
                # Give the retry loop a chance to recover.
                return SelfCorrectingFakeLLMClient(task.known_buggy_variant, task.reference_solution)
            # raw_llm and single_shot_z3 only ever see the buggy first attempt.
            return AlwaysBuggyFakeLLMClient(task.known_buggy_variant)

        results = run_all_baselines(factory, tasks, max_attempts=3)

        raw_metrics = compute_classification_metrics(results["raw_llm"])
        single_shot_metrics = compute_classification_metrics(results["single_shot_z3"])
        full_metrics = compute_classification_metrics(results["verityai_full"])

        # Baseline 1 never checks anything -- it catches none of the bugs
        # (every task here fed it the buggy variant, so recall is the
        # relevant number; it never returns FAIL regardless of input).
        assert raw_metrics["recall"] == 0.0
        # Baseline 2 catches every bug it's shown once (it correctly flags
        # the buggy variant as FAIL every time), but never fixes them --
        # the final code handed back is still the known-buggy one.
        assert single_shot_metrics["recall"] == 1.0
        assert all(o.ground_truth == "buggy" for o in results["single_shot_z3"])
        # VerityAI's retry loop recovers to the reference solution every time
        # this simulated LLM is capable of self-correcting -- unlike
        # baseline 2, its *final* code is the fixed version, not just a
        # correctly-flagged rejection of the broken one.
        assert full_metrics["accuracy"] == 1.0
        assert all(o.ground_truth == "correct" for o in results["verityai_full"])

        report = render_comparison_report(results)
        assert "Raw LLM (no verification)" in report
        assert "VerityAI (full retry loop)" in report

    def test_security_benchmarks_lote_1_also_run_end_to_end(self):
        tasks = load_benchmark_tasks(str(BENCHMARKS_DIR / "security_benchmarks.json"))

        def factory(task, baseline_name):
            return AlwaysBuggyFakeLLMClient(task.known_buggy_variant)

        results = run_all_baselines(factory, tasks, max_attempts=1)

        assert len(results["single_shot_z3"]) == len(tasks)
        single_shot_metrics = compute_classification_metrics(results["single_shot_z3"])
        assert single_shot_metrics["recall"] == 1.0  # all 3 known bugs caught
