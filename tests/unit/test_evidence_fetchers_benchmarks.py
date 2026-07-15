"""Unit tests for evidence/fetchers/benchmarks.py (HumanEval + MBPP).

`requests.get` is monkeypatched -- no real downloads. Fixtures are 3-line
JSONL payloads matching each dataset's real schema.
"""

import gzip
import json
from unittest.mock import MagicMock, patch

from verityai.evidence.fetchers.base import Checkpoint
from verityai.evidence.fetchers.benchmarks import fetch_humaneval, fetch_mbpp

_HUMANEVAL_PROBLEMS = [
    {
        "task_id": "HumanEval/0",
        "prompt": "def f(x: int) -> int:\n    return x\n",
        "canonical_solution": "    return x\n",
        "entry_point": "f",
    },
    {
        "task_id": "HumanEval/1",
        "prompt": "def g(x: int) -> int:\n    return x + 1\n",
        "canonical_solution": "    return x + 1\n",
        "entry_point": "g",
    },
]

_MBPP_PROBLEMS = [
    {
        "task_id": 1,
        "text": "Write a function to add two numbers.",
        "code": "def add(a, b):\n    return a + b\n",
    },
    {
        "task_id": 2,
        "text": "Write a function to subtract.",
        "code": "def sub(a, b):\n    return a - b\n",
    },
]


def _humaneval_response():
    jsonl = "\n".join(json.dumps(p) for p in _HUMANEVAL_PROBLEMS)
    return MagicMock(status_code=200, content=gzip.compress(jsonl.encode("utf-8")))


def _mbpp_response():
    jsonl = "\n".join(json.dumps(p) for p in _MBPP_PROBLEMS)
    return MagicMock(status_code=200, text=jsonl)


class TestFetchHumaneval:
    def test_parses_all_problems(self):
        with patch("requests.get", return_value=_humaneval_response()):
            result = fetch_humaneval()

        assert len(result.records) == 2
        assert result.errors == []
        assert result.records[0].source == "humaneval"
        assert result.records[0].license == "MIT"
        assert result.records[0].content["entry_point"] == "f"

    def test_respects_limit(self):
        with patch("requests.get", return_value=_humaneval_response()):
            result = fetch_humaneval(limit=1)

        assert len(result.records) == 1

    def test_http_failure_recorded_not_raised(self):
        with patch("requests.get", side_effect=ConnectionError("no network")):
            result = fetch_humaneval()

        assert result.records == []
        assert len(result.errors) == 1

    def test_checkpoint_skips_already_done_task(self, tmp_path):
        checkpoint = Checkpoint(tmp_path / "humaneval.json")
        checkpoint.mark_done("HumanEval/0")

        with patch("requests.get", return_value=_humaneval_response()):
            result = fetch_humaneval(checkpoint=checkpoint)

        task_ids = {r.content["task_id"] for r in result.records}
        assert task_ids == {"HumanEval/1"}


class TestFetchMbpp:
    def test_parses_all_problems(self):
        with patch("requests.get", return_value=_mbpp_response()):
            result = fetch_mbpp()

        assert len(result.records) == 2
        assert result.errors == []
        assert result.records[0].source == "mbpp"
        assert result.records[0].license == "Apache-2.0"
        assert result.records[0].content["task_id"] == 1

    def test_respects_limit(self):
        with patch("requests.get", return_value=_mbpp_response()):
            result = fetch_mbpp(limit=1)

        assert len(result.records) == 1

    def test_http_failure_recorded_not_raised(self):
        with patch("requests.get", side_effect=ConnectionError("no network")):
            result = fetch_mbpp()

        assert result.records == []
        assert len(result.errors) == 1
