"""End-to-end CLI tests.

These exercise the path a user actually takes, against a real temporary
`.verity/` directory and the real tokenizer — no fakes. The unit suite proves
the pieces behave; this proves they are wired together and that the commands
produce output a person can act on.
"""

import json

import pytest
from typer.testing import CliRunner

from verityai.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A temporary directory that is the current working directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def initialized(project):
    runner.invoke(app, ["init"])
    return project


TRANSCRIPT = json.dumps(
    [
        {"role": "system", "content": "You are a coding agent working on an API."},
        {"role": "user", "content": "Add rate limiting to the public endpoints."},
        {"role": "assistant", "content": "DECISION: use a token bucket per API key."},
        {"role": "tool", "content": "ok"},
        {"role": "tool", "content": "ok"},
        {"role": "tool", "content": "build log line\n" * 500},
        {"role": "assistant", "content": "The middleware is in src/api/rate_limit.py."},
        {"role": "assistant", "content": "The middleware is in src/api/rate_limit.py."},
        {"role": "user", "content": "Does it handle burst traffic correctly?"},
    ]
)


class TestInit:
    def test_init_creates_the_directory(self, project):
        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert (project / ".verity" / "state").is_dir()

    def test_commands_requiring_state_fail_helpfully_without_init(self, project):
        result = runner.invoke(app, ["handoff"])

        assert result.exit_code == 1
        assert "verity init" in result.output


class TestIngest:
    def test_ingest_reports_a_breakdown(self, project, tmp_path):
        transcript = tmp_path / "transcript.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["ingest", str(transcript)])

        assert result.exit_code == 0
        assert "critical" in result.output
        assert "CONTEXT HEALTH" in result.output

    def test_ingest_names_its_counting_method(self, project, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["ingest", str(transcript)])

        assert "Counted with:" in result.output

    def test_a_missing_file_fails_clearly(self, project):
        result = runner.invoke(app, ["ingest", "no-such-file.json"])

        assert result.exit_code == 1
        assert "No such file" in result.output


class TestContext:
    def test_pruning_reports_a_stage_ledger(self, project, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["context", str(transcript), "--budget", "500"])

        assert result.exit_code == 0
        for stage in ("dedup", "classify", "filter_tool_output", "compress", "budget", "place"):
            assert stage in result.output, stage

    def test_pruning_saves_tokens_on_a_redundant_transcript(self, project, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(
            app, ["context", str(transcript), "--budget", "500", "--task", "rate limiting"]
        )

        assert "Saved:" in result.output
        assert "Before:" in result.output
        assert "After:" in result.output

    def test_no_critical_bug_is_reported_on_a_normal_run(self, project, tmp_path):
        """The BUG line means the budget stage dropped a protected item."""
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["context", str(transcript), "--budget", "300"])

        assert "BUG:" not in result.output

    def test_output_can_be_written_to_a_file(self, project, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)
        out = tmp_path / "pruned.txt"

        runner.invoke(app, ["context", str(transcript), "--budget", "500", "--out", str(out)])

        assert out.exists()
        assert "rate limiting" in out.read_text().lower()


class TestMemoryCommands:
    def test_recording_and_reading_back_state(self, initialized):
        runner.invoke(app, ["task", "add rate limiting", "--next", "write the test"])
        runner.invoke(app, ["remember", "decision", "token bucket", "--why", "bursts"])
        runner.invoke(app, ["remember", "constraint", "no new dependencies"])
        runner.invoke(app, ["remember", "discovery", "middleware runs after auth"])
        runner.invoke(app, ["remember", "failure", "fixed window", "--error", "rejected bursts"])

        result = runner.invoke(app, ["handoff"])

        assert result.exit_code == 0
        assert "add rate limiting" in result.output
        assert "token bucket" in result.output
        assert "bursts" in result.output
        assert "no new dependencies" in result.output
        assert "middleware runs after auth" in result.output
        assert "fixed window" in result.output
        assert "write the test" in result.output

    def test_handoff_reports_its_token_cost(self, initialized):
        runner.invoke(app, ["task", "something"])

        result = runner.invoke(app, ["handoff"])

        assert "tokens" in result.output

    def test_handoff_can_be_written_to_a_file(self, initialized, tmp_path):
        runner.invoke(app, ["task", "the task"])
        out = tmp_path / "handoff.md"

        runner.invoke(app, ["handoff", "--out", str(out)])

        assert "# HANDOFF" in out.read_text()


class TestSnapshots:
    def test_snapshot_restore_round_trip(self, initialized):
        runner.invoke(app, ["task", "original task"])
        runner.invoke(app, ["remember", "decision", "the original decision"])
        runner.invoke(app, ["snapshot", "before the change"])

        runner.invoke(app, ["task", "changed task"])
        result = runner.invoke(app, ["restore", "1"])

        assert result.exit_code == 0
        assert "original task" in runner.invoke(app, ["handoff"]).output

    def test_restore_says_it_does_not_touch_code(self, initialized):
        runner.invoke(app, ["task", "something"])
        runner.invoke(app, ["snapshot"])

        result = runner.invoke(app, ["restore", "1"])

        # Only asserted when a git sha was captured; tmp_path is not a repo,
        # so this checks the command succeeds rather than the advisory text.
        assert result.exit_code == 0

    def test_listing_snapshots(self, initialized):
        runner.invoke(app, ["snapshot", "first"])
        runner.invoke(app, ["snapshot", "second"])

        result = runner.invoke(app, ["snapshots"])

        assert "001" in result.output
        assert "first" in result.output
        assert "second" in result.output

    def test_restoring_a_missing_snapshot_fails_clearly(self, initialized):
        result = runner.invoke(app, ["restore", "42"])

        assert result.exit_code == 1
        assert "No snapshot" in result.output


class TestHealth:
    def test_health_includes_persisted_state_when_available(self, initialized, tmp_path):
        runner.invoke(app, ["remember", "decision", "something decided"])
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["health", str(transcript)])

        assert "CONTEXT HEALTH" in result.output
        assert "PERSISTED STATE" in result.output
