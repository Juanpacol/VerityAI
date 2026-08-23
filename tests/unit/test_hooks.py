"""Tests for the Claude Code hook integration.

The property that matters most: `capture_precompact` persists something an
agent never explicitly called `remember` for -- that is the entire reason
this module exists, so the first test proves exactly that, not just that
the function runs without raising.
"""

import json

from verityai.cli.hooks import (
    capture_precompact,
    install,
    install_statusline,
    render_statusline,
    resume_context,
)
from verityai.core.models import Decision, Discovery
from verityai.memory.snapshot import SnapshotManager
from verityai.memory.store import MemoryStore

TRANSCRIPT_WITH_DECISION = "\n".join(
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
        json.dumps(
            {"type": "assistant", "message": {"role": "assistant", "content": "some filler text"}}
        ),
    ]
)


class TestCapturePrecompact:
    def test_persists_a_critical_item_the_agent_never_explicitly_remembered(self, tmp_path):
        MemoryStore.init(tmp_path)
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(TRANSCRIPT_WITH_DECISION)

        result = capture_precompact({"transcript_path": str(transcript)}, root=tmp_path)

        assert result["captured"] >= 1
        store = MemoryStore.discover(tmp_path)
        statements = {d.statement for d in store.discoveries()}
        assert any("30s timeout" in s for s in statements)

    def test_captured_discoveries_are_tagged_auto_captured(self, tmp_path):
        MemoryStore.init(tmp_path)
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(TRANSCRIPT_WITH_DECISION)

        capture_precompact({"transcript_path": str(transcript)}, root=tmp_path)

        store = MemoryStore.discover(tmp_path)
        captured = [d for d in store.discoveries() if "30s timeout" in d.statement]
        assert captured
        assert captured[0].source == "hook:precompact"
        assert "auto-captured" in captured[0].tags

    def test_creates_a_labeled_snapshot(self, tmp_path):
        MemoryStore.init(tmp_path)
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(TRANSCRIPT_WITH_DECISION)

        result = capture_precompact({"transcript_path": str(transcript)}, root=tmp_path)

        assert result["snapshot_number"] == 1

    def test_is_idempotent_on_repeated_calls(self, tmp_path):
        """The same transcript re-read (e.g. a second PreCompact in one
        session) must not duplicate the same discovery."""
        MemoryStore.init(tmp_path)
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(TRANSCRIPT_WITH_DECISION)

        capture_precompact({"transcript_path": str(transcript)}, root=tmp_path)
        capture_precompact({"transcript_path": str(transcript)}, root=tmp_path)

        store = MemoryStore.discover(tmp_path)
        matching = [d for d in store.discoveries() if "30s timeout" in d.statement]
        assert len(matching) == 1

    def test_no_verity_store_is_skipped_not_fatal(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(TRANSCRIPT_WITH_DECISION)

        result = capture_precompact({"transcript_path": str(transcript)}, root=tmp_path)

        assert result["skipped_reason"] is not None
        assert result["captured"] == 0

    def test_missing_transcript_path_is_skipped_not_fatal(self, tmp_path):
        MemoryStore.init(tmp_path)

        result = capture_precompact({}, root=tmp_path)

        assert result["skipped_reason"] is not None

    def test_nonexistent_transcript_file_is_skipped_not_fatal(self, tmp_path):
        MemoryStore.init(tmp_path)

        result = capture_precompact(
            {"transcript_path": str(tmp_path / "does_not_exist.jsonl")}, root=tmp_path
        )

        assert result["skipped_reason"] is not None

    def test_no_critical_items_captures_nothing(self, tmp_path):
        MemoryStore.init(tmp_path)
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps(
                {"type": "assistant", "message": {"role": "assistant", "content": "just chatting"}}
            )
        )

        result = capture_precompact({"transcript_path": str(transcript)}, root=tmp_path)

        assert result["skipped_reason"] is None
        assert result["captured"] == 0


class TestResumeContext:
    def test_returns_none_when_source_is_not_compact(self, tmp_path):
        MemoryStore.init(tmp_path)

        assert resume_context({"source": "startup"}, root=tmp_path) is None
        assert resume_context({"source": "resume"}, root=tmp_path) is None
        assert resume_context({}, root=tmp_path) is None

    def test_returns_none_without_a_verity_store(self, tmp_path):
        assert resume_context({"source": "compact"}, root=tmp_path) is None

    def test_returns_the_handoff_on_compact(self, tmp_path):
        store = MemoryStore.init(tmp_path)
        store.append(Decision(statement="use postgres", rationale="reliable"))

        context = resume_context({"source": "compact"}, root=tmp_path)

        assert context is not None
        assert "just compacted" in context
        assert "use postgres" in context


class TestInstall:
    def test_writes_hooks_to_a_fresh_settings_file(self, tmp_path):
        path = install(tmp_path)

        settings = json.loads(path.read_text())
        assert "verity hooks precompact" in json.dumps(settings["hooks"]["PreCompact"])
        assert "verity hooks session-start" in json.dumps(settings["hooks"]["SessionStart"])

    def test_preserves_existing_unrelated_settings(self, tmp_path):
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(
            json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}})
        )

        install(tmp_path)

        settings = json.loads((settings_dir / "settings.json").read_text())
        assert settings["permissions"]["allow"] == ["Bash(ls:*)"]
        assert "PreCompact" in settings["hooks"]

    def test_is_idempotent_does_not_duplicate_entries(self, tmp_path):
        install(tmp_path)
        install(tmp_path)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert len(settings["hooks"]["PreCompact"]) == 1
        assert len(settings["hooks"]["SessionStart"]) == 1

    def test_preserves_existing_unrelated_hooks(self, tmp_path):
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": "echo hi"}],
                            }
                        ]
                    }
                }
            )
        )

        install(tmp_path)

        settings = json.loads((settings_dir / "settings.json").read_text())
        assert settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "echo hi"
        assert "PreCompact" in settings["hooks"]


class TestRenderStatusline:
    def test_returns_none_without_a_verity_store(self, tmp_path):
        assert render_statusline({"cwd": str(tmp_path)}, root=tmp_path) is None

    def test_shows_record_counts(self, tmp_path):
        store = MemoryStore.init(tmp_path)
        store.append(Decision(statement="a"))
        store.append(Discovery(statement="b"))

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "1 dec" in line
        assert "1 disc" in line

    def test_shows_no_snapshots_when_none_exist(self, tmp_path):
        MemoryStore.init(tmp_path)

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "no snapshots" in line

    def test_shows_latest_snapshot_number(self, tmp_path):
        store = MemoryStore.init(tmp_path)
        SnapshotManager(store).create()
        SnapshotManager(store).create()

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "snap 002" in line

    def test_shows_corruption_warning(self, tmp_path):
        store = MemoryStore.init(tmp_path)
        store.append(Decision(statement="a"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ bad\n")

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "corrupt" in line

    def test_no_corruption_warning_when_clean(self, tmp_path):
        store = MemoryStore.init(tmp_path)
        store.append(Decision(statement="a"))

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "corrupt" not in line

    def test_reads_cwd_from_workspace_current_dir(self, tmp_path):
        MemoryStore.init(tmp_path)

        line = render_statusline({"workspace": {"current_dir": str(tmp_path)}}, root=None)

        assert line is not None


class TestInstallStatusline:
    def test_sets_statusline_on_a_fresh_settings_file(self, tmp_path):
        path, installed = install_statusline(tmp_path)

        assert installed is True
        settings = json.loads(path.read_text())
        assert settings["statusLine"]["command"] == "verity hooks statusline"

    def test_does_not_overwrite_an_existing_statusline(self, tmp_path):
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(
            json.dumps({"statusLine": {"type": "command", "command": "my-own-script.sh"}})
        )

        path, installed = install_statusline(tmp_path)

        assert installed is False
        settings = json.loads(path.read_text())
        assert settings["statusLine"]["command"] == "my-own-script.sh"
