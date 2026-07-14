"""Execution-based ground truth oracle for the evaluation harness.

Phase 3's original ground truth (`baselines.classify_ground_truth`) compares
generated code *verbatim* against two fixed strings -- against a live model
this hits "novel" close to 100% of the time (confirmed empirically, see
docs/PHASE_3_METHODOLOGY.md's "Real run #1"), because no model reproduces a
reference string exactly. This module replaces that comparison with an
actual behavioral check: call the generated function with known inputs (see
evaluation/benchmarks/*.json's `test_cases`) and see whether it behaves like
the reference solution or the known-buggy variant would.

Isolation model (read before assuming this is a full sandbox): each
candidate snippet runs in its own **subprocess**, not `exec()` in-process,
with a wall-clock timeout and (on POSIX) CPU-time and address-space
`resource` limits via `preexec_fn`. That contains a runaway loop or a
memory bomb. It does **not** provide network or filesystem isolation --
there is no container or namespace here, unlike the Docker image this
project ships (see docs/PHASE_4_PART_D.md, which documents the same gap
for `security_scan.py`'s static blocklist: a real sandbox is still an open
item, not a solved one). Because of that gap, this module runs
`security_scan.scan_for_dangerous_patterns` on the code FIRST and refuses
to execute anything it flags -- belt-and-suspenders on top of process
isolation, not a substitute for it.
"""

import contextlib
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from verityai.symbolic.security_scan import scan_for_dangerous_patterns

if TYPE_CHECKING:
    from verityai.evaluation.baselines import ExecutionTestCase

# Wall-clock ceiling for the whole subprocess call (all test cases combined).
DEFAULT_TIMEOUT_SECONDS = 5.0
# POSIX-only per-process ceilings (see _posix_resource_limits). Generous
# enough for any benchmark-scale snippet; tight enough to kill a runaway
# loop or an allocation bomb well before it threatens the host.
CPU_SECONDS_LIMIT = 3
MEMORY_BYTES_LIMIT = 256 * 1024 * 1024  # 256 MB

# Runs inside the child subprocess: receives {"code": ..., "function_name":
# ..., "test_cases": [{"args": [...], "has_expected": bool, "expected": ...}]}
# as a JSON blob on stdin, emits {"results": [{"ok": bool, "returned": ...,
# "error": str|None}, ...]} as JSON on stdout. Kept as a standalone string
# (not a separate .py file) so there's exactly one file to keep in sync with
# OracleResult's expectations, and so it has zero import-time dependency on
# the rest of this package inside the child process.
#
# Candidate code's own stdout is redirected away during exec() and every
# test-case call -- found the hard way (a live run against llama3.2, not
# by inspection): LLM-generated code very commonly includes a top-level
# `print(...)` demonstrating the function, and since that code runs via
# plain `exec()` in this same process, its print() output was landing on
# the SAME stdout this script uses for its final JSON result, corrupting
# the single-line JSON the parent expects to parse. Every candidate
# reaching this bug reported "novel" (JSON parse failure), not "correct"
# or "buggy" -- silently invalidating the ground truth for any task whose
# generated code happened to print anything.
_RUNNER_SCRIPT = """
import contextlib
import io
import json
import sys

payload = json.loads(sys.stdin.read())
namespace: dict = {}
results = []
_devnull = io.StringIO()

try:
    with contextlib.redirect_stdout(_devnull):
        exec(payload["code"], namespace)
except BaseException as e:
    print(json.dumps({"load_error": f"{type(e).__name__}: {e}"}))
    sys.exit(0)

func = namespace.get(payload["function_name"])
if func is None:
    print(json.dumps({"load_error": f"function {payload['function_name']!r} not defined"}))
    sys.exit(0)

for case in payload["test_cases"]:
    try:
        with contextlib.redirect_stdout(_devnull):
            returned = func(*case["args"])
        if case["has_expected"] and returned != case["expected"]:
            results.append({"ok": False, "returned": returned, "error": None})
        else:
            results.append({"ok": True, "returned": returned, "error": None})
    except BaseException as e:
        results.append({"ok": False, "returned": None, "error": f"{type(e).__name__}: {e}"})

print(json.dumps({"results": results}))
"""


@dataclass
class OracleResult:
    """Outcome of running one candidate snippet's function against its test cases."""

    status: str  # "correct" | "buggy" | "novel" -- matches classify_ground_truth's vocabulary
    detail: str
    passed_cases: int = 0
    total_cases: int = 0


def _posix_resource_limits():
    """A `preexec_fn` applying CPU-time and address-space limits, POSIX only.

    Returns None on non-POSIX platforms (Windows) -- the `resource` module
    doesn't exist there, so the child is left with only the wall-clock
    `subprocess.run(timeout=...)` guard in that case.

    Each limit is applied independently and failures are swallowed: macOS
    in particular rejects RLIMIT_AS ("current limit exceeds maximum
    limit") even though RLIMIT_CPU works fine on the same machine --
    confirmed by hand, not assumed. Without isolating the two, one
    platform's unsupported limit would silently take down subprocess
    creation entirely (`preexec_fn` exceptions abort the child launch),
    losing the CPU-time protection along with it. Linux (CI, the Docker
    image) supports both.
    """
    try:
        import resource
    except ImportError:
        return None

    def _limit():
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS_LIMIT, CPU_SECONDS_LIMIT))
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES_LIMIT, MEMORY_BYTES_LIMIT))

    return _limit


def run_against_test_cases(
    code: str,
    function_name: str,
    test_cases: list["ExecutionTestCase"],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> OracleResult:
    """Execute `code` in an isolated subprocess and check `function_name`
    against every test case.

    Returns "correct" only if every test case passes; "buggy" if the
    function is found and callable but at least one test case fails
    (wrong return value or an unexpected exception); "novel" if nothing
    can be concluded (dangerous code refused, syntax error, function
    missing, subprocess crash/timeout) -- consistent with
    classify_ground_truth's existing "no oracle for it" meaning.
    """
    if not test_cases:
        return OracleResult(status="novel", detail="no test_cases defined for this task")

    findings = [f for f in scan_for_dangerous_patterns(code) if f.construct != "syntax_error"]
    if findings:
        constructs = ", ".join(f.construct for f in findings)
        return OracleResult(
            status="novel",
            detail=f"execution refused: dangerous constructs detected ({constructs})",
        )

    payload = {
        "code": code,
        "function_name": function_name,
        "test_cases": [
            {"args": tc.args, "has_expected": tc.has_expected, "expected": tc.expected}
            for tc in test_cases
        ],
    }

    try:
        proc = subprocess.run(  # noqa: S603 -- payload is JSON on stdin, not shell input
            [sys.executable, "-c", _RUNNER_SCRIPT],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            preexec_fn=_posix_resource_limits(),
        )
    except subprocess.TimeoutExpired:
        return OracleResult(status="novel", detail=f"execution timed out after {timeout_seconds}s")

    if proc.returncode != 0:
        return OracleResult(
            status="novel",
            detail=f"subprocess exited {proc.returncode}: {proc.stderr[-500:]}",
        )

    try:
        parsed: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return OracleResult(
            status="novel", detail=f"could not parse oracle output: {proc.stdout[:500]!r}"
        )

    if "load_error" in parsed:
        return OracleResult(status="novel", detail=parsed["load_error"])

    results = parsed["results"]
    passed = sum(1 for r in results if r["ok"])
    total = len(results)

    if passed == total:
        return OracleResult(
            status="correct", detail="all test cases passed", passed_cases=passed, total_cases=total
        )

    first_failure = next(r for r in results if not r["ok"])
    detail = first_failure["error"] or f"expected != returned ({first_failure['returned']!r})"
    return OracleResult(
        status="buggy", detail=f"test case failed: {detail}", passed_cases=passed, total_cases=total
    )
