"""GitHub issue search via the `gh` CLI -- no token handling in repo code,
relies entirely on whatever `gh auth` session is already active locally.

Only metadata + a short excerpt (never the full issue thread) is stored,
per GitHub's standard API terms.
"""

import json
import subprocess
from datetime import datetime, timezone
from typing import Callable, Optional

from verityai.evidence.fetchers.base import Checkpoint, FetchResult
from verityai.evidence.hashing import compute_content_hash
from verityai.evidence.models import EvidenceRecord, ResearchTopic

_EXCERPT_MAX_CHARS = 500
_GH_JSON_FIELDS = "title,url,state,labels,body"

# (search terms, repo filter or None for global search, topics fed).
# Deliberately short (2-3 words): GitHub issue search ANDs every
# space-separated term, so longer, more "precise"-looking queries silently
# return zero results instead of erroring -- verified by hand against the
# live API before picking these (see the commit message for the ones that
# looked reasonable but returned nothing).
GITHUB_ISSUE_QUERIES: list[tuple[str, Optional[str], list[ResearchTopic]]] = [
    ("AI generated code bug", None, ["T4", "T6"]),
    ("LLM hallucination", None, ["T2", "T5"]),
    ("string theory", "Z3Prover/z3", ["T6"]),
    ("recursion", "Z3Prover/z3", ["T6"]),
]


def _entry_to_record(
    item: dict, query: str, repo: Optional[str], topics: list[ResearchTopic]
) -> EvidenceRecord:
    body = item.get("body") or ""
    content = {
        "query": query,
        "repo": repo,
        "title": item.get("title", ""),
        "state": item.get("state", ""),
        "labels": [label.get("name", "") for label in item.get("labels", [])],
        "excerpt": body[:_EXCERPT_MAX_CHARS],
    }
    content_hash = compute_content_hash(content)
    return EvidenceRecord(
        id=f"github_issues_{content_hash[:12]}",
        source="github_issues",
        source_url=item.get("url", ""),
        license=None,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        retrieval_method="gh_cli",
        content=content,
        content_hash=content_hash,
        feeds_topics=topics,
    )


def fetch_github_issues(
    queries: Optional[list[tuple[str, Optional[str], list[ResearchTopic]]]] = None,
    limit_per_query: int = 10,
    run_subprocess: Optional[Callable] = None,
    checkpoint: Optional[Checkpoint] = None,
) -> FetchResult:
    queries = queries if queries is not None else GITHUB_ISSUE_QUERIES
    run_subprocess = run_subprocess or subprocess.run

    result = FetchResult()
    for query, repo, topics in queries:
        checkpoint_key = f"{repo or 'global'}::{query}"
        if checkpoint is not None and checkpoint.is_done(checkpoint_key):
            continue

        args = [
            "gh",
            "search",
            "issues",
            query,
            "--limit",
            str(limit_per_query),
            "--json",
            _GH_JSON_FIELDS,
        ]
        if repo:
            args.extend(["-R", repo])

        try:
            proc = run_subprocess(args, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            result.errors.append({"item": checkpoint_key, "error": "gh CLI not available"})
            continue
        except Exception as e:  # noqa: BLE001
            result.errors.append({"item": checkpoint_key, "error": str(e)})
            continue

        if proc.returncode != 0:
            result.errors.append(
                {
                    "item": checkpoint_key,
                    "error": proc.stderr.strip() or f"gh exited {proc.returncode}",
                }
            )
            continue

        try:
            items = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            result.errors.append({"item": checkpoint_key, "error": f"unparseable gh output: {e}"})
            continue

        for item in items:
            try:
                result.records.append(_entry_to_record(item, query, repo, topics))
            except Exception as e:  # noqa: BLE001
                result.errors.append({"item": item.get("url", query), "error": str(e)})

        if checkpoint is not None:
            checkpoint.mark_done(checkpoint_key)

    return result
