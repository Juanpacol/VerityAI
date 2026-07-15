"""Rule-count evidence from semgrep/semgrep-rules, via the `gh` CLI.

Deliberately stores ONLY aggregate counts by top-level category (directory)
and file paths -- never rule bodies. semgrep-rules is licensed
LGPL-2.1 + Commons Clause; counting how many rules exist per language is
fair use of public repo metadata, redistributing rule content itself is not.
"""

import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from typing import Callable, Optional

from verityai.evidence.fetchers.base import FetchResult
from verityai.evidence.hashing import compute_content_hash
from verityai.evidence.models import EvidenceRecord

SEMGREP_RULES_REPO = "semgrep/semgrep-rules"
SEMGREP_RULES_LICENSE = "LGPL-2.1 + Commons Clause"
_TREE_API_PATH = f"repos/{SEMGREP_RULES_REPO}/git/trees/develop?recursive=1"


def fetch_semgrep_rule_counts(run_subprocess: Optional[Callable] = None) -> FetchResult:
    run_subprocess = run_subprocess or subprocess.run
    result = FetchResult()

    try:
        proc = run_subprocess(
            ["gh", "api", _TREE_API_PATH], capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        result.errors.append({"item": SEMGREP_RULES_REPO, "error": "gh CLI not available"})
        return result
    except Exception as e:  # noqa: BLE001
        result.errors.append({"item": SEMGREP_RULES_REPO, "error": str(e)})
        return result

    if proc.returncode != 0:
        result.errors.append(
            {
                "item": SEMGREP_RULES_REPO,
                "error": proc.stderr.strip() or f"gh exited {proc.returncode}",
            }
        )
        return result

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        result.errors.append({"item": SEMGREP_RULES_REPO, "error": f"unparseable gh output: {e}"})
        return result

    counts: Counter = Counter()
    total = 0
    for entry in data.get("tree", []):
        path = entry.get("path", "")
        if entry.get("type") != "blob" or not path.endswith((".yaml", ".yml")):
            continue
        if path.startswith("."):  # .github/workflows/*.yaml, .pre-commit-config.yaml etc.
            continue
        total += 1
        counts[path.split("/", 1)[0]] += 1

    content = {
        "total_rule_files": total,
        "counts_by_category": dict(counts.most_common()),
        "tree_truncated": data.get("truncated", False),
    }
    content_hash = compute_content_hash(content)
    result.records.append(
        EvidenceRecord(
            id=f"semgrep_{content_hash[:12]}",
            source="semgrep",
            source_url=f"https://github.com/{SEMGREP_RULES_REPO}",
            license=SEMGREP_RULES_LICENSE,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            retrieval_method="gh_cli",
            content=content,
            content_hash=content_hash,
            feeds_topics=["T4"],
        )
    )
    return result
