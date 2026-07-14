"""Unit tests for symbolic/security_scan.py."""

from verityai.symbolic.security_scan import scan_for_dangerous_patterns


class TestDangerousImports:
    def test_import_os_is_flagged(self):
        findings = scan_for_dangerous_patterns("import os\n")
        assert any("os" in f.construct for f in findings)

    def test_import_subprocess_is_flagged(self):
        findings = scan_for_dangerous_patterns("import subprocess\n")
        assert len(findings) == 1

    def test_from_os_import_is_flagged(self):
        findings = scan_for_dangerous_patterns("from os import system\n")
        assert len(findings) == 1

    def test_aliased_import_is_still_flagged(self):
        findings = scan_for_dangerous_patterns("import os as o\n")
        assert len(findings) == 1

    def test_safe_import_not_flagged(self):
        findings = scan_for_dangerous_patterns("import math\nx = math.sqrt(4)\n")
        assert findings == []


class TestDangerousCalls:
    def test_eval_is_flagged(self):
        findings = scan_for_dangerous_patterns("x = eval('1 + 1')\n")
        assert len(findings) == 1
        assert "eval" in findings[0].construct

    def test_exec_is_flagged(self):
        findings = scan_for_dangerous_patterns("exec('print(1)')\n")
        assert len(findings) == 1

    def test_dunder_import_is_flagged(self):
        findings = scan_for_dangerous_patterns("m = __import__('os')\n")
        assert len(findings) == 1

    def test_os_system_is_flagged(self):
        findings = scan_for_dangerous_patterns("import os\nos.system('ls')\n")
        assert len(findings) == 2  # import + call

    def test_subprocess_run_is_flagged(self):
        code = "import subprocess\nsubprocess.run(['ls'])\n"
        findings = scan_for_dangerous_patterns(code)
        assert any("subprocess.run" in f.construct for f in findings)

    def test_pickle_loads_is_flagged(self):
        code = "import pickle\npickle.loads(b'')\n"
        findings = scan_for_dangerous_patterns(code)
        assert any("pickle.loads" in f.construct for f in findings)


class TestCleanCodeIsNotFlagged:
    def test_ordinary_assert_based_snippet(self):
        code = "def add(a, b):\n    result = a + b\n    assert result == a + b\n    return result\n"
        assert scan_for_dangerous_patterns(code) == []

    def test_loops_and_conditionals_not_flagged(self):
        code = "total = 0\nfor i in range(10):\n    if i > 5:\n        total += i\n"
        assert scan_for_dangerous_patterns(code) == []


class TestSyntaxError:
    def test_syntax_error_returns_syntax_error_finding_not_a_crash(self):
        findings = scan_for_dangerous_patterns("def f(:\n")
        assert len(findings) == 1
        assert findings[0].construct == "syntax_error"
