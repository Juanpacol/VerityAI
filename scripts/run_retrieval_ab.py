#!/usr/bin/env python3
"""Three-arm retrieval A/B: no_kg vs legacy_kg vs hybrid_kg.

Real run #1 (docs/PHASE_3_METHODOLOGY.md) found that the existing
"verityai_full" baseline never actually connected a kg_client -- it has
always been a `no_kg` arm, not a genuine "full VerityAI" arm. Without all
three arms, "hybrid retrieval wins" can't be told apart from "any KG
context helps or hurts a 3B model" (see docs/adr/0003-hybrid-retrieval.md).

Checkpoints to docs/results/<date>_retrieval_ab.json after every single
(task, arm) pair, not just at the end -- the qwen3:8b cross-model run
(Real run #2) crashed partway through and would have lost all progress
without this. Run against a real Ollama instance + real Neo4j; this is not
mocked/offline (see tests/unit/test_baselines.py for the offline-safe
default-arguments coverage of run_verityai_full_baseline itself).

Usage:
  python scripts/run_retrieval_ab.py                     # all 3 arms, all tasks
  python scripts/run_retrieval_ab.py --arms no_kg,hybrid_kg
  python scripts/run_retrieval_ab.py --tasks-limit 3      # smoke test
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
from verityai.evaluation.metrics import compute_classification_metrics  # noqa: E402
from verityai.kg.client import KGClient  # noqa: E402
from verityai.neural.ollama_client import OllamaClient  # noqa: E402

ARMS = ["no_kg", "legacy_kg", "hybrid_kg"]

BENCHMARK_FILES = [
    "src/verityai/evaluation/benchmarks/correctness_benchmarks.json",
    "src/verityai/evaluation/benchmarks/security_benchmarks.json",
]


def load_all_tasks(repo_root: Path) -> list[BenchmarkTask]:
    tasks: list[BenchmarkTask] = []
    for rel_path in BENCHMARK_FILES:
        tasks.extend(load_benchmark_tasks(str(repo_root / rel_path)))
    return tasks


def run_arm(arm: str, task: BenchmarkTask, llm_client, kg_client) -> BenchmarkOutcome:
    if arm == "no_kg":
        return run_verityai_full_baseline(llm_client, task)
    if arm == "legacy_kg":
        return run_verityai_full_baseline(
            llm_client, task, kg_client=kg_client, retrieval_strategy="legacy"
        )
    if arm == "hybrid_kg":
        return run_verityai_full_baseline(
            llm_client, task, kg_client=kg_client, retrieval_strategy="hybrid"
        )
    raise ValueError(f"Unknown arm: {arm}")


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
    parser.add_argument(
        "--arms",
        default=",".join(ARMS),
        help=f"Comma-separated subset of: {','.join(ARMS)}",
    )
    parser.add_argument(
        "--tasks-limit", type=int, default=None, help="Limit number of tasks (smoke test)"
    )
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", "llama3.2"))
    args = parser.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for arm in arms:
        if arm not in ARMS:
            print(f"Unknown arm {arm!r}, must be one of {ARMS}")
            return 1

    repo_root = Path(__file__).parent.parent
    tasks = load_all_tasks(repo_root)
    if args.tasks_limit:
        tasks = tasks[: args.tasks_limit]

    print(f"Loaded {len(tasks)} tasks, arms={arms}, model={args.model}")

    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "neo4jpassword")
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    kg_client = KGClient(driver)
    llm_client = OllamaClient(
        model=args.model,
        base_url=ollama_host,
        embed_model=os.environ.get("OLLAMA_EMBED_MODEL"),
    )

    raw_outcomes: dict[str, list[BenchmarkOutcome]] = {arm: [] for arm in arms}
    errors: list[dict] = []

    output_dir = repo_root / "docs" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = output_dir / f"{date_str}_retrieval_ab.json"

    def checkpoint() -> None:
        payload = {
            "model": args.model,
            "arms": arms,
            "total_tasks": len(tasks),
            "results": {arm: [_serialize(o) for o in raw_outcomes[arm]] for arm in arms},
            "errors": errors,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        output_path.write_text(json.dumps(payload, indent=2))

    total_pairs = len(tasks) * len(arms)
    done = 0
    start_all = time.monotonic()

    for task in tasks:
        for arm in arms:
            done += 1
            print(f"[{done}/{total_pairs}] {task.id} / {arm} ...", flush=True)
            try:
                outcome = run_arm(arm, task, llm_client, kg_client)
                raw_outcomes[arm].append(outcome)
                print(
                    f"    -> {outcome.predicted_status.value}, "
                    f"conf={outcome.confidence:.2f}, {outcome.latency_seconds:.1f}s, "
                    f"attempts={outcome.attempts}"
                )
            except Exception as e:
                print(f"    !! ERROR: {e}")
                errors.append({"task": task.id, "arm": arm, "error": str(e)})
            checkpoint()

    elapsed_all = time.monotonic() - start_all
    print(f"\nDone in {elapsed_all / 60:.1f} min. Wrote {output_path}")

    for arm in arms:
        if raw_outcomes[arm]:
            metrics = compute_classification_metrics(raw_outcomes[arm])
            print(f"\n{arm} ({len(raw_outcomes[arm])} outcomes): {metrics}")

    llm_client.close()
    driver.close()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
