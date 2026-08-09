"""Tests for transcript parsing.

The invariant that matters: parsing never loses input. Every token-accounting
claim downstream assumes the parts sum to the whole they came from, so a
parser that silently swallows an unrecognized region would corrupt the
headline savings figure at the source.
"""

import json

from verityai.context.ingest import from_messages, from_text, kind_for_role, load
from verityai.core.models import ItemKind


class TestRoleMapping:
    def test_known_roles_map(self):
        assert kind_for_role("user") is ItemKind.USER_MESSAGE
        assert kind_for_role("human") is ItemKind.USER_MESSAGE
        assert kind_for_role("assistant") is ItemKind.AGENT_MESSAGE
        assert kind_for_role("system") is ItemKind.SYSTEM
        assert kind_for_role("tool") is ItemKind.TOOL_OUTPUT

    def test_mapping_is_case_insensitive(self):
        assert kind_for_role("USER") is ItemKind.USER_MESSAGE

    def test_unknown_roles_default_to_agent_message(self):
        assert kind_for_role("wizard") is ItemKind.AGENT_MESSAGE
        assert kind_for_role(None) is ItemKind.AGENT_MESSAGE


class TestStructuredMessages:
    def test_messages_become_items(self):
        items = from_messages(
            [
                {"role": "user", "content": "add caching"},
                {"role": "assistant", "content": "on it"},
            ]
        )

        assert len(items) == 2
        assert items[0].kind is ItemKind.USER_MESSAGE
        assert items[1].kind is ItemKind.AGENT_MESSAGE

    def test_multi_part_content_is_flattened(self):
        items = from_messages(
            [{"role": "assistant", "content": [{"text": "first"}, {"text": "second"}]}]
        )

        assert "first" in items[0].content
        assert "second" in items[0].content

    def test_tool_use_blocks_are_not_discarded(self):
        """A tool call's arguments are context and must be counted."""
        items = from_messages(
            [{"role": "assistant", "content": [{"type": "tool_use", "name": "grep"}]}]
        )

        assert "grep" in items[0].content

    def test_empty_messages_are_skipped(self):
        items = from_messages([{"role": "user", "content": "   "}])

        assert items == []

    def test_original_order_is_recorded(self):
        items = from_messages([{"role": "user", "content": f"message {n}"} for n in range(3)])

        assert [i.original_index for i in items] == [0, 1, 2]


class TestPlainText:
    def test_role_markers_split_the_transcript(self):
        items = from_text("user: do the thing\nassistant: doing it now")

        assert len(items) == 2
        assert items[0].kind is ItemKind.USER_MESSAGE
        assert items[1].kind is ItemKind.AGENT_MESSAGE

    def test_text_before_the_first_marker_is_kept(self):
        items = from_text("some preamble here\nuser: the actual request")

        assert any("preamble" in i.content for i in items)

    def test_unmarked_text_splits_on_blank_lines(self):
        items = from_text("first block\n\nsecond block\n\nthird block")

        assert len(items) == 3

    def test_inferred_structure_is_labelled_as_inferred(self):
        """Guessed structure must be distinguishable from parsed structure."""
        items = from_text("user: something")

        assert items[0].metadata["kind_inferred"] is True

    def test_empty_input_produces_nothing(self):
        assert from_text("") == []
        assert from_text("   \n  ") == []


class TestLoad:
    def test_a_json_array_is_parsed_as_messages(self):
        raw = json.dumps([{"role": "user", "content": "hello there"}])

        items = load(raw)

        assert items[0].kind is ItemKind.USER_MESSAGE
        assert "kind_inferred" not in items[0].metadata

    def test_a_messages_object_is_parsed(self):
        raw = json.dumps({"messages": [{"role": "system", "content": "be helpful"}]})

        assert load(raw)[0].kind is ItemKind.SYSTEM

    def test_malformed_json_falls_back_to_text(self):
        items = load('[{"role": "user", broken')

        assert items, "expected the text fallback to produce something"

    def test_plain_text_takes_the_text_path(self):
        items = load("user: just plain text")

        assert items[0].metadata["kind_inferred"] is True


class TestNoInputIsLost:
    def test_every_line_of_a_marked_transcript_appears_somewhere(self):
        raw = "preamble line\nuser: the request\ntool: the output\nassistant: the reply"

        combined = " ".join(i.content for i in load(raw))

        for fragment in ("preamble line", "the request", "the output", "the reply"):
            assert fragment in combined, fragment

    def test_unrecognized_regions_survive(self):
        raw = "===== some banner =====\n\nrandom unstructured text\n\nmore of it"

        combined = " ".join(i.content for i in load(raw))

        assert "some banner" in combined
        assert "random unstructured text" in combined
