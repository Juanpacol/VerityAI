#!/usr/bin/env python3
"""Validates the repeated-run infrastructure (evaluation/repetition.py)
against a real repeat, and re-checks the retry-loop/KG-context findings
with a same-day, same-script control -- stronger than the cross-day
comparison the original noise-floor finding relied on (see
docs/PHASE_3_METHODOLOGY.md's "Analysis" section and its standing rule:
no metric difference gets attributed to a mechanism without checking a
same-configuration repeat first).

Repeats ONE arm (default: hybrid_kg) R times over the same task subset,
then reports pairwise ground-truth agreement + per-metric variance across
the repeats via evaluation/repetition.py -- the first same-day,
same-script repeat this project has run (every prior noise-floor
comparison used two independently-run scripts on different days).

Usage:
  python scripts/run_repeat_validation.py
  python scripts/run_repeat_validation.py --repeats 2 --tasks-limit 10 --arm hybrid_kg
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from neo4j import GraphDatabase  # noqa: E402

from verityai.evaluation.baselines import (  # noqa: E402
    BenchmarkOutcome,
    BenchmarkTask,
    load_benchmark_tasks,
    run_verityai_full_baseline,
)
from verityai.evaluation.repetition import (  # noqa: E402
    pairwise_agreement_summary,
    summarize_metric_variance,
)
from verityai.kg.client import KGClient  # noqa: E402
from verityai.neural.ollama_client import OllamaClient  # noqa: E402

BENCHMARK_FILES = [
    "src/verityai/evaluation/benchmarks/correctness_benchmarks.json",
    "src/verityai/evaluation/benchmarks/security_benchmarks.json",
]

# From docs/PHASE_3_METHODOLOGY.md's Analysis section -- the cross-day
# noise floor established there, used as a prior comparison point since
# it's the only other same-configuration check this project has run.
ESTABLISHED_CROSS_DAY_NOISE_FLOOR = 0.692


def load_all_tasks(repo_root: Path) -> list[BenchmarkTask]:
    tasks: list[BenchmarkTask] = []
    for rel_path in BENCHMARK_FILES:
        tasks.extend(load_benchmark_tasks(str(repo_root / rel_path)))
    return tasks


def _serialize(outcome: BenchmarkOutcome) -> dict:
    return {
        "task_id": outcome.task_id,
        "ground_truth": outcome.ground_truth,
        "status": outcome.predicted_status.value,
        "confidence": outcome.confidence,
        "latency_seconds": outcome.latency_seconds,
        "attempts": outcome.attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--tasks-limit", type=int, default=10)
    parser.add_argument("--arm", choices=["no_kg", "legacy_kg", "hybrid_kg"], default="hybrid_kg")
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", "llama3.2"))
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    tasks = load_all_tasks(repo_root)[: args.tasks_limit]

    print(f"Repeating arm={args.arm} x{args.repeats} over {len(tasks)} tasks, model={args.model}")

    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "neo4jpassword")
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    kg_client = KGClient(driver) if args.arm != "no_kg" else None
    llm_client = OllamaClient(
        model=args.model, base_url=ollama_host, embed_model=os.environ.get("OLLAMA_EMBED_MODEL")
    )
    retrieval_strategy = "hybrid" if args.arm == "hybrid_kg" else "legacy"

    output_dir = repo_root / "docs" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = output_dir / f"{date_str}_repeat_validation.json"

    repeats: list[list[BenchmarkOutcome]] = []
    errors: list[dict] = []

    def checkpoint() -> None:
        payload = {
            "model": args.model,
            "arm": args.arm,
            "n_repeats_completed": len(repeats),
            "n_repeats_target": args.repeats,
            "tasks_limit": args.tasks_limit,
            "repeats": [[_serialize(o) for o in r] for r in repeats],
            "errors": errors,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        output_path.write_text(json.dumps(payload, indent=2))

    start_all = time.monotonic()
    for repeat_idx in range(args.repeats):
        print(f"\n=== repeat {repeat_idx + 1}/{args.repeats} ===")
        outcomes = []
        for i, task in enumerate(tasks, start=1):
            print(f"  [{i}/{len(tasks)}] {task.id} ...", flush=True)
            try:
                outcome = run_verityai_full_baseline(
                    llm_client,
                    task,
                    kg_client=kg_client,
                    retrieval_strategy=retrieval_strategy,
                )
                outcomes.append(outcome)
                print(f"      -> {outcome.predicted_status.value}, conf={outcome.confidence:.2f}")
            except Exception as e:  # noqa: BLE001
                print(f"      !! ERROR: {e}")
                errors.append({"repeat": repeat_idx, "task": task.id, "error": str(e)})
        repeats.append(outcomes)
        checkpoint()

    elapsed = time.monotonic() - start_all
    print(f"\nDone in {elapsed / 60:.1f} min. Wrote {output_path}")

    if len(repeats) >= 2:
        agreement = pairwise_agreement_summary(repeats)
        variance = summarize_metric_variance(repeats)
        print("\n=== Pairwise agreement across repeats (this run, same day/script) ===")
        print(json.dumps(agreement, indent=2))
        print("\n=== Metric variance across repeats ===")
        print(json.dumps(variance, indent=2))
        # Both this run's mean and ESTABLISHED_CROSS_DAY_NOISE_FLOOR measure the
        # SAME quantity (agreement between repeats of one config) via different
        # methods (same-day/same-script here, cross-day/cross-script there) --
        # this is a cross-validation of the noise-floor ESTIMATE itself, not the
        # is_difference_significant_vs_noise comparison (which is for checking a
        # DIFFERENT config's agreement rate against a within-config range; see
        # evaluation/repetition.py). Reported side by side, not run through that
        # function, to avoid conflating the two different kinds of comparison.
        this_run_mean = agreement["ground_truth_agreement_rate_mean"]
        print("\n=== Cross-validating the noise-floor estimate itself ===")
        print(
            json.dumps(
                {
                    "this_run_same_day_same_script_mean": this_run_mean,
                    "established_cross_day_cross_script": ESTABLISHED_CROSS_DAY_NOISE_FLOOR,
                    "difference": (
                        round(abs(this_run_mean - ESTABLISHED_CROSS_DAY_NOISE_FLOOR), 3)
                        if this_run_mean is not None
                        else None
                    ),
                },
                indent=2,
            )
        )

    llm_client.close()
    driver.close()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
