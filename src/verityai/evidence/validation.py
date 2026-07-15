"""Deterministic validation for `EvidenceRecord`s.

Never raises on bad data and never infers "valid" by default -- every check
that fails appends a human-readable reason to the report, and a record with
zero failing checks is the only way to earn `status="valid"`. Same
"degrade, never lie" philosophy as `NOT_VERIFIED` (ADR-0001) and
`kg/retrieval.py`'s `degraded_reason`, applied to evidence bookkeeping
instead of code verification.
"""

from datetime import datetime, timezone
from typing import Literal, Optional
from urllib.parse import urlparse

from verityai.evidence.hashing import compute_content_hash
from verityai.evidence.models import EvidenceRecord, ValidationReport

# Max age in days before a record is flagged "stale", per source. Datasets
# (HumanEval/MBPP) are frozen artifacts so they age slowly; a GitHub issue
# search result goes stale fastest since new issues appear constantly.
FRESHNESS_POLICY: dict[str, int] = {
    "humaneval": 365,
    "mbpp": 365,
    "arxiv": 180,
    "semgrep": 90,
    "z3_docs": 90,
    "github_issues": 60,
}


def _parse_retrieved_at(value: str) -> Optional[datetime]:
    try:
        # fromisoformat doesn't accept a trailing "Z" before Python 3.11;
        # this repo targets 3.9, so normalize it to an explicit UTC offset.
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def validate_record(
    record: EvidenceRecord,
    known_hashes: set,
    now: Optional[datetime] = None,
) -> ValidationReport:
    """Validate one record. Never raises.

    `known_hashes` should be the set of content hashes belonging to OTHER
    already-stored records (e.g. `store.known_hashes()` computed BEFORE
    saving this record, or with this record's own hash excluded) -- passing
    a set that includes this record's own hash will flag it as a duplicate
    of itself.
    """
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    if not record.source_url.strip():
        reasons.append("missing source_url")
    if not record.retrieved_at.strip():
        reasons.append("missing retrieved_at")
    if not record.content:
        reasons.append("empty content")
    if not record.feeds_topics:
        reasons.append("no feeds_topics tagged")

    if record.source_url.strip():
        parsed_url = urlparse(record.source_url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            reasons.append(f"malformed source_url: {record.source_url!r}")

    if record.content:
        recomputed = compute_content_hash(record.content)
        if recomputed != record.content_hash:
            reasons.append("content_hash does not match recomputed hash")

    if record.content_hash in known_hashes:
        reasons.append(f"duplicate content_hash: {record.content_hash}")

    stale_reasons: list[str] = []
    retrieved_at = _parse_retrieved_at(record.retrieved_at) if record.retrieved_at.strip() else None
    if record.retrieved_at.strip() and retrieved_at is None:
        reasons.append(f"unparseable retrieved_at: {record.retrieved_at!r}")
    elif retrieved_at is not None:
        max_age_days = FRESHNESS_POLICY.get(record.source)
        if max_age_days is not None:
            age_days = (now - retrieved_at).days
            if age_days > max_age_days:
                stale_reasons.append(
                    f"stale: retrieved {age_days}d ago, max {max_age_days}d for {record.source}"
                )

    all_reasons = reasons + stale_reasons
    status: Literal["valid", "invalid", "stale"]
    if not all_reasons:
        status = "valid"
    elif not reasons and stale_reasons:
        status = "stale"
    else:
        status = "invalid"

    return ValidationReport(status=status, reasons=all_reasons)


def validate_store(records: list[EvidenceRecord]) -> dict:
    """Summarize validation status across a batch of records already loaded
    from a store. Re-validates each against the hashes of all OTHERS in the
    batch (excluding itself), so duplicate detection works across the set.
    """
    all_hashes = [r.content_hash for r in records]
    summary = {"total": len(records), "valid": 0, "invalid": 0, "stale": 0, "unchecked": 0}
    per_record = []
    for i, record in enumerate(records):
        others = set(all_hashes[:i] + all_hashes[i + 1 :])
        report = validate_record(record, known_hashes=others)
        summary[report.status] += 1
        per_record.append({"id": record.id, "status": report.status, "reasons": report.reasons})
    return {"summary": summary, "records": per_record}
