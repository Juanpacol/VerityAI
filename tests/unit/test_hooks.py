"""Tests for the Claude Code hook integration.

The property that matters most: `capture_precompact` persists something an
agent never explicitly called `remember` for -- that is the entire reason
this module exists, so the first test proves exactly that, not just that
the function runs without raising.
"""

import json
from pathlib import Path

from verityai.cli.hooks import (
    capture_precompact,
    install,
    install_statusline,
    render_statusline,
    resume_context,
    verdict,
)
from verityai.core.models import ContextHealth, Decision, Failure
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
        assert result["snapshot_path"] is not None
        assert Path(result["snapshot_path"]).exists()

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


class TestVerdict:
    def test_healthy_when_nothing_is_wrong(self):
        health = ContextHealth(
            window_usage=0.3,
            relevant_ratio=0.9,
            critical_retained=1.0,
            redundancy=0.05,
            tool_noise=0.0,
        )
        status, reasons = verdict(health, {"corrupt_lines": 0}, snapshot_age_days=1.0)
        assert status == "healthy"
        assert reasons == []

    def test_critical_on_corruption_regardless_of_health(self):
        health = ContextHealth(
            window_usage=0.1,
            relevant_ratio=1.0,
            critical_retained=1.0,
            redundancy=0.0,
            tool_noise=0.0,
        )
        status, reasons = verdict(health, {"corrupt_lines": 3}, snapshot_age_days=None)
        assert status == "critical"
        assert any("corrupt" in reason for reason, _action in reasons)
        assert any("verity health" in action for _reason, action in reasons)

    def test_critical_retained_is_never_a_trigger(self):
        """compute_health() hardcodes critical_retained=1.0 on every
        unpruned transcript (its own docstring says so) -- every call this
        module makes is on an unpruned transcript, so this dimension can
        never actually report a loss here. A version of verdict() that
        used it as a critical-tier trigger shipped briefly and was wrong
        for the same reason ADR-0041 flags contradiction_count; this pins
        the fix (ADR-0042/0043)."""
        health = ContextHealth(
            window_usage=0.1,
            relevant_ratio=1.0,
            critical_retained=0.8,  # a value that could never occur here in practice
            redundancy=0.0,
            tool_noise=0.0,
        )
        status, reasons = verdict(health, {"corrupt_lines": 0}, snapshot_age_days=None)
        assert status == "healthy"
        assert not any("lost" in reason.lower() for reason, _action in reasons)

    def test_degraded_on_high_window_usage(self):
        health = ContextHealth(
            window_usage=0.9,
            relevant_ratio=1.0,
            critical_retained=1.0,
            redundancy=0.0,
            tool_noise=0.0,
        )
        status, _ = verdict(health, {"corrupt_lines": 0}, snapshot_age_days=None)
        assert status == "degraded"

    def test_degraded_on_high_redundancy(self):
        health = ContextHealth(
            window_usage=0.1,
            relevant_ratio=0.5,
            critical_retained=1.0,
            redundancy=0.4,
            tool_noise=0.0,
        )
        status, _ = verdict(health, {"corrupt_lines": 0}, snapshot_age_days=None)
        assert status == "degraded"

    def test_degraded_on_stale_snapshot(self):
        status, reasons = verdict(None, {"corrupt_lines": 0}, snapshot_age_days=10.0)
        assert status == "degraded"
        assert any("snapshot" in reason for reason, _action in reasons)
        assert any("verity snapshot" in action for _reason, action in reasons)

    def test_healthy_with_no_health_and_no_snapshots(self):
        status, reasons = verdict(None, {"corrupt_lines": 0}, snapshot_age_days=None)
        assert status == "healthy"
        assert reasons == []

    def test_never_reports_contradictions(self):
        """ADR-0041: contradiction_count is never computed by anything in
        this codebase; verdict must not treat its always-zero default as a
        real signal."""
        health = ContextHealth(
            window_usage=0.1,
            relevant_ratio=1.0,
            critical_retained=1.0,
            redundancy=0.0,
            tool_noise=0.0,
            contradiction_count=0,
        )
        status, reasons = verdict(health, {"corrupt_lines": 0}, snapshot_age_days=None)
        assert status == "healthy"
        assert not any("contradiction" in reason.lower() for reason, _action in reasons)


class TestRenderStatusline:
    def test_returns_none_without_a_verity_store(self, tmp_path):
        assert render_statusline({"cwd": str(tmp_path)}, root=tmp_path) is None

    def test_is_a_single_line(self, tmp_path):
        MemoryStore.init(tmp_path)

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "\n" not in line

    def test_shows_status_word(self, tmp_path):
        MemoryStore.init(tmp_path)

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "healthy" in line

    def test_shows_decisions_and_failures_short_codes(self, tmp_path):
        store = MemoryStore.init(tmp_path)
        store.append(Decision(statement="a"))
        store.append(Failure(attempted="b"))

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "1D 1F" in line

    def test_shows_context_window_percentage(self, tmp_path):
        MemoryStore.init(tmp_path)
        payload = {"cwd": str(tmp_path), "context_window": {"used_percentage": 62.4}}

        line = render_statusline(payload, root=tmp_path)

        assert "ctx 62%" in line

    def test_shows_critical_share_as_a_percentage_not_a_raw_count(self, tmp_path):
        """A raw count ("433 crit") is meaningless without knowing the
        transcript size -- 433 of 500 items reads very differently from
        433 of 50,000. A percentage is legible on its own."""
        MemoryStore.init(tmp_path)
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(TRANSCRIPT_WITH_DECISION)
        payload = {"cwd": str(tmp_path), "transcript_path": str(transcript)}

        line = render_statusline(payload, root=tmp_path)

        assert "33% crit" in line

    def test_points_at_verity_status_when_not_healthy(self, tmp_path):
        store = MemoryStore.init(tmp_path)
        store.append(Decision(statement="a"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ bad\n")

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "verity status" in line

    def test_no_pointer_when_healthy(self, tmp_path):
        store = MemoryStore.init(tmp_path)
        store.append(Decision(statement="a"))

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "verity status" not in line

    def test_status_turns_critical_on_corruption(self, tmp_path):
        store = MemoryStore.init(tmp_path)
        store.append(Decision(statement="a"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ bad\n")

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "critical" in line
        assert "1⚠" in line

    def test_zero_alerts_when_clean(self, tmp_path):
        store = MemoryStore.init(tmp_path)
        store.append(Decision(statement="a"))

        line = render_statusline({"cwd": str(tmp_path)}, root=tmp_path)

        assert "0⚠" in line

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
