"""Tests for parsing real Claude Code session transcripts.

`tests/fixtures/claude_code_sample.jsonl` is structurally faithful to real
session files (confirmed by inspecting actual transcripts before writing the
parser) but contains no real conversation content — it exercises the parser's
behavior, not a benchmark claim. The real corpus used to measure anything is
never committed to this repository; see docs/BENCHMARK_PROTOCOL.md.
"""

from pathlib import Path

from verityai.context.ingest import load
from verityai.context.ingest_claude_code import (
    from_claude_code_session,
    is_claude_code_jsonl,
    parse_jsonl,
)
from verityai.core.models import ItemKind

FIXTURE = Path(__file__).parents[1] / "fixtures" / "claude_code_sample.jsonl"


class TestSniffing:
    def test_a_real_shaped_session_is_recognised(self):
        assert is_claude_code_jsonl(FIXTURE.read_text())

    def test_a_json_array_is_not_recognised(self):
        assert not is_claude_code_jsonl('[{"role": "user", "content": "hi"}]')

    def test_plain_text_is_not_recognised(self):
        assert not is_claude_code_jsonl("just some prose\n\nmore prose")

    def test_empty_text_is_not_recognised(self):
        assert not is_claude_code_jsonl("")


class TestParsing:
    def test_user_and_assistant_lines_become_items(self):
        items, _ = parse_jsonl(FIXTURE.read_text())

        assert any(i.kind is ItemKind.USER_MESSAGE for i in items)
        assert any(i.kind is ItemKind.AGENT_MESSAGE for i in items)

    def test_a_plain_string_content_is_captured(self):
        items, _ = parse_jsonl(FIXTURE.read_text())

        assert any("health check endpoint" in i.content for i in items)

    def test_a_text_block_is_captured(self):
        items, _ = parse_jsonl(FIXTURE.read_text())

        assert any("routes file" in i.content for i in items)

    def test_a_tool_use_block_is_captured_with_its_input(self):
        items, _ = parse_jsonl(FIXTURE.read_text())

        tool_call = next(i for i in items if "tool_call Read" in i.content)
        assert "routes.py" in tool_call.content

    def test_a_nested_tool_result_text_block_is_flattened(self):
        """tool_result.content is itself a list of blocks -- one level of
        nesting the generic ingest.py flattener was never written to see."""
        items, _ = parse_jsonl(FIXTURE.read_text())

        assert any("def index()" in i.content for i in items)

    def test_a_tool_result_with_plain_string_content_is_captured(self):
        items, _ = parse_jsonl(FIXTURE.read_text())

        assert any("plain string tool result" in i.content for i in items)

    def test_original_index_is_sequential_over_kept_items_only(self):
        items, _ = parse_jsonl(FIXTURE.read_text())

        assert [i.original_index for i in items] == list(range(len(items)))

    def test_items_are_marked_with_their_source(self):
        items, _ = parse_jsonl(FIXTURE.read_text())

        assert all(i.metadata["source"] == "claude_code" for i in items)


class TestDeclaredScope:
    """Session bookkeeping is skipped and counted, never silently dropped."""

    def test_bookkeeping_types_are_counted_not_lost(self):
        _, skipped = parse_jsonl(FIXTURE.read_text())

        for expected in ("mode", "permission-mode", "file-history-snapshot", "system"):
            assert skipped.get(expected, 0) >= 1, expected

    def test_an_unrecognised_future_event_type_is_still_counted(self):
        """The parser has no hardcoded list of 'the' bookkeeping types --
        anything outside user/assistant is counted generically by its own
        type name, so a new event type Claude Code adds later doesn't need
        this module to be updated to stay honest about what it skipped."""
        _, skipped = parse_jsonl(FIXTURE.read_text())

        assert skipped.get("unknown-future-event", 0) == 1

    def test_unparseable_lines_are_counted(self):
        _, skipped = parse_jsonl(FIXTURE.read_text())

        assert skipped.get("unparseable", 0) == 1

    def test_a_content_line_with_no_message_is_counted_not_crashed(self):
        _, skipped = parse_jsonl(FIXTURE.read_text())

        assert skipped.get("user_without_message", 0) == 1

    def test_whitespace_only_content_produces_no_item(self):
        items, _ = parse_jsonl(FIXTURE.read_text())

        assert not any(i.content.strip() == "" for i in items)

    def test_nothing_is_silently_lost(self):
        """Every non-empty line is accounted for as either an item or a
        named skip category -- the same invariant ingest.py's docstring
        states for its own two formats."""
        raw = FIXTURE.read_text()
        non_empty_lines = sum(1 for line in raw.splitlines() if line.strip())

        items, skipped = parse_jsonl(raw)
        # One line produces no item despite being a parseable content line
        # (whitespace-only content) -- accounted for directly rather than
        # needing a dedicated bucket, the same way ingest.py's from_messages
        # silently drops a whitespace-only message.
        accounted = len(items) + sum(skipped.values())

        assert accounted + 1 == non_empty_lines


class TestFileReading:
    def test_from_claude_code_session_reads_a_real_path(self):
        items, skipped = from_claude_code_session(FIXTURE)

        assert items
        assert skipped


class TestLoadIntegration:
    """The generic ingest.py entry point must route this format correctly."""

    def test_load_auto_detects_a_claude_code_session(self):
        items = load(FIXTURE.read_text())

        assert any("health check endpoint" in i.content for i in items)
        assert all(i.metadata.get("source") == "claude_code" for i in items)

    def test_load_still_handles_a_plain_json_array(self):
        """The disambiguation between a JSON array and JSONL must not
        regress the format ingest.py already supported."""
        items = load('[{"role": "user", "content": "hello there"}]')

        assert items[0].kind is ItemKind.USER_MESSAGE
        assert "source" not in items[0].metadata

    def test_load_still_falls_back_to_plain_text(self):
        items = load("just a plain paragraph of text\n\nand another one")

        assert items
        assert all(i.metadata.get("kind_inferred") for i in items)
