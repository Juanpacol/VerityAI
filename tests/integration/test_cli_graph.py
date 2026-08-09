"""End-to-end tests for the `verity graph` commands.

Real temporary repositories, real SQLite, real AST parsing — no fakes. The
point is that the pieces are wired together and that the output tells a person
something true, including where it is uncertain.
"""

import pytest
from typer.testing import CliRunner

from verityai.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A repo with a non-lexical relationship worth finding."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()

    (tmp_path / "src" / "limits.py").write_text(
        '"""Throttling."""\n\n\n'
        "def apply_ceiling(count, maximum):\n"
        '    """Clamp a value."""\n'
        "    return min(count, maximum)\n\n\n"
        "def rate_limit_request(key, count):\n"
        '    """Apply rate limiting to a request."""\n'
        "    return apply_ceiling(count, 100)\n\n\n"
        "def orphan():\n"
        '    """Nothing calls this."""\n'
        "    return None\n"
    )
    (tmp_path / "tests" / "test_limits.py").write_text(
        "from limits import rate_limit_request\n\n\n"
        "def test_throttling():\n"
        '    assert rate_limit_request("k", 500) == 100\n'
    )
    (tmp_path / "notes.md").write_text("# notes\n")

    runner.invoke(app, ["init"])
    return tmp_path


class TestBuild:
    def test_build_reports_honest_coverage(self, project):
        result = runner.invoke(app, ["graph", "build"])

        assert result.exit_code == 0
        assert "2/2 Python files in the graph (100%)" in result.output
        assert "non-Python" in result.output

    def test_build_states_the_unresolved_count(self, project):
        """Unresolved calls are a fact about the graph, not a hidden detail."""
        result = runner.invoke(app, ["graph", "build"])

        assert "could not be resolved" in result.output
        assert "kept, not discarded" in result.output

    def test_rebuilding_is_incremental(self, project):
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "build"])

        assert result.exit_code == 0
        assert "2/2" in result.output

    def test_the_graph_persists_between_invocations(self, project):
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "find", "apply_ceiling"])

        assert result.exit_code == 0
        assert "limits.py" in result.output

    def test_a_syntax_error_is_surfaced(self, project):
        (project / "src" / "broken.py").write_text("def f(\n")

        result = runner.invoke(app, ["graph", "build"])

        assert "failed to parse" in result.output
        assert "broken.py" in result.output

    def test_build_requires_init(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["graph", "build"])

        assert result.exit_code == 1
        assert "verity init" in result.output


class TestFind:
    def test_find_reports_location_and_signature(self, project):
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "find", "rate_limit_request"])

        assert "limits.py" in result.output
        assert "rate_limit_request(key, count)" in result.output

    def test_an_unknown_symbol_exits_non_zero(self, project):
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "find", "does_not_exist"])

        assert result.exit_code == 1
        assert "No definition" in result.output


class TestContext:
    def test_context_finds_code_by_relationship(self, project):
        """The claim that justifies the graph, exercised end to end."""
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "context", "rate limiting"])

        assert "rate_limit_request" in result.output
        assert "apply_ceiling" in result.output

    def test_every_result_carries_a_reason(self, project):
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "context", "rate limiting"])

        assert "why:" in result.output

    def test_context_on_an_empty_graph_says_what_to_do(self, project):
        result = runner.invoke(app, ["graph", "context", "anything"])

        assert "verity graph build" in result.output


class TestDeps:
    def test_deps_report_both_directions(self, project):
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "deps", "tests/test_limits.py"])

        assert "imports:" in result.output
        assert "imported by:" in result.output
        assert "limits" in result.output


class TestCycles:
    def test_a_clean_project_exits_zero(self, project):
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "cycles"])

        assert result.exit_code == 0
        assert "No circular imports" in result.output

    def test_a_cycle_exits_non_zero_so_it_works_in_ci(self, project):
        (project / "src" / "a.py").write_text("import b\n")
        (project / "src" / "b.py").write_text("import a\n")
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "cycles"])

        assert result.exit_code == 1
        assert "cycle:" in result.output


class TestUntested:
    def test_untested_code_is_listed(self, project):
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "untested"])

        assert "orphan" in result.output

    def test_tested_code_is_not_listed(self, project):
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "untested"])

        assert "rate_limit_request" not in result.output

    def test_the_over_reporting_caveat_is_always_printed(self, project):
        """This number would be confidently wrong without its caveat."""
        runner.invoke(app, ["graph", "build"])

        result = runner.invoke(app, ["graph", "untested"])

        assert "Over-approximation" in result.output
        assert "coverage.py" in result.output


class TestTaskFiles:
    def test_relevant_files_can_be_recorded(self, project):
        """The gap dogfooding exposed: the first real handoff had no way to
        populate RELEVANT FILES."""
        runner.invoke(
            app, ["task", "add throttling", "-f", "src/limits.py", "-f", "tests/test_limits.py"]
        )

        result = runner.invoke(app, ["handoff"])

        assert "src/limits.py" in result.output
        assert "tests/test_limits.py" in result.output
