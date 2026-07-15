"""Unit tests for evidence/models.py -- the EvidenceRecord schema backing
the T1-T6 research-evidence pipeline. Pure pydantic validation, no I/O.
"""

import pytest
from pydantic import ValidationError

from verityai.evidence.models import Classification, EvidenceRecord, ValidationReport


def make_record(**overrides) -> EvidenceRecord:
    defaults = dict(
        id="arxiv_abc123",
        source="arxiv",
        source_url="https://arxiv.org/abs/1234.5678",
        retrieved_at="2026-07-15T00:00:00Z",
        retrieval_method="requests",
        content={"title": "On Calibration"},
        content_hash="a" * 64,
        feeds_topics=["T1"],
    )
    defaults.update(overrides)
    return EvidenceRecord(**defaults)


class TestEvidenceRecordRoundTrip:
    def test_round_trips_through_json(self):
        record = make_record()
        restored = EvidenceRecord.model_validate_json(record.model_dump_json())
        assert restored == record

    def test_defaults_are_unchecked_and_unclassified(self):
        record = make_record()
        assert record.validation.status == "unchecked"
        assert record.validation.reasons == []
        assert record.classification is None

    def test_license_and_classification_are_optional(self):
        record = make_record()
        assert record.license is None
        assert record.classification is None


class TestEvidenceRecordValidation:
    def test_rejects_unknown_source(self):
        with pytest.raises(ValidationError):
            make_record(source="not_a_real_source")

    def test_rejects_unknown_topic_tag(self):
        with pytest.raises(ValidationError):
            make_record(feeds_topics=["T99"])

    def test_rejects_unknown_retrieval_method(self):
        with pytest.raises(ValidationError):
            make_record(retrieval_method="carrier_pigeon")


class TestClassification:
    def test_degraded_classification_stamps_reason(self):
        c = Classification(classified_by="none", degraded_reason="no generate_fn configured")
        assert c.classification_reviewed is False
        assert c.degraded_reason == "no generate_fn configured"

    def test_reviewed_defaults_false_even_with_confidence(self):
        c = Classification(classified_by="llama3.2", confidence=0.9)
        assert c.classification_reviewed is False


class TestValidationReport:
    def test_default_status_is_unchecked(self):
        report = ValidationReport()
        assert report.status == "unchecked"
        assert report.reasons == []

    def test_accumulates_multiple_reasons(self):
        report = ValidationReport(status="invalid", reasons=["missing url", "stale"])
        assert len(report.reasons) == 2
