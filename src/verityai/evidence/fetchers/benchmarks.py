"""Fetchers for public code-generation benchmark datasets (HumanEval, MBPP)
-- the raw material T3's subset classifier runs against.

HumanEval: MIT-licensed (openai/human-eval). Downloaded as a gzipped
JSONL file and decompressed in memory.

MBPP: Apache-2.0-licensed (google-research/google-research). Downloaded as
a plain JSONL file.

Both are small, static, versioned datasets fetched once in full (no
pagination/rate-limiting concerns the way arXiv or GitHub search has).
"""

import gzip
import json
from datetime import datetime, timezone
from typing import Callable, Optional

import requests

from verityai.evidence.fetchers.base import Checkpoint, FetchResult
from verityai.evidence.hashing import compute_content_hash
from verityai.evidence.models import EvidenceRecord

HUMANEVAL_URL = "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz"
MBPP_URL = (
    "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/mbpp.jsonl"
)

HUMANEVAL_LICENSE = "MIT"
MBPP_LICENSE = "Apache-2.0"


def _humaneval_record(problem: dict) -> EvidenceRecord:
    content = {
        "task_id": problem["task_id"],
        "prompt": problem["prompt"],
        "canonical_solution": problem["canonical_solution"],
        "entry_point": problem["entry_point"],
    }
    content_hash = compute_content_hash(content)
    return EvidenceRecord(
        id=f"humaneval_{content_hash[:12]}",
        source="humaneval",
        source_url="https://github.com/openai/human-eval",
        license=HUMANEVAL_LICENSE,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        retrieval_method="file_download",
        content=content,
        content_hash=content_hash,
        feeds_topics=["T3"],
    )


def _mbpp_record(problem: dict) -> EvidenceRecord:
    content = {
        "task_id": problem["task_id"],
        "text": problem["text"],
        "code": problem["code"],
    }
    content_hash = compute_content_hash(content)
    return EvidenceRecord(
        id=f"mbpp_{content_hash[:12]}",
        source="mbpp",
        source_url="https://github.com/google-research/google-research/tree/master/mbpp",
        license=MBPP_LICENSE,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        retrieval_method="file_download",
        content=content,
        content_hash=content_hash,
        feeds_topics=["T3"],
    )


def fetch_humaneval(
    http_get: Optional[Callable] = None,
    checkpoint: Optional[Checkpoint] = None,
    limit: Optional[int] = None,
) -> FetchResult:
    http_get = http_get or requests.get
    result = FetchResult()
    try:
        response = http_get(HUMANEVAL_URL, timeout=30)
        response.raise_for_status()
        raw = gzip.decompress(response.content).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        result.errors.append({"item": "HumanEval.jsonl.gz", "error": str(e)})
        return result

    for i, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        if limit is not None and i >= limit:
            break
        try:
            problem = json.loads(line)
            task_id = problem["task_id"]
            if checkpoint is not None and checkpoint.is_done(task_id):
                continue
            result.records.append(_humaneval_record(problem))
            if checkpoint is not None:
                checkpoint.mark_done(task_id)
        except Exception as e:  # noqa: BLE001
            result.errors.append({"item": f"line {i}", "error": str(e)})

    return result


def fetch_mbpp(
    http_get: Optional[Callable] = None,
    checkpoint: Optional[Checkpoint] = None,
    limit: Optional[int] = None,
) -> FetchResult:
    http_get = http_get or requests.get
    result = FetchResult()
    try:
        response = http_get(MBPP_URL, timeout=30)
        response.raise_for_status()
        raw = response.text
    except Exception as e:  # noqa: BLE001
        result.errors.append({"item": "mbpp.jsonl", "error": str(e)})
        return result

    for i, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        if limit is not None and i >= limit:
            break
        try:
            problem = json.loads(line)
            task_id = str(problem["task_id"])
            if checkpoint is not None and checkpoint.is_done(task_id):
                continue
            result.records.append(_mbpp_record(problem))
            if checkpoint is not None:
                checkpoint.mark_done(task_id)
        except Exception as e:  # noqa: BLE001
            result.errors.append({"item": f"line {i}", "error": str(e)})

    return result
