"""Three baseline configurations compared in Phase 3, per the architecture plan:

1. raw_llm       -- LLM output trusted as-is, no verification at all.
2. single_shot_z3 -- LLM generates once, Z3 checks once, no retry.
3. verityai_full  -- Orchestrator's full generate-verify-retry loop.

Each runner works against any object exposing `.generate(prompt) -> str`
(OllamaClient, or a scripted test double), so the exact same code path
that runs here against a fake in tests also runs against a live Ollama
instance -- see docs/PHASE_3_METHODOLOGY.md for why no live-model numbers
ship with this repo yet.

Ground truth is determined *after* generation (classify_ground_truth),
not assigned in advance: a live LLM won't reliably reproduce either fixed
benchmark string verbatim, so "did it produce the reference solution, the
known bug, or something else entirely" has to be checked against what it
actually output.
"""

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from verityai.agent.orchestrator import Orchestrator
from verityai.evaluation.metrics import BenchmarkOutcome
from verityai.neural.response_parsing import split_code_and_reasoning
from verityai.ontology.models import GenerationRequest, VerificationStatus
from verityai.symbolic.verify import verify_python_snippet


@dataclass
class BenchmarkTask:
    id: str
    prompt: str
    category: str
    bug_class: str
    reference_solution: str
    known_buggy_variant: str


def load_benchmark_tasks(json_path: str) -> list[BenchmarkTask]:
    """Load benchmark tasks from a JSON file (see evaluation/benchmarks/)."""
    with open(json_path) as f:
        raw_tasks = json.load(f)

    return [
        BenchmarkTask(
            id=t["id"],
            prompt=t["prompt"],
            category=t["category"],
            bug_class=t["bug_class"],
            reference_solution=t["reference_solution"],
            known_buggy_variant=t["known_buggy_variant"],
        )
        for t in raw_tasks
    ]


def classify_ground_truth(task: BenchmarkTask, code: str) -> str:
    """Classify generated code as "correct", "buggy", or "novel".

    "novel" means the code matched neither known variant -- there's no
    oracle for it, so it can't be scored as right or wrong (see
    evaluation/metrics.py for how "novel" is excluded from the confusion
    matrix rather than silently miscounted).
    """
    normalized = code.strip()
    if normalized == task.reference_solution.strip():
        return "correct"
    if normalized == task.known_buggy_variant.strip():
        return "buggy"
    return "novel"


def run_raw_llm_baseline(llm_client: Any, task: BenchmarkTask) -> BenchmarkOutcome:
    """Baseline 1: trust whatever the LLM outputs, no verification at all.

    Confidence is reported as a flat 1.0 -- this baseline has no
    calibrated notion of confidence since it never checks anything;
    that flat number itself is the point being illustrated.
    """
    start = time.monotonic()
    raw = llm_client.generate(task.prompt)
    code, _ = split_code_and_reasoning(raw)
    elapsed = time.monotonic() - start

    return BenchmarkOutcome(
        task_id=task.id,
        ground_truth=classify_ground_truth(task, code),
        predicted_status=VerificationStatus.PASS,
        confidence=1.0,
        latency_seconds=elapsed,
        attempts=1,
    )


def run_single_shot_z3_baseline(
    llm_client: Any, task: BenchmarkTask, timeout_seconds: float = 3.0
) -> BenchmarkOutcome:
    """Baseline 2: LLM generates once, Z3 checks once, no retry."""
    start = time.monotonic()
    raw = llm_client.generate(task.prompt)
    code, _ = split_code_and_reasoning(raw)
    result = verify_python_snippet(code, timeout_seconds=timeout_seconds)
    elapsed = time.monotonic() - start

    return BenchmarkOutcome(
        task_id=task.id,
        ground_truth=classify_ground_truth(task, code),
        predicted_status=result.status,
        confidence=result.confidence,
        latency_seconds=elapsed,
        attempts=1,
    )


def run_verityai_full_baseline(
    llm_client: Any,
    task: BenchmarkTask,
    max_attempts: int = 3,
    timeout_seconds: float = 3.0,
) -> BenchmarkOutcome:
    """Baseline 3: VerityAI's full generate-verify-retry loop."""
    start = time.monotonic()
    orchestrator = Orchestrator(llm_client=llm_client, z3_timeout_seconds=timeout_seconds)
    response = orchestrator.run(GenerationRequest(prompt=task.prompt, max_attempts=max_attempts))
    elapsed = time.monotonic() - start

    return BenchmarkOutcome(
        task_id=task.id,
        ground_truth=classify_ground_truth(task, response.code),
        predicted_status=response.final_verification.status,
        confidence=response.confidence,
        latency_seconds=elapsed,
        attempts=len(response.traces),
    )


def run_all_baselines(
    llm_client_factory: Callable[[BenchmarkTask, str], Any],
    tasks: list[BenchmarkTask],
    max_attempts: int = 3,
    timeout_seconds: float = 3.0,
) -> dict[str, list[BenchmarkOutcome]]:
    """Run all 3 baselines over `tasks`.

    Args:
        llm_client_factory: (task, baseline_name) -> llm_client. Called
            fresh for each (task, baseline) pair rather than sharing one
            client, since a scripted test double's response queue for one
            baseline shouldn't be consumed by another. A real Ollama-backed
            factory can just ignore the arguments and return the same
            shared client every time.
        tasks: Benchmark tasks to run (see load_benchmark_tasks).

    Returns:
        {"raw_llm": [...], "single_shot_z3": [...], "verityai_full": [...]}
    """
    results: dict[str, list[BenchmarkOutcome]] = {
        "raw_llm": [],
        "single_shot_z3": [],
        "verityai_full": [],
    }

    for task in tasks:
        results["raw_llm"].append(run_raw_llm_baseline(llm_client_factory(task, "raw_llm"), task))
        results["single_shot_z3"].append(
            run_single_shot_z3_baseline(
                llm_client_factory(task, "single_shot_z3"), task, timeout_seconds
            )
        )
        results["verityai_full"].append(
            run_verityai_full_baseline(
                llm_client_factory(task, "verityai_full"), task, max_attempts, timeout_seconds
            )
        )

    return results
