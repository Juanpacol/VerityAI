"""Privacy safeguard: benchmark output must never contain raw transcript text.

Family A's whole premise is that it can run over real, private session
transcripts (see `context/ingest_claude_code.py`). That is only acceptable if
the measurement never leaks what it measured — `CaseResult`/`CorpusReport`
carry counts and hashes, never `.content`, and this file pins that down
explicitly rather than leaving it as an implicit property of which fields
happen to exist today.
"""

import json

from verityai.bench.deterministic import measure_case, measure_corpus, render_report, to_json

from ..conftest import FixedCounter

SECRET_MARKER = "SECRET_TOKEN_sk_live_do_not_leak_this_9f8e7d"

TRANSCRIPT_WITH_SECRET = json.dumps(
    [
        {"role": "user", "content": f"Here is my API key: {SECRET_MARKER}, please use it."},
        {"role": "assistant", "content": f"Got it, using {SECRET_MARKER} for the request."},
        {"role": "tool", "content": f"Authenticated with {SECRET_MARKER}. Response: 200 OK."},
    ]
)


class TestCaseResultNeverCarriesContent:
    def test_the_secret_does_not_appear_in_the_rendered_report(self):
        case = measure_case("session.json", TRANSCRIPT_WITH_SECRET, counter=FixedCounter())

        rendered = "\n".join(f"{k}={v}" for k, v in vars(case).items() if k != "stages")
        assert SECRET_MARKER not in rendered

    def test_the_secret_does_not_appear_in_case_result_fields(self):
        case = measure_case("session.json", TRANSCRIPT_WITH_SECRET, counter=FixedCounter())

        for field_name, value in vars(case).items():
            assert SECRET_MARKER not in str(value), field_name

    def test_stage_ledger_carries_only_counts(self):
        case = measure_case("session.json", TRANSCRIPT_WITH_SECRET, counter=FixedCounter())

        for stage in case.stages:
            assert SECRET_MARKER not in json.dumps(stage)


class TestCorpusReportNeverCarriesContent:
    def test_render_report_never_contains_the_secret(self, tmp_path):
        path = tmp_path / "session.json"
        path.write_text(TRANSCRIPT_WITH_SECRET)

        report = measure_corpus([path], counter=FixedCounter())

        assert SECRET_MARKER not in render_report(report)

    def test_to_json_never_contains_the_secret(self, tmp_path):
        path = tmp_path / "session.json"
        path.write_text(TRANSCRIPT_WITH_SECRET)

        report = measure_corpus([path], counter=FixedCounter())

        assert SECRET_MARKER not in to_json(report)

    def test_only_the_filename_identifies_a_case_not_its_content(self, tmp_path):
        """A case is addressed by its filename, which the caller already
        knows -- never by anything extracted from inside the file."""
        path = tmp_path / "my_private_session.json"
        path.write_text(TRANSCRIPT_WITH_SECRET)

        report = measure_corpus([path], counter=FixedCounter())

        assert report.cases[0].name == "my_private_session.json"
