"""arXiv API fetcher -- metadata only (title/abstract/authors/categories),
never PDFs at scale, per arXiv's API Terms of Use.

Rate-limited to 1 request per 3 seconds (the ToS-documented minimum), via
the injectable `RateLimiter` in `fetchers.base`.
"""

from datetime import datetime, timezone
from typing import Callable, Optional
from xml.etree import ElementTree as ET

import requests

from verityai.evidence.fetchers.base import Checkpoint, FetchResult, RateLimiter
from verityai.evidence.hashing import compute_content_hash
from verityai.evidence.models import EvidenceRecord, ResearchTopic

ARXIV_API_URL = "http://export.arxiv.org/api/query"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_TOS_MIN_INTERVAL_SECONDS = 3.0

# Fixed query set mapped to the research topics they feed. Deliberately
# static (not user-supplied search terms) -- this pipeline defends specific
# T1-T6 claims, not general-purpose literature search.
ARXIV_QUERIES: list[tuple[str, list[ResearchTopic]]] = [
    ("confidence calibration neural network predictions", ["T1"]),
    ("large language model self-correction code generation", ["T2"]),
    ("developer trust explainable AI generated code", ["T5"]),
    ("formal verification large language model generated code", ["T1", "T6"]),
]


def _tag(name: str) -> str:
    return f"{{{_ATOM_NS}}}{name}"


def _parse_atom_entries(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    entries = []
    for entry_el in root.findall(_tag("entry")):
        id_text = (entry_el.findtext(_tag("id")) or "").strip()
        title = " ".join((entry_el.findtext(_tag("title")) or "").split())
        summary = " ".join((entry_el.findtext(_tag("summary")) or "").split())
        published = (entry_el.findtext(_tag("published")) or "").strip()
        authors = [
            (author_el.findtext(_tag("name")) or "").strip()
            for author_el in entry_el.findall(_tag("author"))
        ]
        categories = [cat_el.get("term", "") for cat_el in entry_el.findall(_tag("category"))]
        arxiv_id = id_text.rsplit("/", 1)[-1] if id_text else ""
        entries.append(
            {
                "arxiv_id": arxiv_id,
                "url": id_text,
                "title": title,
                "abstract": summary,
                "published": published,
                "authors": authors,
                "categories": categories,
            }
        )
    return entries


def _entry_to_record(entry: dict, topics: list[ResearchTopic]) -> EvidenceRecord:
    content = {
        "arxiv_id": entry["arxiv_id"],
        "title": entry["title"],
        "abstract": entry["abstract"],
        "authors": entry["authors"],
        "categories": entry["categories"],
        "published": entry["published"],
    }
    content_hash = compute_content_hash(content)
    return EvidenceRecord(
        id=f"arxiv_{content_hash[:12]}",
        source="arxiv",
        source_url=entry["url"] or f"https://arxiv.org/abs/{entry['arxiv_id']}",
        license=None,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        retrieval_method="requests",
        content=content,
        content_hash=content_hash,
        feeds_topics=topics,
    )


def fetch_arxiv(
    queries: Optional[list[tuple[str, list[ResearchTopic]]]] = None,
    max_results_per_query: int = 10,
    http_get: Optional[Callable] = None,
    rate_limiter: Optional[RateLimiter] = None,
    checkpoint: Optional[Checkpoint] = None,
) -> FetchResult:
    """Fetch arXiv papers for each configured query.

    A query already marked done in `checkpoint` is skipped entirely,
    letting a crashed/interrupted run resume without re-fetching or
    re-rate-limiting queries it already completed.
    """
    queries = queries if queries is not None else ARXIV_QUERIES
    http_get = http_get or requests.get
    rate_limiter = rate_limiter or RateLimiter(_ARXIV_TOS_MIN_INTERVAL_SECONDS)

    result = FetchResult()
    for query, topics in queries:
        if checkpoint is not None and checkpoint.is_done(query):
            continue

        rate_limiter.wait()
        try:
            response = http_get(
                ARXIV_API_URL,
                params={
                    "search_query": f"all:{query}",
                    "start": "0",
                    "max_results": str(max_results_per_query),
                },
                timeout=15,
            )
            response.raise_for_status()
            entries = _parse_atom_entries(response.text)
        except Exception as e:  # noqa: BLE001 -- per-item isolation is the point
            result.errors.append({"item": query, "error": str(e)})
            continue

        for entry in entries:
            try:
                result.records.append(_entry_to_record(entry, topics))
            except Exception as e:  # noqa: BLE001
                result.errors.append({"item": entry.get("arxiv_id", query), "error": str(e)})

        if checkpoint is not None:
            checkpoint.mark_done(query)

    return result
