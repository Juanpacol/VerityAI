"""Tests for the deterministic benchmark harness.

The subject under test is mostly the harness's willingness to disqualify its
own results. A benchmark that always reports a number will eventually report a
wrong one, so the guards -- duplicate-heavy corpora, tiny samples, estimated
counts -- are what these tests pin down.
"""

import json

from verityai.bench.deterministic import (
    measure_case,
    measure_corpus,
    render_report,
    to_json,
)

from ..conftest import FixedCounter


def transcript(n_items=30, duplicate=False):
    """A JSON transcript with `n_items` messages."""
    if duplicate:
        content = "the same content repeated verbatim every single time here"
        messages = [{"role": "assistant", "content": content} for _ in range(n_items)]
    else:
        messages = [
            {"role": "assistant", "content": f"distinct message {n} about the design of part {n}"}
            for n in range(n_items)
        ]
    return json.dumps(messages)


class TestSelfDisqualification:
    def test_a_duplicate_heavy_corpus_is_flagged(self):
        case = measure_case("synthetic", transcript(duplicate=True), counter=FixedCounter())

        assert any("duplicates" in w for w in case.warnings)
        assert any("Do not publish" in w for w in case.warnings)

    def test_a_realistic_corpus_is_not_flagged_for_duplication(self):
        case = measure_case("realistic", transcript(), counter=FixedCounter())

        assert not any("duplicates" in w for w in case.warnings)

    def test_a_tiny_corpus_is_flagged(self):
        case = measure_case("tiny", transcript(n_items=5), counter=FixedCounter())

        assert any("too small" in w for w in case.warnings)

    def test_estimated_counts_block_publication(self, tmp_path):
        counter = FixedCounter()
        counter._encoder = None

        for n in range(3):
            (tmp_path / f"t{n}.json").write_text(transcript())

        report = measure_corpus(sorted(tmp_path.glob("*.json")), counter=counter)

        assert any("estimates" in w for w in report.warnings)
        assert report.is_publishable is False

    def test_a_single_transcript_is_not_a_corpus(self, tmp_path):
        (tmp_path / "only.json").write_text(transcript())

        report = measure_corpus([tmp_path / "only.json"], counter=FixedCounter())

        assert any("not a corpus" in w for w in report.warnings)
        assert report.is_publishable is False

    def test_a_case_warning_blocks_the_whole_corpus(self, tmp_path):
        for n in range(3):
            (tmp_path / f"t{n}.json").write_text(transcript(duplicate=True))

        report = measure_corpus(sorted(tmp_path.glob("*.json")), counter=FixedCounter())

        assert report.is_publishable is False


class TestMeasurement:
    def test_pruning_a_duplicate_transcript_saves_tokens(self):
        case = measure_case("dupes", transcript(duplicate=True), counter=FixedCounter())

        assert case.tokens_saved > 0
        assert case.items_after < case.items_before

    def test_critical_retention_is_measured(self):
        case = measure_case("normal", transcript(), counter=FixedCounter())

        assert case.critical_retention == 1.0

    def test_the_stage_ledger_is_captured(self):
        case = measure_case("normal", transcript(), budget=50, counter=FixedCounter())

        names = [stage["name"] for stage in case.stages]
        assert "dedup" in names
        assert "budget" in names

    def test_the_token_method_is_recorded(self):
        case = measure_case("normal", transcript(), counter=FixedCounter())

        assert case.token_method == "fixed:words"

    def test_reduction_ratio_of_an_empty_case_is_zero(self):
        case = measure_case("empty", "", counter=FixedCounter())

        assert case.reduction_ratio == 0.0


class TestAggregation:
    def test_the_aggregate_is_token_weighted_not_a_mean_of_ratios(self, tmp_path):
        """A tiny transcript with a huge ratio must not outvote a large one."""
        (tmp_path / "large.json").write_text(transcript(n_items=100))
        (tmp_path / "tiny.json").write_text(transcript(n_items=3, duplicate=True))

        report = measure_corpus(sorted(tmp_path.glob("*.json")), counter=FixedCounter())

        mean_of_ratios = sum(c.reduction_ratio for c in report.cases) / len(report.cases)
        assert report.aggregate_ratio != mean_of_ratios

    def test_totals_are_the_sum_of_cases(self, tmp_path):
        for n in range(3):
            (tmp_path / f"t{n}.json").write_text(transcript())

        report = measure_corpus(sorted(tmp_path.glob("*.json")), counter=FixedCounter())

        assert report.total_before == sum(c.tokens_before for c in report.cases)
        assert report.total_after == sum(c.tokens_after for c in report.cases)

    def test_one_counter_is_used_across_the_whole_corpus(self, tmp_path):
        for n in range(3):
            (tmp_path / f"t{n}.json").write_text(transcript())

        report = measure_corpus(sorted(tmp_path.glob("*.json")), counter=FixedCounter())

        assert {c.token_method for c in report.cases} == {report.token_method}


class TestReporting:
    def test_warnings_are_rendered_above_the_numbers(self, tmp_path):
        (tmp_path / "dupes.json").write_text(transcript(duplicate=True))

        report = measure_corpus([tmp_path / "dupes.json"], counter=FixedCounter())
        rendered = render_report(report)

        assert rendered.index("WARNINGS") < rendered.index("TOTAL")

    def test_an_unpublishable_report_says_so(self, tmp_path):
        (tmp_path / "dupes.json").write_text(transcript(duplicate=True))

        report = measure_corpus([tmp_path / "dupes.json"], counter=FixedCounter())

        assert "NOT PUBLISHABLE" in render_report(report)

    def test_the_family_is_named_in_the_output(self, tmp_path):
        (tmp_path / "t.json").write_text(transcript())

        rendered = render_report(measure_corpus([tmp_path / "t.json"], counter=FixedCounter()))

        assert "Family A" in rendered

    def test_json_output_carries_the_warnings_with_the_data(self, tmp_path):
        """A consumer must not be able to take the numbers and drop the caveats."""
        (tmp_path / "dupes.json").write_text(transcript(duplicate=True))

        report = measure_corpus([tmp_path / "dupes.json"], counter=FixedCounter())
        parsed = json.loads(to_json(report))

        assert parsed["publishable"] is False
        assert parsed["warnings"] or parsed["cases"][0]["warnings"]
        assert parsed["family"] == "A"

    def test_json_records_the_counting_method(self, tmp_path):
        (tmp_path / "t.json").write_text(transcript())

        parsed = json.loads(to_json(measure_corpus([tmp_path / "t.json"], counter=FixedCounter())))

        assert parsed["token_method"] == "fixed:words"
