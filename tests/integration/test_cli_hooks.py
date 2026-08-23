"""End-to-end tests for `verity hooks *`, exercised the way Claude Code
actually calls them: a JSON payload piped on stdin, nothing on argv."""

import json

import pytest
from typer.testing import CliRunner

from verityai.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

TRANSCRIPT = "\n".join(
    [
        json.dumps({"type": "user", "message": {"role": "user", "content": "investigate the bug"}}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": "DECISION: use a 30s timeout, not 5s -- 5s caused false positives",
                },
            }
        ),
    ]
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def initialized(project):
    runner.invoke(app, ["init"])
    return project


class TestPrecompact:
    def test_captures_a_critical_item_from_a_real_transcript(self, initialized):
        transcript = initialized / "transcript.jsonl"
        transcript.write_text(TRANSCRIPT)
        payload = json.dumps({"transcript_path": str(transcript), "cwd": str(initialized)})

        result = runner.invoke(app, ["hooks", "precompact"], input=payload)

        assert result.exit_code == 0
        assert "captured 1" in result.output
        discoveries = (initialized / ".verity" / "state" / "discoveries.jsonl").read_text()
        assert "30s timeout" in discoveries

    def test_never_fails_without_a_verity_store(self, project):
        transcript = project / "transcript.jsonl"
        transcript.write_text(TRANSCRIPT)
        payload = json.dumps({"transcript_path": str(transcript), "cwd": str(project)})

        result = runner.invoke(app, ["hooks", "precompact"], input=payload)

        assert result.exit_code == 0

    def test_never_fails_on_garbage_stdin(self, initialized):
        result = runner.invoke(app, ["hooks", "precompact"], input="not valid json at all")

        assert result.exit_code == 0


class TestSessionStart:
    def test_prints_handoff_on_compact(self, initialized):
        runner.invoke(app, ["task", "the task"])
        payload = json.dumps({"source": "compact", "cwd": str(initialized)})

        result = runner.invoke(app, ["hooks", "session-start"], input=payload)

        assert result.exit_code == 0
        assert "just compacted" in result.output
        assert "the task" in result.output

    def test_prints_nothing_on_a_normal_startup(self, initialized):
        payload = json.dumps({"source": "startup", "cwd": str(initialized)})

        result = runner.invoke(app, ["hooks", "session-start"], input=payload)

        assert result.exit_code == 0
        assert result.output.strip() == ""


class TestInstall:
    def test_registers_hooks_in_settings_json(self, project):
        result = runner.invoke(app, ["hooks", "install", str(project)])

        assert result.exit_code == 0
        settings = json.loads((project / ".claude" / "settings.json").read_text())
        assert "PreCompact" in settings["hooks"]
        assert "SessionStart" in settings["hooks"]

    def test_statusline_flag_registers_it(self, project):
        result = runner.invoke(app, ["hooks", "install", str(project), "--statusline"])

        assert result.exit_code == 0
        settings = json.loads((project / ".claude" / "settings.json").read_text())
        assert settings["statusLine"]["command"] == "verity hooks statusline"

    def test_without_the_flag_no_statusline_is_set(self, project):
        result = runner.invoke(app, ["hooks", "install", str(project)])

        assert result.exit_code == 0
        settings = json.loads((project / ".claude" / "settings.json").read_text())
        assert "statusLine" not in settings


class TestStatusline:
    def test_shows_verity_state(self, initialized):
        runner.invoke(app, ["remember", "decision", "use postgres", "--why", "reliable"])
        payload = json.dumps({"cwd": str(initialized)})

        result = runner.invoke(app, ["hooks", "statusline"], input=payload)

        assert result.exit_code == 0
        assert "1D" in result.output

    def test_prints_nothing_without_a_verity_store(self, project):
        payload = json.dumps({"cwd": str(project)})

        result = runner.invoke(app, ["hooks", "statusline"], input=payload)

        assert result.exit_code == 0
        assert result.output.strip() == ""
