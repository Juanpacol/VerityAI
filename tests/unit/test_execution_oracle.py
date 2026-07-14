"""Unit tests for evaluation/execution_oracle.py.

These actually launch a subprocess (no mocking of subprocess.run) --
that's the entire point of the module, and a fake would hide the exact
platform quirks this module exists to handle (see the RLIMIT_AS macOS
finding documented in _posix_resource_limits' docstring).
"""

from verityai.evaluation.baselines import ExecutionTestCase
from verityai.evaluation.execution_oracle import run_against_test_cases


def case(args=None, expected=None, has_expected=False):
    return ExecutionTestCase(args=args or [], expected=expected, has_expected=has_expected)


class TestCorrectCode:
    def test_single_test_case_passes(self):
        code = "def add_two():\n    return 7\n"
        result = run_against_test_cases(code, "add_two", [case(expected=7, has_expected=True)])
        assert result.status == "correct"
        assert result.passed_cases == 1
        assert result.total_cases == 1

    def test_multiple_test_cases_all_pass(self):
        code = "def add(a, b):\n    return a + b\n"
        cases = [
            case(args=[3, 4], expected=7, has_expected=True),
            case(args=[10, -3], expected=7, has_expected=True),
        ]
        result = run_against_test_cases(code, "add", cases)
        assert result.status == "correct"
        assert result.passed_cases == 2

    def test_assert_only_function_with_no_expected_value(self):
        """Some benchmark functions only assert internally and return
        nothing -- 'did not raise' is the entire signal."""
        code = "def check():\n    x = 1\n    assert x == 1\n"
        result = run_against_test_cases(code, "check", [case()])
        assert result.status == "correct"


class TestBuggyCode:
    def test_wrong_return_value(self):
        code = "def add_two():\n    return 6\n"
        result = run_against_test_cases(code, "add_two", [case(expected=7, has_expected=True)])
        assert result.status == "buggy"
        assert result.passed_cases == 0

    def test_raises_assertion_error(self):
        code = "def check():\n    x = 1\n    assert x == 2\n"
        result = run_against_test_cases(code, "check", [case()])
        assert result.status == "buggy"
        assert "AssertionError" in result.detail

    def test_one_of_several_cases_fails(self):
        code = "def add(a, b):\n    return a - b\n"  # wrong operator
        cases = [
            case(args=[3, 4], expected=7, has_expected=True),
            case(args=[10, 0], expected=10, has_expected=True),  # happens to pass anyway
        ]
        result = run_against_test_cases(code, "add", cases)
        assert result.status == "buggy"
        assert result.passed_cases == 1
        assert result.total_cases == 2


class TestCandidateStdoutIsolation:
    """Regression tests: found via a real live-model run (llama3.2), not
    by inspection. LLM-generated code very commonly includes a top-level
    `print(...)` demonstrating the function -- since candidate code runs
    via plain `exec()` inside the same subprocess that reports its own
    JSON result on stdout, an un-isolated print() from the candidate
    corrupted the single-line JSON the parent expects, making every such
    task silently report "novel" instead of "correct"/"buggy"."""

    def test_module_level_print_does_not_corrupt_the_result(self):
        code = 'def add_two():\n    return 7\n\nprint("demo:", add_two())\n'
        result = run_against_test_cases(code, "add_two", [case(expected=7, has_expected=True)])
        assert result.status == "correct"

    def test_print_inside_the_function_itself_does_not_corrupt_the_result(self):
        code = 'def add_two():\n    print("computing...")\n    return 7\n'
        result = run_against_test_cases(code, "add_two", [case(expected=7, has_expected=True)])
        assert result.status == "correct"

    def test_print_alongside_a_genuine_bug_still_reports_buggy_not_novel(self):
        code = 'def add_two():\n    print("computing...")\n    return 6\n'
        result = run_against_test_cases(code, "add_two", [case(expected=7, has_expected=True)])
        assert result.status == "buggy"


class TestUnexecutableCode:
    def test_no_test_cases_is_novel(self):
        result = run_against_test_cases("def f(): return 1", "f", [])
        assert result.status == "novel"
        assert "no test_cases" in result.detail

    def test_syntax_error_is_novel(self):
        result = run_against_test_cases("def f(:\n    return 1\n", "f", [case()])
        assert result.status == "novel"

    def test_missing_function_is_novel(self):
        code = "def wrong_name():\n    return 1\n"
        result = run_against_test_cases(code, "expected_name", [case()])
        assert result.status == "novel"
        assert "not defined" in result.detail

    def test_timeout_is_novel(self):
        code = "def f():\n    while True:\n        pass\n"
        result = run_against_test_cases(code, "f", [case()], timeout_seconds=1.0)
        assert result.status == "novel"
        assert "timed out" in result.detail


class TestDangerousCodeIsRefused:
    """The oracle must never execute code the static security scanner
    flags -- process isolation alone is not treated as sufficient (see
    module docstring's isolation-model caveat: no network/filesystem
    sandbox exists yet)."""

    def test_os_system_is_refused_without_executing(self):
        code = "import os\ndef f():\n    os.system('touch /tmp/should_not_exist_oracle_test')\n    return 1\n"
        result = run_against_test_cases(code, "f", [case(expected=1, has_expected=True)])
        assert result.status == "novel"
        assert "refused" in result.detail

        import os

        assert not os.path.exists("/tmp/should_not_exist_oracle_test")

    def test_eval_is_refused(self):
        code = "def f():\n    return eval('1')\n"
        result = run_against_test_cases(code, "f", [case(expected=1, has_expected=True)])
        assert result.status == "novel"
        assert "refused" in result.detail


class TestIsolation:
    def test_generated_code_cannot_affect_parent_process_state(self):
        """Runs in a real subprocess -- mutating sys.path or builtins in
        the child must not leak back to this test process."""
        import sys

        marker = "___oracle_isolation_marker___"
        code = f"import sys\nsys.path.append('{marker}')\ndef f():\n    return 1\n"
        run_against_test_cases(code, "f", [case(expected=1, has_expected=True)])
        assert marker not in sys.path
