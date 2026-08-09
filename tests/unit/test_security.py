"""Tests for security scanning.

`TestKnownFalsePositive` pins down a real dogfooding finding: scanning this
repository's own code flagged `GraphQuery.context_for` for check-then-act
race, and reading it shows an ordinary local dict being built up, not a
shared-resource race. The detector's own docstring says this is expected --
this test makes sure the caveat that explains it is actually wired to the
rule that produces it, not just written in a comment nobody reads.
"""

from verityai.core.models import Rule, VerificationStatus
from verityai.reliability.report import render_report
from verityai.reliability.security import (
    BUILTIN_SECURITY_RULES,
    RULE_CAVEATS,
    caveats_for,
    extract_all_facts,
    scan_code,
    scan_file,
    scan_repo,
)

VULNERABLE_SQL = """
def get_user(conn, name):
    query = "SELECT * FROM users WHERE name = " + name
    return conn.execute(query)
"""

SAFE_SQL = """
def get_user(conn, name):
    return conn.execute("SELECT * FROM users WHERE name = ?", (name,))
"""

VULNERABLE_RACE = """
def add_if_missing(cache, key, value):
    if key not in cache:
        cache[key] = value
"""

SAFE_RACE = """
def add_if_missing(cache, key, value, lock):
    with lock:
        if key not in cache:
            cache[key] = value
"""


class TestSQLInjection:
    def test_dynamic_query_is_flagged(self):
        findings = scan_code(VULNERABLE_SQL)

        assert len(findings) == 1
        assert findings[0].rule_id == "sql-injection"
        assert findings[0].status is VerificationStatus.FAIL

    def test_parameterized_query_is_not_flagged(self):
        assert scan_code(SAFE_SQL) == []

    def test_finding_carries_severity_from_the_rule(self):
        findings = scan_code(VULNERABLE_SQL)

        assert findings[0].severity == "high"

    def test_finding_carries_the_path(self):
        findings = scan_code(VULNERABLE_SQL, path="src/db.py")

        assert findings[0].path == "src/db.py"


class TestCheckThenActRace:
    def test_unguarded_check_then_act_is_flagged(self):
        findings = scan_code(VULNERABLE_RACE)

        assert len(findings) == 1
        assert findings[0].rule_id == "check-then-act-race"

    def test_lock_guarded_check_then_act_is_not_flagged(self):
        assert scan_code(SAFE_RACE) == []


class TestNoFindings:
    def test_ordinary_code_produces_nothing(self):
        assert scan_code("def add(a, b):\n    return a + b\n") == []

    def test_empty_code_produces_nothing(self):
        assert scan_code("") == []

    def test_unparseable_code_produces_nothing_not_a_crash(self):
        assert scan_code("def f(\n") == []


class TestKnownFalsePositive:
    """A real finding from dogfooding this against the project's own code."""

    def test_the_race_rule_has_a_documented_caveat(self):
        assert "check-then-act-race" in RULE_CAVEATS

    def test_the_caveat_explains_the_shape_only_limitation(self):
        caveat = RULE_CAVEATS["check-then-act-race"]

        assert "syntactic shape" in caveat
        assert "local dict" in caveat or "cannot tell" in caveat

    def test_caveats_for_surfaces_only_rules_that_actually_fired(self):
        sql_findings = scan_code(VULNERABLE_SQL)

        caveats = caveats_for(sql_findings)

        assert caveats == []  # sql-injection has no documented caveat

    def test_caveats_for_surfaces_the_race_caveat_when_it_fires(self):
        race_findings = scan_code(VULNERABLE_RACE)

        assert caveats_for(race_findings) == [RULE_CAVEATS["check-then-act-race"]]

    def test_the_caveat_is_rendered_in_the_report(self):
        findings = scan_code(VULNERABLE_RACE, path="a.py")
        from verityai.core.models import ReliabilityReport

        report = ReliabilityReport(findings=findings, files_scanned=1)
        rendered = render_report(report, caveats=caveats_for(findings))

        assert "note:" in rendered
        assert "syntactic shape" in rendered


class TestInjectedRules:
    def test_a_caller_can_run_a_subset_of_rules(self):
        sql_only = [r for r in BUILTIN_SECURITY_RULES if r.id == "sql-injection"]

        findings = scan_code(VULNERABLE_RACE, rules=sql_only)

        assert findings == []

    def test_a_caller_can_add_project_specific_rules(self):
        custom = Rule(
            id="custom-rule",
            name="Custom",
            formal_spec="PRE: sql_query_built_dynamically; POST: this_never_appears",
        )

        findings = scan_code(VULNERABLE_SQL, rules=[custom])

        assert [f.rule_id for f in findings] == ["custom-rule"]


class TestFileAndRepoScanning:
    def test_scan_file_reads_and_scans(self, tmp_path):
        path = tmp_path / "vuln.py"
        path.write_text(VULNERABLE_SQL)

        findings = scan_file(path, rel_path="vuln.py")

        assert findings[0].path == "vuln.py"

    def test_scan_file_on_unreadable_path_returns_nothing(self, tmp_path):
        assert scan_file(tmp_path / "does_not_exist.py") == []

    def test_scan_repo_covers_every_python_file(self, tmp_path):
        (tmp_path / "vuln.py").write_text(VULNERABLE_SQL)
        (tmp_path / "safe.py").write_text(SAFE_SQL)
        (tmp_path / "notes.md").write_text("# notes")

        report = scan_repo(tmp_path)

        assert report.files_scanned == 2
        assert len(report.violations) == 1
        assert report.violations[0].path == "vuln.py"

    def test_scan_repo_excludes_vendored_projects(self, tmp_path):
        """Same scope discipline as the code graph (ADR-0006) -- a vendored
        dependency's vulnerabilities are not this project's to fix."""
        (tmp_path / "mine.py").write_text(VULNERABLE_SQL)
        vendored = tmp_path / "vendor" / "lib"
        vendored.mkdir(parents=True)
        (vendored / "setup.py").write_text("")
        (vendored / "theirs.py").write_text(VULNERABLE_SQL)

        report = scan_repo(tmp_path)

        assert report.files_scanned == 1
        assert report.violations[0].path == "mine.py"

    def test_scan_repo_reports_zero_violations_cleanly(self, tmp_path):
        (tmp_path / "safe.py").write_text(SAFE_SQL)

        report = scan_repo(tmp_path)

        assert report.is_clean
        assert report.files_scanned == 1


class TestExtractAllFacts:
    def test_combines_both_extractors(self):
        combined = VULNERABLE_SQL + VULNERABLE_RACE

        facts = extract_all_facts(combined)

        assert "sql_query_built_dynamically" in facts
        assert "check_then_act_on_shared_resource" in facts
