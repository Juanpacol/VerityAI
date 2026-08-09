"""End-to-end tests for `verity reliability security|architecture`."""

import pytest
from typer.testing import CliRunner

from verityai.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestSecurityCommand:
    def test_a_vulnerable_file_is_flagged_and_exits_nonzero(self, project):
        (project / "db.py").write_text(
            "def get_user(conn, name):\n"
            '    query = "SELECT * FROM users WHERE name = " + name\n'
            "    return conn.execute(query)\n"
        )

        result = runner.invoke(app, ["reliability", "security"])

        assert result.exit_code == 1
        assert "SQL Injection" in result.output
        assert "1 violation" in result.output

    def test_a_safe_file_exits_zero(self, project):
        (project / "db.py").write_text(
            "def get_user(conn, name):\n"
            '    return conn.execute("SELECT * FROM users WHERE name = ?", (name,))\n'
        )

        result = runner.invoke(app, ["reliability", "security"])

        assert result.exit_code == 0
        assert "No violations found" in result.output

    def test_the_race_caveat_is_printed_when_it_fires(self, project):
        (project / "cache.py").write_text(
            "def add_if_missing(cache, key, value):\n"
            "    if key not in cache:\n"
            "        cache[key] = value\n"
        )

        result = runner.invoke(app, ["reliability", "security"])

        assert "note:" in result.output
        assert "syntactic shape" in result.output

    def test_can_target_an_explicit_root(self, tmp_path, project):
        other = tmp_path / "elsewhere"
        other.mkdir()
        (other / "db.py").write_text(
            'def f(conn, x):\n    conn.execute("SELECT * FROM t WHERE x = " + x)\n'
        )

        result = runner.invoke(app, ["reliability", "security", str(other)])

        assert result.exit_code == 1


class TestArchitectureCommand:
    def test_a_clean_project_exits_zero(self, project):
        (project / "a.py").write_text("x = 1\n")

        result = runner.invoke(app, ["reliability", "architecture"])

        assert result.exit_code == 0
        assert "No violations found" in result.output

    def test_this_projects_own_source_has_no_violations(self):
        """The acid test, run through the CLI rather than the library directly."""
        import pathlib

        repo_root = pathlib.Path(__file__).parents[2]

        result = runner.invoke(app, ["reliability", "architecture", str(repo_root)])

        assert result.exit_code == 0, result.output
