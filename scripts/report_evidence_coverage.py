#!/usr/bin/env python3
"""Render docs/EVIDENCE_COVERAGE.md: per-topic (T1-T6) record counts,
validation pass rates, freshness, and how much has been LLM-classified
vs. actually human/auditor-reviewed.

Usage:
  python scripts/report_evidence_coverage.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from verityai.evidence.report import compute_coverage_report, render_markdown  # noqa: E402
from verityai.evidence.store import EvidenceStore  # noqa: E402


def main() -> int:
    repo_root = Path(__file__).parent.parent
    store = EvidenceStore(repo_root / "docs" / "evidence")
    records = list(store.iter_records())

    report = compute_coverage_report(records)
    markdown = render_markdown(report)

    (repo_root / "docs" / "EVIDENCE_COVERAGE.md").write_text(markdown)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = repo_root / "docs" / "results" / f"{date_str}_evidence_coverage.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "topics": [
                    {
                        "topic": row.topic,
                        "record_count": row.record_count,
                        "sources": row.sources,
                        "validation_pass_rate": row.validation_pass_rate,
                        "median_age_days": row.median_age_days,
                        "llm_classified_rate": row.llm_classified_rate,
                        "reviewed_rate": row.reviewed_rate,
                    }
                    for row in report
                ],
                "total_records": len(records),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )

    print(markdown)
    print(f"\nWrote docs/EVIDENCE_COVERAGE.md and {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
