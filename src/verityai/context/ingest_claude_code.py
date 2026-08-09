"""Parsing real Claude Code session transcripts into `ContextItem`s.

Claude Code writes one JSON object per line to
`~/.claude/projects/<project>/<session-id>.jsonl` — an event log, not the
`{"role": ..., "content": ...}` message-array shape `ingest.py::from_messages`
already handles. Confirmed against real transcript files before writing this
(never guessed): each line has a `type` field. Only `"user"` and `"assistant"`
carry conversational content; the rest — `mode`, `permission-mode`,
`file-history-snapshot`, `file-history-delta`, `attachment`, `ai-title`,
`agent-name`, `last-prompt`, `system` — are session bookkeeping the UI itself
uses and are declared out of scope rather than silently skipped, the same
discipline `graph/ingest.py` applies to non-Python files.

Within a relevant line, `message.content` is either a plain string or a list
of blocks: `{"type": "text", "text": ...}`, `{"type": "tool_use", "name":
..., "input": {...}}`, or `{"type": "tool_result", "content": ...}` where
that nested `content` is *itself* a string or a list of blocks — one more
level of nesting than `ingest.py`'s generic flattener expects. This module
recurses through that nesting itself rather than stretching the generic
`_flatten_part` to cover a shape it wasn't written for.

This is the harness's first ingestion path built directly from real, private
data rather than a plausible-looking spec. Grounding it in an actual sample
(see `tests/fixtures/claude_code_sample.jsonl`, structurally faithful but
non-sensitive) is what let the earlier version of this kind of thing avoid
inventing block shapes that don't occur in practice.
"""

import json
from pathlib import Path
from typing import Any

from verityai.core.models import ContextItem, ItemKind

# Types that carry conversational content. Everything else in a real session
# file is UI/session bookkeeping -- confirmed by inspecting real transcripts,
# not assumed.
_CONTENT_TYPES = frozenset({"user", "assistant"})

_ROLE_TO_KIND = {
    "user": ItemKind.USER_MESSAGE,
    "assistant": ItemKind.AGENT_MESSAGE,
}


def is_claude_code_jsonl(raw: str) -> bool:
    """Sniff test: does `raw` look like a Claude Code session file?

    Cheap and deliberately narrow -- checks only that enough of the first few
    non-empty lines are JSON objects carrying a `type` field from the known
    vocabulary. False negatives (falling through to the generic parser or to
    `from_text`) lose formatting, not content; false positives would be worse,
    since they'd route real prose through a parser built for a different
    shape.
    """
    checked = 0
    hits = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if checked >= 5:
            break
        checked += 1
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "type" in obj:
            hits += 1
    return checked > 0 and hits == checked


def _flatten_block(block: Any) -> str:
    """Render one content block as text, recursing into nested content.

    Falls back to `json.dumps` for anything unrecognized -- the same
    never-drop-input rule `ingest.py::_flatten_part` follows, applied to a
    block shape that module was never written to see (a `tool_result`'s own
    `content` field, which can itself be a string or a list of blocks).
    """
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return str(block)

    block_type = block.get("type")

    if block_type == "text":
        return str(block.get("text", ""))

    if block_type == "tool_use":
        name = block.get("name", "unknown_tool")
        return f"[tool_call {name}] {json.dumps(block.get('input', {}), ensure_ascii=False)}"

    if block_type == "tool_result":
        return _flatten_content(block.get("content", ""))

    return json.dumps(block, ensure_ascii=False)


def _flatten_content(content: Any) -> str:
    """Render a `message.content` value (string or list of blocks) as text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_flatten_block(block) for block in content)
    return str(content) if content else ""


def parse_jsonl(raw: str) -> tuple[list[ContextItem], dict[str, int]]:
    """Parse Claude Code session JSONL text into items, plus what was skipped.

    Returns `(items, skipped_line_counts)`. `skipped_line_counts` maps each
    non-content `type` (or `"unparseable"`) to how many lines of it were seen
    — the same "declare what you didn't read" discipline as
    `IngestReport.skipped` in `graph/ingest.py`, so a caller can tell "this
    session was mostly tool bookkeeping" from "this parser is broken."
    """
    items: list[ContextItem] = []
    skipped: dict[str, int] = {}
    index = 0

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            skipped["unparseable"] = skipped.get("unparseable", 0) + 1
            continue

        if not isinstance(event, dict):
            skipped["unparseable"] = skipped.get("unparseable", 0) + 1
            continue

        event_type = event.get("type", "unknown")
        if event_type not in _CONTENT_TYPES:
            skipped[event_type] = skipped.get(event_type, 0) + 1
            continue

        message = event.get("message")
        if not isinstance(message, dict):
            skipped[f"{event_type}_without_message"] = (
                skipped.get(f"{event_type}_without_message", 0) + 1
            )
            continue

        content = _flatten_content(message.get("content", ""))
        if not content.strip():
            continue

        items.append(
            ContextItem(
                kind=_ROLE_TO_KIND.get(event_type, ItemKind.AGENT_MESSAGE),
                content=content,
                original_index=index,
                metadata={"role": message.get("role", event_type), "source": "claude_code"},
            )
        )
        index += 1

    return items, skipped


def from_claude_code_session(path: Path) -> tuple[list[ContextItem], dict[str, int]]:
    """Read and parse a Claude Code session file from disk."""
    return parse_jsonl(Path(path).read_text(encoding="utf-8"))
