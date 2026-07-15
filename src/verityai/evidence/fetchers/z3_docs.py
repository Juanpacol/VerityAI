"""Fetches specific, pinned pages from Z3's official documentation
(microsoft/z3guide, MIT-licensed) describing theory limitations relevant
to T6 (can Z3 detect SQL injection / race conditions at all, or is that
pattern-matching territory?).

Pinned to exact raw-content URLs rather than a search or crawl -- this is
about defending specific claims (e.g. "Z3's string theory is documented as
incomplete/undecidable"), not general documentation harvesting.
"""

from datetime import datetime, timezone
from typing import Callable, Optional

import requests

from verityai.evidence.fetchers.base import FetchResult
from verityai.evidence.hashing import compute_content_hash
from verityai.evidence.models import EvidenceRecord, ResearchTopic

Z3GUIDE_LICENSE = "MIT"

# (title, raw content URL, topics fed). Each is a specific theories page
# from microsoft/z3guide's markdown source, not the rendered Docusaurus
# site (which would require HTML parsing this project has no dependency
# for).
Z3_DOC_PAGES: list[tuple[str, str, list[ResearchTopic]]] = [
    (
        "Strings",
        "https://raw.githubusercontent.com/microsoft/z3guide/main/website/docs-smtlib/"
        "02%20-%20theories/06%20-%20Strings.md",
        ["T6"],
    ),
    (
        "Arrays",
        "https://raw.githubusercontent.com/microsoft/z3guide/main/website/docs-smtlib/"
        "02%20-%20theories/04%20-%20Arrays.md",
        ["T6"],
    ),
]


def fetch_z3_docs(
    pages: Optional[list[tuple[str, str, list[ResearchTopic]]]] = None,
    http_get: Optional[Callable] = None,
) -> FetchResult:
    pages = pages if pages is not None else Z3_DOC_PAGES
    http_get = http_get or requests.get

    result = FetchResult()
    for title, url, topics in pages:
        try:
            response = http_get(url, timeout=15)
            response.raise_for_status()
            text = response.text
        except Exception as e:  # noqa: BLE001
            result.errors.append({"item": title, "error": str(e)})
            continue

        content = {"title": title, "text": text, "permalink": url}
        content_hash = compute_content_hash(content)
        result.records.append(
            EvidenceRecord(
                id=f"z3_docs_{content_hash[:12]}",
                source="z3_docs",
                source_url=url,
                license=Z3GUIDE_LICENSE,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                retrieval_method="requests",
                content=content,
                content_hash=content_hash,
                feeds_topics=topics,
            )
        )

    return result
