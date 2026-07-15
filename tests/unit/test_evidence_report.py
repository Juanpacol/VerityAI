"""Unit tests for evidence/report.py -- per-topic coverage tabulation and
markdown rendering. Built from synthetic in-memory records, no store I/O.
"""

from datetime import datetime, timedelta, timezone

from verityai.evidence.models import Classification, EvidenceRecord, ValidationReport
from verityai.evidence.report import compute_coverage_report, render_markdown


def make_record(**overrides) -> EvidenceRecord:
    defaults = dict(
        id="arxiv_abc123",
        source="arxiv",
        source_url="https://arxiv.org/abs/1234.5678",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        retrieval_method="requests",
        content={"title": "x"},
        content_hash="a" * 64,
        feeds_topics=["T1"],
        validation=ValidationReport(status="valid"),
    )
    defaults.update(overrides)
    return EvidenceRecord(**defaults)


class TestComputeCoverageReport:
    def test_returns_a_row_for_every_topic_even_with_zero_records(self):
        report = compute_coverage_report([])

        topics = [row.topic for row in report]
        assert topics == ["T1", "T2", "T3", "T4", "T5", "T6"]
        assert all(row.record_count == 0 for row in report)
        assert all(row.validation_pass_rate is None for row in report)

    def test_counts_records_per_topic(self):
        records = [
            make_record(id="a", feeds_topics=["T1"]),
            make_record(id="b", feeds_topics=["T1", "T3"]),
            make_record(id="c", feeds_topics=["T3"]),
        ]
        report = compute_coverage_report(records)

        by_topic = {row.topic: row for row in report}
        assert by_topic["T1"].record_count == 2
        assert by_topic["T3"].record_count == 2
        assert by_topic["T2"].record_count == 0

    def test_sources_deduplicated_and_sorted(self):
        records = [
            make_record(id="a", source="arxiv", feeds_topics=["T1"]),
            make_record(id="b", source="mbpp", feeds_topics=["T1"]),
            make_record(id="c", source="arxiv", feeds_topics=["T1"]),
        ]
        report = compute_coverage_report(records)

        t1 = next(row for row in report if row.topic == "T1")
        assert t1.sources == ["arxiv", "mbpp"]

    def test_validation_pass_rate(self):
        records = [
            make_record(id="a", feeds_topics=["T1"], validation=ValidationReport(status="valid")),
            make_record(id="b", feeds_topics=["T1"], validation=ValidationReport(status="invalid")),
        ]
        report = compute_coverage_report(records)

        t1 = next(row for row in report if row.topic == "T1")
        assert t1.validation_pass_rate == 0.5

    def test_median_age_computed_from_retrieved_at(self):
        now = datetime.now(timezone.utc)
        records = [
            make_record(id="a", feeds_topics=["T1"], retrieved_at=now.isoformat()),
            make_record(
                id="b",
                feeds_topics=["T1"],
                retrieved_at=(now - timedelta(days=10)).isoformat(),
            ),
        ]
        report = compute_coverage_report(records, now=now)

        t1 = next(row for row in report if row.topic == "T1")
        assert t1.median_age_days == 5

    def test_llm_classified_and_reviewed_rates(self):
        records = [
            make_record(
                id="a",
                feeds_topics=["T1"],
                classification=Classification(
                    classified_by="llama3.2", classification_reviewed=True
                ),
            ),
            make_record(
                id="b",
                feeds_topics=["T1"],
                classification=Classification(
                    classified_by="none", degraded_reason="no generate_fn configured"
                ),
            ),
            make_record(id="c", feeds_topics=["T1"]),  # never classified
        ]
        report = compute_coverage_report(records)

        t1 = next(row for row in report if row.topic == "T1")
        assert t1.llm_classified_rate == 1 / 3  # only "a" has classified_by != "none"
        assert t1.reviewed_rate == 1 / 3


class TestRenderMarkdown:
    def test_includes_a_row_per_topic(self):
        report = compute_coverage_report([make_record(feeds_topics=["T1"])])

        markdown = render_markdown(report)

        for topic in ("T1", "T2", "T3", "T4", "T5", "T6"):
            assert f"| {topic} |" in markdown

    def test_zero_records_topic_shows_na_not_crash(self):
        report = compute_coverage_report([])

        markdown = render_markdown(report)

        assert "n/a" in markdown

    def test_references_the_auditor_agent(self):
        report = compute_coverage_report([])
        markdown = render_markdown(report)

        assert "evidence-auditor" in markdown
