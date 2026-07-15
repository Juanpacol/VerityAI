#!/usr/bin/env python3
"""Fetch external evidence backing the T1-T6 research roadmap.

Wiring script: dispatches to source-specific fetchers in
verityai.evidence.fetchers, validates each record, stores it in
docs/evidence/<source>/, and writes a run summary to
docs/results/<date>_evidence_fetch.json -- same pattern as
scripts/run_retrieval_ab.py.

Usage:
  python scripts/fetch_evidence.py --source arxiv
  python scripts/fetch_evidence.py --source arxiv --limit 5
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from verityai.evidence.fetchers.arxiv import fetch_arxiv  # noqa: E402
from verityai.evidence.fetchers.base import Checkpoint  # noqa: E402
from verityai.evidence.store import EvidenceStore  # noqa: E402
from verityai.evidence.validation import validate_record  # noqa: E402

FETCHERS = {
    "arxiv": lambda limit, checkpoint: fetch_arxiv(
        max_results_per_query=limit, checkpoint=checkpoint
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, choices=sorted(FETCHERS))
    parser.add_argument(
        "--limit", type=int, default=10, help="Max results per query (source-dependent)"
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    evidence_root = repo_root / "docs" / "evidence"
    checkpoint = Checkpoint(evidence_root / ".checkpoints" / f"{args.source}.json")
    store = EvidenceStore(evidence_root)

    print(f"Fetching source={args.source} limit={args.limit}...")
    fetch_result = FETCHERS[args.source](args.limit, checkpoint)

    saved = 0
    for record in fetch_result.records:
        known_hashes = store.known_hashes() - {record.content_hash}
        record.validation = validate_record(record, known_hashes=known_hashes)
        if store.save(record):
            saved += 1

    output_dir = repo_root / "docs" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = output_dir / f"{date_str}_evidence_fetch.json"
    summary = {
        "source": args.source,
        "fetched": len(fetch_result.records),
        "saved_new_or_changed": saved,
        "errors": fetch_result.errors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    output_path.write_text(json.dumps(summary, indent=2))

    print(
        f"Fetched {len(fetch_result.records)} records, saved {saved} new/changed, "
        f"{len(fetch_result.errors)} errors. Wrote {output_path}"
    )
    if fetch_result.errors:
        print("Errors:")
        for error in fetch_result.errors:
            print(f"  - {error['item']}: {error['error']}")

    return 1 if fetch_result.errors and saved == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
