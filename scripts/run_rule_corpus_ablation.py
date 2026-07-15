#!/usr/bin/env python3
"""T4: does hybrid-retrieval accuracy/confidence improve with more rules in
the corpus, or does it plateau well before the current 50-rule corpus?

Ablation via in-memory filtering, not physical KG mutation:
`SubsampledKGClient` wraps the real `KGClient` and filters
`get_rules_with_embeddings()` down to a fixed-seed random subset of N rule
IDs. The real Neo4j-stored corpus is never touched -- no `clear_all()`, no
re-ingestion, nothing to restore afterward. This is deliberately safer
than re-ingesting a smaller rule set per size, which would require wiping
and rebuilding the KG state other parts of the project (dashboard, CLI)
depend on.

Runs the same 28 benchmark tasks with retrieval_strategy=hybrid against
corpus sizes [10, 20, 30, 50] (50 = the full corpus). Per the noise-floor
finding in docs/PHASE_3_METHODOLOGY.md ("Analysis" section, 2026-07-15):
a single run per size cannot, on its own, distinguish a real corpus-size
effect from ordinary temperature=0.7 sampling noise -- treat any
conclusion here as provisional until checked against a repeat, the same
standing rule applied to the retry-loop and retrieval-strategy findings.

Checkpoints after every (task, size) pair, same crash-resilience pattern
as scripts/run_retrieval_ab.py.

Usage:
  python scripts/run_rule_corpus_ablation.py
  python scripts/run_rule_corpus_ablation.py --sizes 10,50 --tasks-limit 3
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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
from verityai.ontology.models import Rule, VerificationStatus  # noqa: E402

DEFAULT_SIZES = [10, 20, 30, 50]
SEED = 42

BENCHMARK_FILES = [
    "src/verityai/evaluation/benchmarks/correctness_benchmarks.json",
    "src/verityai/evaluation/benchmarks/security_benchmarks.json",
]


class SubsampledKGClient:
    """Wraps a real KGClient, filtering `get_rules_with_embeddings()` to a
    fixed subset of rule IDs. No Neo4j mutation -- the real corpus is
    never touched, so nothing needs restoring once the ablation finishes.
    """

    def __init__(self, real_kg_client: KGClient, rule_ids: set):
        self._real = real_kg_client
        self._rule_ids = rule_ids

    def get_rules_with_embeddings(
        self, language: str = "python"
    ) -> list[tuple[Rule, Optional[list[float]]]]:
        all_rules = self._real.get_rules_with_embeddings(language)
        return [(rule, emb) for rule, emb in all_rules if str(rule.id) in self._rule_ids]


def pick_rule_subset(real_kg_client: KGClient, size: int, seed: int = SEED) -> set:
    all_rules = real_kg_client.get_rules_with_embeddings("python")
    all_ids = sorted(str(rule.id) for rule, _ in all_rules)
    rng = random.Random(seed)
    chosen = rng.sample(all_ids, min(size, len(all_ids)))
    return set(chosen)


def load_all_tasks(repo_root: Path) -> list[BenchmarkTask]:
    tasks: list[BenchmarkTask] = []
    for rel_path in BENCHMARK_FILES:
        tasks.extend(load_benchmark_tasks(str(repo_root / rel_path)))
    return tasks


def _serialize(outcome: BenchmarkOutcome) -> dict[str, Any]:
    return {
        "task_id": outcome.task_id,
        "ground_truth": outcome.ground_truth,
        "status": outcome.predicted_status.value,
        "confidence": outcome.confidence,
        "latency_seconds": outcome.latency_seconds,
        "attempts": outcome.attempts,
    }


def _deserialize(data: dict[str, Any]) -> BenchmarkOutcome:
    return BenchmarkOutcome(
        task_id=data["task_id"],
        ground_truth=data["ground_truth"],
        predicted_status=VerificationStatus(data["status"]),
        confidence=data["confidence"],
        latency_seconds=data["latency_seconds"],
        attempts=data["attempts"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        default=",".join(str(s) for s in DEFAULT_SIZES),
        help=f"Comma-separated corpus sizes to test, default {DEFAULT_SIZES}",
    )
    parser.add_argument(
        "--tasks-limit", type=int, default=None, help="Limit number of tasks (smoke test)"
    )
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", "llama3.2"))
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Load an existing checkpoint for today's date and skip (task, size) pairs already done",
    )
    args = parser.parse_args()

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]

    repo_root = Path(__file__).parent.parent
    tasks = load_all_tasks(repo_root)
    if args.tasks_limit:
        tasks = tasks[: args.tasks_limit]

    print(f"Loaded {len(tasks)} tasks, corpus sizes={sizes}, model={args.model}")

    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "neo4jpassword")
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    real_kg_client = KGClient(driver)
    llm_client = OllamaClient(
        model=args.model,
        base_url=ollama_host,
        embed_model=os.environ.get("OLLAMA_EMBED_MODEL"),
    )

    raw_outcomes: dict[int, list[BenchmarkOutcome]] = {size: [] for size in sizes}
    rule_counts: dict[int, int] = {}
    errors: list[dict] = []
    done_pairs: set[tuple[str, int]] = set()

    output_dir = repo_root / "docs" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = output_dir / f"{date_str}_corpus_ablation.json"

    if args.resume and output_path.exists():
        previous = json.loads(output_path.read_text())
        for size_str, outcomes in previous.get("results", {}).items():
            size = int(size_str)
            if size not in raw_outcomes:
                continue
            for outcome_data in outcomes:
                raw_outcomes[size].append(_deserialize(outcome_data))
                done_pairs.add((outcome_data["task_id"], size))
        rule_counts.update({int(k): v for k, v in previous.get("actual_rule_counts", {}).items()})
        errors.extend(previous.get("errors", []))
        print(f"Resumed: {len(done_pairs)} (task, size) pairs already done, skipping those.")

    def checkpoint() -> None:
        payload = {
            "model": args.model,
            "sizes": sizes,
            "seed": SEED,
            "actual_rule_counts": rule_counts,
            "total_tasks": len(tasks),
            "results": {str(size): [_serialize(o) for o in raw_outcomes[size]] for size in sizes},
            "errors": errors,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        output_path.write_text(json.dumps(payload, indent=2))

    total_pairs = len(tasks) * len(sizes)
    done = 0
    start_all = time.monotonic()

    for size in sizes:
        rule_ids = pick_rule_subset(real_kg_client, size)
        rule_counts[size] = len(rule_ids)
        subsampled_client = SubsampledKGClient(real_kg_client, rule_ids)
        print(f"\n=== corpus size {size} ({len(rule_ids)} rules selected) ===")

        for task in tasks:
            done += 1
            if (task.id, size) in done_pairs:
                print(f"[{done}/{total_pairs}] {task.id} / size={size} ... skipped (resumed)")
                continue
            print(f"[{done}/{total_pairs}] {task.id} / size={size} ...", flush=True)
            try:
                outcome = run_verityai_full_baseline(
                    llm_client, task, kg_client=subsampled_client, retrieval_strategy="hybrid"
                )
                raw_outcomes[size].append(outcome)
                print(
                    f"    -> {outcome.predicted_status.value}, "
                    f"conf={outcome.confidence:.2f}, {outcome.latency_seconds:.1f}s, "
                    f"attempts={outcome.attempts}"
                )
            except Exception as e:  # noqa: BLE001 -- one bad pair shouldn't abort the run
                print(f"    !! ERROR: {e}")
                errors.append({"task": task.id, "size": size, "error": str(e)})
            checkpoint()

    elapsed_all = time.monotonic() - start_all
    print(f"\nDone in {elapsed_all / 60:.1f} min. Wrote {output_path}")

    for size in sizes:
        if raw_outcomes[size]:
            metrics = compute_classification_metrics(raw_outcomes[size])
            print(f"\nsize={size} ({len(raw_outcomes[size])} outcomes): {metrics}")

    llm_client.close()
    driver.close()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
