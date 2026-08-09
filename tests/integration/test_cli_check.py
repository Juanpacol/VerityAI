"""End-to-end tests for `verity check`.

Real temporary repo, real graph build, real memory store -- exercising the
same path an agent would trigger through MCP's `check_claims`.
"""

import pytest
from typer.testing import CliRunner

from verityai.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text(
        "def apply_ceiling(n, cap):\n    return min(n, cap)\n\n\n"
        "def rate_limit_request(key, n):\n    return apply_ceiling(n, 100)\n"
    )
    runner.invoke(app, ["init"])
    runner.invoke(app, ["graph", "build"])
    return tmp_path


class TestCheck:
    def test_a_hallucinated_symbol_is_a_contradiction_and_exits_nonzero(self, project, tmp_path):
        claim_file = tmp_path / "claim.txt"
        claim_file.write_text("Uses `TotallyInventedSymbolXYZ` internally.")

        result = runner.invoke(app, ["check", str(claim_file)])

        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "1 contradiction" in result.output

    def test_a_real_symbol_passes_and_exits_zero(self, project, tmp_path):
        claim_file = tmp_path / "claim.txt"
        claim_file.write_text("`apply_ceiling` clamps the count.")

        result = runner.invoke(app, ["check", str(claim_file)])

        assert result.exit_code == 0
        assert "0 contradiction" in result.output

    def test_a_real_relation_passes(self, project, tmp_path):
        claim_file = tmp_path / "claim.txt"
        claim_file.write_text("`rate_limit_request` calls `apply_ceiling`.")

        result = runner.invoke(app, ["check", str(claim_file)])

        assert result.exit_code == 0

    def test_a_false_relation_fails(self, project, tmp_path):
        claim_file = tmp_path / "claim.txt"
        claim_file.write_text("`apply_ceiling` calls `rate_limit_request`.")

        result = runner.invoke(app, ["check", str(claim_file)])

        assert result.exit_code == 1

    def test_works_without_a_graph_having_been_built(self, tmp_path, monkeypatch):
        """Decision resurfacing must still run against .verity/ memory."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        claim_file = tmp_path / "claim.txt"
        claim_file.write_text("Just a plain sentence with no code refs.")

        result = runner.invoke(app, ["check", str(claim_file)])

        assert result.exit_code == 0
        assert "No checkable claims" in result.output

    def test_works_entirely_outside_a_verity_project(self, tmp_path, monkeypatch):
        """No .verity/ at all -- file claims still check against the filesystem."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "real.py").write_text("x = 1\n")
        claim_file = tmp_path / "claim.txt"
        claim_file.write_text("See `real.py` and `missing.py`.")

        result = runner.invoke(app, ["check", str(claim_file)])

        assert "real.py" in result.output
        assert "missing.py" in result.output
        assert result.exit_code == 1

    def test_reads_from_stdin(self, project):
        result = runner.invoke(app, ["check", "-"], input="`apply_ceiling` exists.")

        assert result.exit_code == 0
