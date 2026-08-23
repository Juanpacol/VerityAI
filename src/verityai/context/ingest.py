"""Turning a raw transcript into addressable `ContextItem`s.

Everything downstream operates on items, so this is where an opaque blob of
text becomes something that can be counted, classified and dropped
individually. Three input shapes are supported:

- **JSON** — a list of `{"role": ..., "content": ...}` messages, the shape
  every agent framework already speaks. Preferred, because role information is
  explicit rather than guessed.
- **Claude Code session JSONL** — one JSON event per line, as Claude Code
  writes real session transcripts to disk. See `ingest_claude_code.py` for
  the schema and why it needs its own parser rather than stretching this
  module's generic one.
- **Plain text** — split on blank lines and on common role markers. A
  best-effort fallback for pasted terminal scrollback, and it says so: items
  it could not confidently attribute are marked `ItemKind.AGENT_MESSAGE` with
  `metadata["kind_inferred"] = True`, so a later report can distinguish
  measured structure from guessed structure.

The parser never drops input. An unparseable region becomes one item rather
than disappearing — the token accounting downstream is only trustworthy if
the sum of the parts equals the whole it started from.
"""

import json
import re
from typing import Any

from verityai.context.ingest_claude_code import is_claude_code_jsonl, parse_jsonl
from verityai.core.models import ContextItem, ItemKind

# Role markers common to agent transcripts and terminal scrollback. Matched at
# the start of a line only, so the word "user" inside a sentence is not a
# false boundary.
_ROLE_MARKER = re.compile(
    r"^\s*(?:\[|<|##\s*)?(user|human|assistant|agent|system|tool|function|output)"
    r"\s*(?:\]|>|:|\n)",
    re.IGNORECASE | re.MULTILINE,
)

_ROLE_TO_KIND = {
    "user": ItemKind.USER_MESSAGE,
    "human": ItemKind.USER_MESSAGE,
    "assistant": ItemKind.AGENT_MESSAGE,
    "agent": ItemKind.AGENT_MESSAGE,
    "system": ItemKind.SYSTEM,
    "tool": ItemKind.TOOL_OUTPUT,
    "function": ItemKind.TOOL_OUTPUT,
    "output": ItemKind.TOOL_OUTPUT,
}


def kind_for_role(role: str | None) -> ItemKind:
    """Map a transcript role to an item kind, defaulting to agent message."""
    if not role:
        return ItemKind.AGENT_MESSAGE
    return _ROLE_TO_KIND.get(role.strip().lower(), ItemKind.AGENT_MESSAGE)


def from_messages(messages: list[dict[str, Any]]) -> list[ContextItem]:
    """Build items from structured messages.

    `content` that is a list (the multi-part content blocks used by several
    APIs) is flattened by concatenating the text parts — a tool_use block's
    arguments are context too, and skipping them would under-count.
    """
    items: list[ContextItem] = []
    for index, message in enumerate(messages):
        content = message.get("content", "")
        if isinstance(content, list):
            content = "\n".join(_flatten_part(part) for part in content)
        elif not isinstance(content, str):
            content = str(content)

        if not content.strip():
            continue

        items.append(
            ContextItem(
                kind=kind_for_role(message.get("role")),
                content=content,
                original_index=index,
                metadata={"role": message.get("role", "unknown")},
            )
        )
    return items


def _flatten_part(part: Any) -> str:
    """Extract text from one content block of any common shape."""
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        for key in ("text", "content", "output"):
            value = part.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(part, ensure_ascii=False)
    return str(part)


def from_text(text: str) -> list[ContextItem]:
    """Build items from unstructured text, splitting on role markers.

    Falls back to blank-line splitting when no markers are found. Items
    produced this way carry `kind_inferred` in their metadata so the report
    layer can be honest about how much of the structure was guessed.
    """
    if not text.strip():
        return []

    matches = list(_ROLE_MARKER.finditer(text))

    if not matches:
        blocks = [block for block in re.split(r"\n\s*\n", text) if block.strip()]
        return [
            ContextItem(
                kind=ItemKind.AGENT_MESSAGE,
                content=block.strip(),
                original_index=index,
                metadata={"kind_inferred": True, "split": "blank_line"},
            )
            for index, block in enumerate(blocks)
        ]

    items: list[ContextItem] = []

    # Text before the first marker is real content and must not be lost.
    preamble = text[: matches[0].start()].strip()
    if preamble:
        items.append(
            ContextItem(
                kind=ItemKind.SYSTEM,
                content=preamble,
                original_index=0,
                metadata={"kind_inferred": True, "split": "preamble"},
            )
        )

    for position, match in enumerate(matches):
        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if not content:
            continue

        items.append(
            ContextItem(
                kind=kind_for_role(match.group(1)),
                content=content,
                original_index=len(items),
                metadata={"kind_inferred": True, "split": "role_marker", "role": match.group(1)},
            )
        )

    return items


def load_report(raw: str) -> tuple[list[ContextItem], dict[str, int]]:
    """Parse `raw` as JSON messages if possible, otherwise as text, plus a
    count-by-reason of any session bookkeeping lines that were skipped.

    Tries the structured path first and falls back silently, because a caller
    piping in a transcript should not have to declare its format. Whether the
    structured path was taken is recoverable from `metadata["kind_inferred"]`.

    A single JSON array/object and a Claude Code session both start with `[`
    or `{`, so the two are disambiguated by trying to parse the *whole* input
    as one JSON value first — a real session file is many JSON objects
    (one per line), so that parse fails, and `is_claude_code_jsonl` is the
    tiebreaker before falling further back to the plain-text path.

    The skip channel is a count-by-reason dict here, not a `ParseReport` —
    `ingest_claude_code.parse_jsonl`'s own return shape, kept as-is. A
    20,000-line session file where hundreds of lines share one reason needs
    a summary, not a report naming every line; `memory/store.py`'s
    `ParseReport` is the right instrument for the opposite population
    (a handful of hand-edited records where the line number matters).
    """
    stripped = raw.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            if is_claude_code_jsonl(raw):
                return parse_jsonl(raw)
            return from_text(raw), {}

        if isinstance(parsed, list):
            return from_messages(parsed), {}
        if isinstance(parsed, dict) and isinstance(parsed.get("messages"), list):
            return from_messages(parsed["messages"]), {}

    return from_text(raw), {}


def load(raw: str) -> list[ContextItem]:
    """`load_report(raw)` with the skip counts dropped. See `load_report()`
    for that channel."""
    return load_report(raw)[0]
