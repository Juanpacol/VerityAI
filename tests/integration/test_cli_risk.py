"""End-to-end tests for `verity reliability risk`.

The command exists to make `reliability/risk.py` reachable at all -- it had no
production caller before this. Two of these tests are really about ADR-0028:
that an absolute path is reported as a non-result rather than a clean `low`,
and that an empty graph is refused rather than answered.
"""

import pytest
from typer.testing import CliRunner

from verityai.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "tests").mkdir()

    (tmp_path / "src" / "hub.py").write_text("def shared(x):\n    return x + 1\n")
    (tmp_path / "src" / "callers.py").write_text(
        "from hub import shared\n\n\n"
        "def a(x):\n    return shared(x)\n\n\n"
        "def b(x):\n    return shared(x)\n\n\n"
        "def c(x):\n    return shared(x)\n"
    )
    (tmp_path / "src" / "plain.py").write_text("def tidy(x):\n    return x.strip()\n")
    (tmp_path / "tests" / "test_plain.py").write_text(
        "from plain import tidy\n\n\ndef test_t():\n    assert tidy(' a ') == 'a'\n"
    )
    (tmp_path / "src" / "auth" / "tokens.py").write_text("def mint():\n    return 'tok'\n")

    runner.invoke(app, ["init"])
    runner.invoke(app, ["graph", "build"])
    return tmp_path


class TestTiering:
    def test_every_tier_is_printed_with_its_reasons(self, project):
        result = runner.invoke(
            app, ["reliability", "risk", "src/auth/tokens.py", "src/hub.py", "src/plain.py"]
        )

        assert result.exit_code == 0
        assert "[HIGH" in result.output
        assert "[MEDIUM" in result.output
        assert "[LOW" in result.output
        assert "a high-risk convention" in result.output
        assert "blast radius" in result.output

    def test_a_high_tier_is_not_a_finding_and_exits_zero(self, project):
        """A tier is a verification depth. Exiting non-zero would make it a
        CI gate on a judgement the command does not make."""
        result = runner.invoke(app, ["reliability", "risk", "src/auth/tokens.py"])

        assert result.exit_code == 0

    def test_a_tier_never_appears_without_a_reason(self, project):
        result = runner.invoke(app, ["reliability", "risk", "src/plain.py"])

        assert "no elevating signal found" in result.output

    def test_show_rules_prints_low_tier_coverage(self, project):
        """The most useful line this command emits: how much of the rule set
        a low-tier file actually earns, which is why tiers are reported
        rather than used to gate scans (ADR-0026)."""
        result = runner.invoke(app, ["reliability", "risk", "src/plain.py", "--show-rules"])

        assert "RULES ADMITTED BY TIER" in result.output
        assert "1/3" in result.output
        assert "shell-command-injection" in result.output
        assert "is currently checked by only 1/3 rule(s)" in result.output


class TestPathFormAtTheCliLayer:
    """ADR-0028 at the CLI boundary.

    The library refuses to guess a repository root, because guessing one
    reproduces the original silent failure from a different direction. The CLI
    *knows* the root -- it is the parent of `.verity/` -- so it supplies it,
    and every path form a user can plausibly type resolves to the same tier.

    These pin that the CLI actually passes it. Without that argument all three
    forms below return `low` with no signals, which reads as a clean bill of
    health rather than as nothing having been measured.
    """

    def test_an_absolute_path_finds_the_same_signals_as_a_relative_one(self, project):
        absolute = runner.invoke(app, ["reliability", "risk", str(project / "src" / "hub.py")])
        relative = runner.invoke(app, ["reliability", "risk", "src/hub.py"])

        assert "blast radius" in relative.output
        assert "blast radius" in absolute.output, (
            "the CLI must pass repo_root, or an absolute path silently tiers low"
        )

    def test_a_dot_slash_path_finds_the_same_signals(self, project):
        result = runner.invoke(app, ["reliability", "risk", "./src/hub.py"])

        assert "blast radius" in result.output

    def test_a_path_outside_the_repository_says_so(self, project):
        result = runner.invoke(app, ["reliability", "risk", "/etc/hosts"])

        assert result.exit_code == 0
        assert "outside repo_root" in result.output


class TestRefusals:
    def test_no_paths_and_no_changed_flag_exits_two(self, project):
        result = runner.invoke(app, ["reliability", "risk"])

        assert result.exit_code == 2
        assert "Nothing to tier" in result.output

    def test_an_empty_graph_is_refused_rather_than_tiering_everything_low(
        self, tmp_path, monkeypatch
    ):
        """Without this guard the command answers `low` for every file, which
        reads as "nothing needs deep verification" when the truth is "nothing
        was measured"."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("def f():\n    return 1\n")
        runner.invoke(app, ["init"])  # no `graph build`

        result = runner.invoke(app, ["reliability", "risk", "a.py"])

        assert result.exit_code == 1
        assert "graph is empty" in result.output
        assert "verity graph build" in result.output
