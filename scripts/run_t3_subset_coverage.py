#!/usr/bin/env python3
"""T3: what fraction of realistic code falls inside VerityAI's verifiable
subset (ADR-0001)?

Fetches HumanEval + MBPP (or reuses what's already in docs/evidence/ from
a prior run), classifies every problem's canonical solution through the
REAL verifier (symbolic.ast_to_smt.ASTtoSMTConverter, via
evidence.subset_classifier.classify_problem -- ground truth, not an
approximation), and tabulates in/out-of-subset counts plus an exclusion-
category histogram per dataset. Answers Fase 2 of the T1-T6 research
roadmap directly.

Usage:
  python scripts/run_t3_subset_coverage.py
  python scripts/run_t3_subset_coverage.py --limit 50   # smoke test
  python scripts/run_t3_subset_coverage.py --skip-fetch  # reuse stored evidence only
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from verityai.evidence.fetchers.base import Checkpoint  # noqa: E402
from verityai.evidence.fetchers.benchmarks import fetch_humaneval, fetch_mbpp  # noqa: E402
from verityai.evidence.store import EvidenceStore  # noqa: E402
from verityai.evidence.subset_classifier import classify_problem  # noqa: E402
from verityai.evidence.validation import validate_record  # noqa: E402


def _reconstruct_code(source: str, content: dict) -> str:
    if source == "humaneval":
        return str(content["prompt"]) + str(content["canonical_solution"])
    return str(content["code"])


def _tabulate(store: EvidenceStore, source: str) -> dict:
    members = 0
    non_members = 0
    category_counts: Counter = Counter()
    per_problem = []

    for record in store.iter_records(source=source):
        code = _reconstruct_code(source, record.content)
        classification = classify_problem(code)
        if classification.subset_member:
            members += 1
        else:
            non_members += 1
            category_counts.update(classification.exclusion_categories)
        per_problem.append(
            {
                "id": record.id,
                "subset_member": classification.subset_member,
                "exclusion_categories": classification.exclusion_categories,
            }
        )

    total = members + non_members
    return {
        "total": total,
        "subset_members": members,
        "non_members": non_members,
        "coverage_rate": (members / total) if total else None,
        "exclusion_category_histogram": dict(category_counts.most_common()),
        "per_problem": per_problem,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Limit problems per dataset")
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Reuse whatever's already stored in docs/evidence/ instead of fetching",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    evidence_root = repo_root / "docs" / "evidence"
    store = EvidenceStore(evidence_root)

    if not args.skip_fetch:
        print("Fetching HumanEval...")
        he_checkpoint = Checkpoint(evidence_root / ".checkpoints" / "humaneval.json")
        he_result = fetch_humaneval(checkpoint=he_checkpoint, limit=args.limit)
        for record in he_result.records:
            record.validation = validate_record(
                record, known_hashes=store.known_hashes() - {record.content_hash}
            )
            store.save(record)
        print(f"  {len(he_result.records)} fetched, {len(he_result.errors)} errors")

        print("Fetching MBPP...")
        mbpp_checkpoint = Checkpoint(evidence_root / ".checkpoints" / "mbpp.json")
        mbpp_result = fetch_mbpp(checkpoint=mbpp_checkpoint, limit=args.limit)
        for record in mbpp_result.records:
            record.validation = validate_record(
                record, known_hashes=store.known_hashes() - {record.content_hash}
            )
            store.save(record)
        print(f"  {len(mbpp_result.records)} fetched, {len(mbpp_result.errors)} errors")

    print("Classifying HumanEval against the verifiable subset...")
    humaneval_report = _tabulate(store, "humaneval")
    print(
        f"  {humaneval_report['subset_members']}/{humaneval_report['total']} in subset "
        f"({(humaneval_report['coverage_rate'] or 0) * 100:.1f}%)"
    )

    print("Classifying MBPP against the verifiable subset...")
    mbpp_report = _tabulate(store, "mbpp")
    print(
        f"  {mbpp_report['subset_members']}/{mbpp_report['total']} in subset "
        f"({(mbpp_report['coverage_rate'] or 0) * 100:.1f}%)"
    )

    output_dir = repo_root / "docs" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = output_dir / f"{date_str}_t3_subset_coverage.json"
    output_path.write_text(
        json.dumps(
            {
                "humaneval": humaneval_report,
                "mbpp": mbpp_report,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )
    print(f"\nWrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
