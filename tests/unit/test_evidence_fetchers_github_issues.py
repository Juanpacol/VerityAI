"""Unit tests for evidence/fetchers/github_issues.py.

`subprocess.run` is monkeypatched -- no real `gh` CLI invocation, no
network traffic.
"""

import json
from unittest.mock import MagicMock, patch

from verityai.evidence.fetchers.base import Checkpoint
from verityai.evidence.fetchers.github_issues import fetch_github_issues

_SAMPLE_ISSUES = [
    {
        "title": "Invalid model with string theory",
        "url": "https://github.com/Z3Prover/z3/issues/8194",
        "state": "open",
        "labels": [{"name": "strings"}],
        "body": "Z3 reports sat but generates an invalid model. " * 20,
    }
]


def _completed_process(returncode=0, stdout="[]", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestFetchGithubIssuesSuccess:
    def test_parses_issues_into_records(self):
        proc = _completed_process(stdout=json.dumps(_SAMPLE_ISSUES))
        with patch("subprocess.run", return_value=proc):
            result = fetch_github_issues(queries=[("string theory", "Z3Prover/z3", ["T6"])])

        assert len(result.records) == 1
        record = result.records[0]
        assert record.source == "github_issues"
        assert record.retrieval_method == "gh_cli"
        assert record.content["title"] == "Invalid model with string theory"
        assert record.content["labels"] == ["strings"]
        assert len(record.content["excerpt"]) <= 500
        assert record.feeds_topics == ["T6"]
        assert result.errors == []

    def test_global_query_has_no_repo_flag(self):
        proc = _completed_process(stdout=json.dumps(_SAMPLE_ISSUES))
        mock_run = MagicMock(return_value=proc)
        with patch("subprocess.run", mock_run):
            fetch_github_issues(queries=[("AI generated code bug", None, ["T4"])])

        args = mock_run.call_args[0][0]
        assert "-R" not in args

    def test_repo_scoped_query_includes_repo_flag(self):
        proc = _completed_process(stdout=json.dumps(_SAMPLE_ISSUES))
        mock_run = MagicMock(return_value=proc)
        with patch("subprocess.run", mock_run):
            fetch_github_issues(queries=[("string theory", "Z3Prover/z3", ["T6"])])

        args = mock_run.call_args[0][0]
        assert "-R" in args
        assert "Z3Prover/z3" in args


class TestFetchGithubIssuesDegradation:
    def test_missing_gh_binary_recorded_not_raised(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = fetch_github_issues(queries=[("q", None, ["T4"])])

        assert result.records == []
        assert len(result.errors) == 1
        assert "gh CLI not available" in result.errors[0]["error"]

    def test_nonzero_exit_recorded_not_raised(self):
        proc = _completed_process(returncode=1, stdout="", stderr="rate limit exceeded")
        with patch("subprocess.run", return_value=proc):
            result = fetch_github_issues(queries=[("q", None, ["T4"])])

        assert result.records == []
        assert "rate limit exceeded" in result.errors[0]["error"]

    def test_malformed_json_output_recorded_not_raised(self):
        proc = _completed_process(stdout="not json")
        with patch("subprocess.run", return_value=proc):
            result = fetch_github_issues(queries=[("q", None, ["T4"])])

        assert result.records == []
        assert len(result.errors) == 1

    def test_one_bad_query_does_not_block_others(self):
        good_proc = _completed_process(stdout=json.dumps(_SAMPLE_ISSUES))

        def flaky_run(args, **kwargs):
            if "bad query" in args:
                raise FileNotFoundError()
            return good_proc

        with patch("subprocess.run", side_effect=flaky_run):
            result = fetch_github_issues(
                queries=[("bad query", None, ["T4"]), ("good query", None, ["T4"])]
            )

        assert len(result.records) == 1
        assert len(result.errors) == 1


class TestFetchGithubIssuesCheckpointing:
    def test_completed_query_marked_done_and_skipped_on_resume(self, tmp_path):
        checkpoint = Checkpoint(tmp_path / "github_issues.json")
        proc = _completed_process(stdout=json.dumps(_SAMPLE_ISSUES))
        mock_run = MagicMock(return_value=proc)

        with patch("subprocess.run", mock_run):
            fetch_github_issues(queries=[("q", None, ["T4"])], checkpoint=checkpoint)
            mock_run.reset_mock()
            fetch_github_issues(queries=[("q", None, ["T4"])], checkpoint=checkpoint)

        mock_run.assert_not_called()
