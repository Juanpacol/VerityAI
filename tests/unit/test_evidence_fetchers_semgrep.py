"""Unit tests for evidence/fetchers/semgrep.py.

`subprocess.run` is monkeypatched with a canned `gh api` tree response --
no real `gh` CLI invocation.
"""

import json
from unittest.mock import MagicMock, patch

from verityai.evidence.fetchers.semgrep import fetch_semgrep_rule_counts

_SAMPLE_TREE = {
    "truncated": False,
    "tree": [
        {"path": "python/foo.yaml", "type": "blob"},
        {"path": "python/bar.yaml", "type": "blob"},
        {"path": "javascript/baz.yaml", "type": "blob"},
        {"path": "python/README.md", "type": "blob"},
        {"path": ".github/workflows/ci.yaml", "type": "blob"},
        {"path": "python", "type": "tree"},
    ],
}


def _completed_process(returncode=0, stdout="{}", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestFetchSemgrepSuccess:
    def test_counts_yaml_files_by_top_level_category(self):
        proc = _completed_process(stdout=json.dumps(_SAMPLE_TREE))
        with patch("subprocess.run", return_value=proc):
            result = fetch_semgrep_rule_counts()

        assert len(result.records) == 1
        record = result.records[0]
        assert record.source == "semgrep"
        assert record.license == "LGPL-2.1 + Commons Clause"
        assert record.content["total_rule_files"] == 3
        assert record.content["counts_by_category"] == {"python": 2, "javascript": 1}
        assert result.errors == []

    def test_dotfiles_and_non_blob_entries_excluded(self):
        proc = _completed_process(stdout=json.dumps(_SAMPLE_TREE))
        with patch("subprocess.run", return_value=proc):
            result = fetch_semgrep_rule_counts()

        # .github/workflows/ci.yaml must not appear, nor the tree-type entry
        assert "ci" not in str(result.records[0].content)

    def test_never_stores_rule_bodies(self):
        proc = _completed_process(stdout=json.dumps(_SAMPLE_TREE))
        with patch("subprocess.run", return_value=proc):
            result = fetch_semgrep_rule_counts()

        content = result.records[0].content
        assert set(content.keys()) == {"total_rule_files", "counts_by_category", "tree_truncated"}


class TestFetchSemgrepDegradation:
    def test_missing_gh_binary_recorded_not_raised(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = fetch_semgrep_rule_counts()

        assert result.records == []
        assert "gh CLI not available" in result.errors[0]["error"]

    def test_nonzero_exit_recorded_not_raised(self):
        proc = _completed_process(returncode=1, stderr="not found")
        with patch("subprocess.run", return_value=proc):
            result = fetch_semgrep_rule_counts()

        assert result.records == []
        assert "not found" in result.errors[0]["error"]

    def test_malformed_json_recorded_not_raised(self):
        proc = _completed_process(stdout="not json")
        with patch("subprocess.run", return_value=proc):
            result = fetch_semgrep_rule_counts()

        assert result.records == []
        assert len(result.errors) == 1
