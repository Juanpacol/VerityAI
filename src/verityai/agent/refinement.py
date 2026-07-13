"""Incremental verification: only re-verify functions whose source changed.

Interactive refinement means a session generates several revisions of the
same code ("make it thread-safe", then "add input validation"). Re-running
the full Z3 check on every turn wastes queries on functions the user didn't
touch. `IncrementalVerifier` caches a `VerificationResult` per (function
name, source hash) and only re-verifies functions whose source segment
actually changed since the last turn.
"""

import ast
import hashlib
import logging
from typing import Callable

from verityai.ontology.models import VerificationResult, VerificationStatus

logger = logging.getLogger(__name__)

VerifyFn = Callable[[str], VerificationResult]

# Worst-wins precedence when combining per-function results: lower number
# means "more severe", so a single FAIL outranks passing neighbors instead
# of being averaged away.
_STATUS_PRECEDENCE = {
    VerificationStatus.FAIL: 0,
    VerificationStatus.TIMEOUT: 1,
    VerificationStatus.UNKNOWN: 2,
    VerificationStatus.NOT_VERIFIED: 3,
    VerificationStatus.PASS: 4,
}


def extract_functions(code: str) -> dict[str, str]:
    """Map each top-level function name to its exact source segment.

    Returns {} if `code` has no top-level function def (e.g. a bare
    script) — callers should treat that as "nothing to key a cache on"
    and fall back to verifying the whole code string.
    """
    tree = ast.parse(code)
    functions = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            segment = ast.get_source_segment(code, node)
            if segment is not None:
                functions[node.name] = segment
    return functions


def _hash_source(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


class IncrementalVerifier:
    """Verifies code function-by-function, reusing results across calls.

    Not thread-safe and not meant to be shared across sessions — one
    instance corresponds to one conversation's evolving code.
    """

    def __init__(self, verify_fn: VerifyFn):
        """
        Args:
            verify_fn: Verifies a single self-contained code string (e.g.
                `Orchestrator.verify_code`) and returns its VerificationResult.
                A lone function definition is valid input on its own, since
                the AST converter processes top-level FunctionDefs directly.
        """
        self._verify_fn = verify_fn
        self._cache: dict[str, tuple[str, VerificationResult]] = {}
        self.last_reverified: list[str] = []

    def verify(self, code: str) -> VerificationResult:
        """Verify `code`, re-running the verifier only on changed functions.

        Falls back to a single uncached full-code verify when `code` has
        no top-level functions to key the cache on.
        """
        functions = extract_functions(code)
        if not functions:
            self.last_reverified = ["<module>"]
            return self._verify_fn(code)

        self.last_reverified = []
        results: dict[str, VerificationResult] = {}

        for name, source in functions.items():
            source_hash = _hash_source(source)
            cached = self._cache.get(name)
            if cached is not None and cached[0] == source_hash:
                results[name] = cached[1]
                continue

            result = self._verify_fn(source)
            self._cache[name] = (source_hash, result)
            results[name] = result
            self.last_reverified.append(name)

        # Drop functions that were removed since the last turn.
        for stale_name in set(self._cache) - set(functions):
            del self._cache[stale_name]

        return self._combine(results)

    def _combine(self, results: dict[str, VerificationResult]) -> VerificationResult:
        """Merge per-function results into one overall VerificationResult."""
        if not results:
            return VerificationResult(code_id="", status=VerificationStatus.PASS, confidence=1.0)

        worst = min(results.values(), key=lambda r: _STATUS_PRECEDENCE[r.status])
        all_violations = [v for r in results.values() for v in r.violations]

        return VerificationResult(
            code_id="",
            status=worst.status,
            confidence=min(r.confidence for r in results.values()),
            violations=all_violations,
            z3_result=worst.z3_result,
            metadata={
                "per_function": {name: r.status.value for name, r in results.items()},
                "reverified": list(self.last_reverified),
            },
        )
