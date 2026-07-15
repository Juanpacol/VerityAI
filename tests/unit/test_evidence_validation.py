"""Unit tests for evidence/validation.py -- deterministic, never-raises
validation of EvidenceRecords.
"""

from datetime import datetime, timedelta, timezone

from verityai.evidence.hashing import compute_content_hash
from verityai.evidence.models import EvidenceRecord
from verityai.evidence.validation import validate_record, validate_store


def make_record(**overrides) -> EvidenceRecord:
    content = overrides.pop("content", {"title": "On Calibration"})
    defaults = dict(
        id="arxiv_abc123",
        source="arxiv",
        source_url="https://arxiv.org/abs/1234.5678",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        retrieval_method="requests",
        content=content,
        content_hash=overrides.pop("content_hash", compute_content_hash(content)),
        feeds_topics=["T1"],
    )
    defaults.update(overrides)
    return EvidenceRecord(**defaults)


class TestValidRecord:
    def test_well_formed_record_is_valid(self):
        record = make_record()
        report = validate_record(record, known_hashes=set())
        assert report.status == "valid"
        assert report.reasons == []


class TestProvenanceCompleteness:
    def test_missing_source_url_flagged(self):
        record = make_record(source_url="")
        report = validate_record(record, known_hashes=set())
        assert "missing source_url" in report.reasons
        assert report.status == "invalid"

    def test_missing_retrieved_at_flagged(self):
        record = make_record(retrieved_at="")
        report = validate_record(record, known_hashes=set())
        assert "missing retrieved_at" in report.reasons

    def test_empty_content_flagged(self):
        record = make_record(content={}, content_hash=compute_content_hash({}))
        report = validate_record(record, known_hashes=set())
        assert "empty content" in report.reasons

    def test_no_topics_flagged(self):
        record = make_record(feeds_topics=[])
        report = validate_record(record, known_hashes=set())
        assert "no feeds_topics tagged" in report.reasons


class TestUrlWellFormedness:
    def test_missing_scheme_flagged(self):
        record = make_record(source_url="arxiv.org/abs/1234")
        report = validate_record(record, known_hashes=set())
        assert any("malformed source_url" in r for r in report.reasons)

    def test_ftp_scheme_flagged(self):
        record = make_record(source_url="ftp://arxiv.org/abs/1234")
        report = validate_record(record, known_hashes=set())
        assert any("malformed source_url" in r for r in report.reasons)


class TestHashIntegrity:
    def test_tampered_hash_flagged(self):
        record = make_record(content_hash="0" * 64)
        report = validate_record(record, known_hashes=set())
        assert "content_hash does not match recomputed hash" in report.reasons


class TestDuplicateDetection:
    def test_hash_in_known_hashes_flagged_as_duplicate(self):
        record = make_record()
        report = validate_record(record, known_hashes={record.content_hash})
        assert any("duplicate content_hash" in r for r in report.reasons)

    def test_hash_not_in_known_hashes_not_flagged(self):
        record = make_record()
        report = validate_record(record, known_hashes={"z" * 64})
        assert not any("duplicate" in r for r in report.reasons)


class TestFreshness:
    def test_fresh_record_not_stale(self):
        record = make_record(source="arxiv", retrieved_at=datetime.now(timezone.utc).isoformat())
        report = validate_record(record, known_hashes=set())
        assert report.status == "valid"

    def test_record_past_max_age_is_stale(self):
        old = datetime.now(timezone.utc) - timedelta(days=200)
        record = make_record(source="arxiv", retrieved_at=old.isoformat())
        report = validate_record(record, known_hashes=set())
        assert report.status == "stale"
        assert any("stale" in r for r in report.reasons)

    def test_exactly_at_max_age_boundary_not_stale(self):
        boundary = datetime.now(timezone.utc) - timedelta(days=180)
        record = make_record(source="arxiv", retrieved_at=boundary.isoformat())
        report = validate_record(record, known_hashes=set())
        assert report.status == "valid"

    def test_unparseable_retrieved_at_flagged(self):
        record = make_record(retrieved_at="not-a-date")
        report = validate_record(record, known_hashes=set())
        assert any("unparseable retrieved_at" in r for r in report.reasons)
        assert report.status == "invalid"

    def test_source_with_no_freshness_policy_never_stale(self):
        record = make_record(
            source="humaneval",
            retrieved_at=(datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
        )
        # humaneval has a 365-day policy -- this one exceeds it, so it IS stale;
        # this test documents that the policy applies once a source has one.
        report = validate_record(record, known_hashes=set())
        assert report.status == "stale"


class TestMultiFailureAccumulation:
    def test_invalid_wins_over_stale_when_both_present(self):
        old = datetime.now(timezone.utc) - timedelta(days=200)
        record = make_record(source="arxiv", source_url="", retrieved_at=old.isoformat())
        report = validate_record(record, known_hashes=set())
        assert report.status == "invalid"
        assert len(report.reasons) >= 2


class TestValidateStore:
    def test_summarizes_batch_by_status(self):
        fresh_content = {"title": "fresh paper"}
        stale_content = {"title": "stale paper"}
        fresh = make_record(
            id="a", content=fresh_content, content_hash=compute_content_hash(fresh_content)
        )
        stale = make_record(
            id="b",
            source="arxiv",
            content=stale_content,
            content_hash=compute_content_hash(stale_content),
            retrieved_at=(datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
        )
        summary = validate_store([fresh, stale])
        assert summary["summary"]["valid"] == 1
        assert summary["summary"]["stale"] == 1
        assert summary["summary"]["total"] == 2

    def test_cross_record_duplicate_detected(self):
        content = {"title": "duplicate paper"}
        r1 = make_record(id="a", content=content, content_hash=compute_content_hash(content))
        r2 = make_record(id="b", content=content, content_hash=compute_content_hash(content))
        summary = validate_store([r1, r2])
        assert summary["summary"]["invalid"] == 2
        assert all(
            any("duplicate" in reason for reason in rec["reasons"]) for rec in summary["records"]
        )
