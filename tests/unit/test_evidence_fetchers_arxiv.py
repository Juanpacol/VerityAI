"""Unit tests for evidence/fetchers/arxiv.py.

`requests.get` is monkeypatched with `unittest.mock.patch` (same style as
test_ollama_client.py) -- no real network traffic, ever.
"""

from unittest.mock import MagicMock, patch

import requests

from verityai.evidence.fetchers.arxiv import fetch_arxiv
from verityai.evidence.fetchers.base import Checkpoint, RateLimiter

_ATOM_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1234.5678v1</id>
    <title>On Calibration of Modern Neural Networks</title>
    <summary>We study confidence calibration in classifiers.</summary>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Jane Doe</name></author>
    <author><name>John Smith</name></author>
    <category term="cs.LG"/>
    <category term="stat.ML"/>
  </entry>
</feed>
"""

_EMPTY_ATOM_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>
"""


def _no_op_rate_limiter() -> RateLimiter:
    return RateLimiter(0.0, sleep_fn=lambda s: None)


class TestFetchArxivSuccess:
    def test_parses_entries_into_records(self):
        mock_response = MagicMock(status_code=200, text=_ATOM_FIXTURE)
        with patch("requests.get", return_value=mock_response):
            result = fetch_arxiv(
                queries=[("confidence calibration", ["T1"])],
                rate_limiter=_no_op_rate_limiter(),
            )

        assert len(result.records) == 1
        record = result.records[0]
        assert record.source == "arxiv"
        assert record.content["arxiv_id"] == "1234.5678v1"
        assert record.content["title"] == "On Calibration of Modern Neural Networks"
        assert record.content["authors"] == ["Jane Doe", "John Smith"]
        assert record.content["categories"] == ["cs.LG", "stat.ML"]
        assert record.feeds_topics == ["T1"]
        assert record.source_url == "http://arxiv.org/abs/1234.5678v1"
        assert result.errors == []

    def test_record_id_is_hash_derived_and_deterministic(self):
        mock_response = MagicMock(status_code=200, text=_ATOM_FIXTURE)
        with patch("requests.get", return_value=mock_response):
            first = fetch_arxiv(
                queries=[("q", ["T1"])], rate_limiter=_no_op_rate_limiter()
            ).records[0]
            second = fetch_arxiv(
                queries=[("q", ["T1"])], rate_limiter=_no_op_rate_limiter()
            ).records[0]

        assert first.id == second.id
        assert first.id.startswith("arxiv_")

    def test_empty_feed_yields_no_records_no_errors(self):
        mock_response = MagicMock(status_code=200, text=_EMPTY_ATOM_FIXTURE)
        with patch("requests.get", return_value=mock_response):
            result = fetch_arxiv(queries=[("q", ["T1"])], rate_limiter=_no_op_rate_limiter())

        assert result.records == []
        assert result.errors == []

    def test_multiple_queries_tag_different_topics(self):
        mock_response = MagicMock(status_code=200, text=_ATOM_FIXTURE)
        with patch("requests.get", return_value=mock_response):
            result = fetch_arxiv(
                queries=[("q1", ["T1"]), ("q2", ["T2", "T6"])],
                rate_limiter=_no_op_rate_limiter(),
            )

        topics_seen = [tuple(r.feeds_topics) for r in result.records]
        assert ("T1",) in topics_seen
        assert ("T2", "T6") in topics_seen


class TestFetchArxivDegradation:
    def test_http_error_recorded_not_raised(self):
        mock_response = MagicMock(status_code=500)
        mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        with patch("requests.get", return_value=mock_response):
            result = fetch_arxiv(queries=[("q", ["T1"])], rate_limiter=_no_op_rate_limiter())

        assert result.records == []
        assert len(result.errors) == 1
        assert result.errors[0]["item"] == "q"

    def test_connection_error_recorded_not_raised(self):
        with patch("requests.get", side_effect=requests.ConnectionError("no network")):
            result = fetch_arxiv(queries=[("q", ["T1"])], rate_limiter=_no_op_rate_limiter())

        assert result.records == []
        assert len(result.errors) == 1

    def test_one_bad_query_does_not_block_others(self):
        good_response = MagicMock(status_code=200, text=_ATOM_FIXTURE)

        def flaky_get(url, params=None, timeout=None):
            if params["search_query"] == "all:bad query":
                raise requests.ConnectionError("boom")
            return good_response

        with patch("requests.get", side_effect=flaky_get):
            result = fetch_arxiv(
                queries=[("bad query", ["T1"]), ("good query", ["T2"])],
                rate_limiter=_no_op_rate_limiter(),
            )

        assert len(result.records) == 1
        assert len(result.errors) == 1
        assert result.errors[0]["item"] == "bad query"


class TestFetchArxivCheckpointing:
    def test_query_already_done_is_skipped(self, tmp_path):
        checkpoint = Checkpoint(tmp_path / "arxiv.json")
        checkpoint.mark_done("q")
        mock_get = MagicMock()

        with patch("requests.get", mock_get):
            result = fetch_arxiv(
                queries=[("q", ["T1"])],
                rate_limiter=_no_op_rate_limiter(),
                checkpoint=checkpoint,
            )

        mock_get.assert_not_called()
        assert result.records == []

    def test_successful_query_marked_done(self, tmp_path):
        checkpoint = Checkpoint(tmp_path / "arxiv.json")
        mock_response = MagicMock(status_code=200, text=_ATOM_FIXTURE)

        with patch("requests.get", return_value=mock_response):
            fetch_arxiv(
                queries=[("q", ["T1"])],
                rate_limiter=_no_op_rate_limiter(),
                checkpoint=checkpoint,
            )

        assert checkpoint.is_done("q") is True

    def test_failed_query_not_marked_done(self, tmp_path):
        checkpoint = Checkpoint(tmp_path / "arxiv.json")

        with patch("requests.get", side_effect=requests.ConnectionError("boom")):
            fetch_arxiv(
                queries=[("q", ["T1"])],
                rate_limiter=_no_op_rate_limiter(),
                checkpoint=checkpoint,
            )

        assert checkpoint.is_done("q") is False
