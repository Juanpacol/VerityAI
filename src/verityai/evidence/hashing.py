"""Canonical content hashing, shared by fetchers (to derive record IDs) and
validation (to detect tampering/duplicates).
"""

import hashlib
import json
from typing import Any


def compute_content_hash(content: dict[str, Any]) -> str:
    """sha256 hex digest of `content`'s canonical JSON form.

    `sort_keys=True` and compact separators make this independent of key
    insertion order or incidental whitespace -- two records with the same
    logical content always hash identically.
    """
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
