"""Unit tests for evidence/fetchers/z3_docs.py.

`requests.get` is monkeypatched -- no real network traffic.
"""

from unittest.mock import MagicMock, patch

import requests

from verityai.evidence.fetchers.z3_docs import fetch_z3_docs


class TestFetchZ3DocsSuccess:
    def test_fetches_each_pinned_page(self):
        mock_response = MagicMock(
            status_code=200, text="# Strings\nZ3's string solver is incomplete."
        )
        with patch("requests.get", return_value=mock_response):
            result = fetch_z3_docs(pages=[("Strings", "https://example.com/strings.md", ["T6"])])

        assert len(result.records) == 1
        record = result.records[0]
        assert record.source == "z3_docs"
        assert record.license == "MIT"
        assert record.content["title"] == "Strings"
        assert "incomplete" in record.content["text"]
        assert record.feeds_topics == ["T6"]
        assert result.errors == []

    def test_multiple_pages_all_fetched(self):
        mock_response = MagicMock(status_code=200, text="content")
        with patch("requests.get", return_value=mock_response):
            result = fetch_z3_docs(
                pages=[
                    ("Strings", "https://example.com/strings.md", ["T6"]),
                    ("Arrays", "https://example.com/arrays.md", ["T6"]),
                ]
            )

        assert len(result.records) == 2


class TestFetchZ3DocsDegradation:
    def test_http_error_recorded_not_raised(self):
        mock_response = MagicMock(status_code=404)
        mock_response.raise_for_status.side_effect = requests.HTTPError("404")
        with patch("requests.get", return_value=mock_response):
            result = fetch_z3_docs(pages=[("Strings", "https://example.com/gone.md", ["T6"])])

        assert result.records == []
        assert len(result.errors) == 1

    def test_one_bad_page_does_not_block_others(self):
        good_response = MagicMock(status_code=200, text="ok")

        def flaky_get(url, timeout=None):
            if "gone" in url:
                raise requests.ConnectionError("boom")
            return good_response

        with patch("requests.get", side_effect=flaky_get):
            result = fetch_z3_docs(
                pages=[
                    ("Gone", "https://example.com/gone.md", ["T6"]),
                    ("Ok", "https://example.com/ok.md", ["T6"]),
                ]
            )

        assert len(result.records) == 1
        assert len(result.errors) == 1
