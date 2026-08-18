"""Sorting context into the five relevance buckets, deterministically.

Every rule here is a stated heuristic over structure — item kind, age,
duplication, explicit markers — and none of them calls a model. That is
partly the "deterministic first" principle, but mostly a measurement
constraint: the product claim is *we cut your context by N tokens*, and if
the classifier spent tokens to decide what to cut, N would be a fiction.

The classifier's judgements are meant to be argued with, so every item comes
back with a `relevance_reason` naming the rule that fired. A user who
disagrees with a pruning decision can see which rule to change. This is the
same discipline as the old retrieval layer's `degraded_reason` — never let
the system reach a conclusion it cannot explain.

Precedence, highest first: explicit protection markers, then obsolescence,
then duplication, then kind-and-age heuristics. Order matters — an obsolete
duplicate is obsolete, and a critical item stays critical even if duplicated.
"""

import hashlib
import re
from collections.abc import Iterable

from verityai.core.models import ContextItem, ItemKind, Relevance

# Markers a user or agent can write to pin something. Matched case-insensitively
# anywhere in the item. Explicit intent always beats inference — if someone
# took the trouble to say "this matters", no heuristic overrides it.
CRITICAL_MARKERS = (
    "verity:critical",
    "decision:",
    "constraint:",
    "must not",
    "do not",
    "never",
    "requirement:",
)

# A monetary amount, requiring a currency symbol or ISO code -- never a bare
# number. This is the deliberate line between "an exact figure worth
# protecting" and "any number," which is most of what a dev-log or a piece
# of code contains (line numbers, counts, versions). Matching bare digits
# would make nearly everything CRITICAL and defeat pruning entirely.
_CURRENCY_AMOUNT = re.compile(
    r"(?:[$€£¥]\s?\d[\d,]*\.?\d*)"
    r"|(?:\b(?:USD|EUR|GBP|MXN|COP|JPY)\s?\d[\d,]*\.?\d*)",
)

# An IBAN-shaped account number: two letters (country code), two check
# digits, then 10-30 alphanumerics (BBAN) -- real IBANs run 15-34 characters
# total, so the floor here stays close to that rather than accepting
# anything vaguely shaped like one.
#
# The lookaround (not just \b) is load-bearing, found necessary by running
# this against real data: a base64-ish blob such as
# "...bsIT9IZ+CH78K2XZ+KRGYuWie..." matches `\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b`
# on the `CH78K2XZ` fragment, because regex `\b` treats `+` and `/` as word
# boundaries even though they are plainly part of the same continuous blob,
# not a boundary a human would recognize. Excluding base64's own alphabet
# immediately before and after the match is what actually rules that out;
# `\b` alone does not.
_IBAN_SHAPED = re.compile(r"(?<![A-Za-z0-9+/=])[A-Z]{2}\d{2}[A-Z0-9]{10,30}(?![A-Za-z0-9+/=])")


def extract_financial_figures(text: str) -> set[str]:
    """Every monetary amount or account-number-shaped string in `text`.

    Shared by the classifier (`_decide`, below) and `context/health.py`'s
    `digit_retention` metric -- one pattern, two consumers, so protecting a
    figure and measuring whether it survived can never quietly drift apart
    from each other.

    Deliberately narrow. Excluded on purpose: bare percentages (`92.4%` is
    routine noise in this very codebase's own benchmark output), line
    numbers, counts ("100 tests passed"), and generic digit runs (hashes,
    IDs). A number is only "a financial figure" here if it carries a
    currency marker or an IBAN's shape -- both signal precision worth
    protecting; a bare integer does not.
    """
    figures: set[str] = set()
    figures.update(match.group(0) for match in _CURRENCY_AMOUNT.finditer(text))
    figures.update(match.group(0) for match in _IBAN_SHAPED.finditer(text))
    return figures


# Markers indicating an item has been explicitly retired.
OBSOLETE_MARKERS = (
    "verity:obsolete",
    "superseded by",
    "no longer",
    "reverted",
    "abandoned this approach",
)

# Tool output that is almost always pure noise: successful, uninformative,
# or machine chatter no one will read again. Kept narrow on purpose — the
# cost of dropping a real signal is far higher than of keeping some noise.
NOISE_PATTERNS = (
    re.compile(r"^\s*$"),
    re.compile(r"^(ok|done|success|\+ok)\s*$", re.I),
    re.compile(r"^\s*\d+ files? changed[,\s\d\w()+-]*$", re.I),
    re.compile(r"^npm (WARN|notice)\b", re.I),
    re.compile(r"^\s*(Installing|Downloading|Resolving|Fetching)\b", re.I),
    re.compile(r"^\s*\[?\d+%\]?\s*(\||#|=)*\s*$"),  # progress bars
)

# Below this many characters a tool output usually carries no information
# beyond "it ran" -- unless it signals failure. `exit code 1` is eleven
# characters and is one of the most informative things a tool can say, so
# length alone must never be enough to discard something.
_TRIVIAL_TOOL_OUTPUT_CHARS = 24

# Failure signals. Their presence makes an item informative at any length,
# and overrides every noise heuristic below.
SIGNAL_MARKERS = (
    "error",
    "exception",
    "traceback",
    "failed",
    "failure",
    "denied",
    "refused",
    "timeout",
    "timed out",
    "not found",
    "cannot",
    "unable",
    "exit code",
    "exit status",
    "fatal",
    "panic",
    "warning:",
)


def content_hash(text: str) -> str:
    """Stable hash of normalized content, for exact-duplicate detection.

    Normalizes whitespace so that reformatting alone does not defeat dedup —
    the same file printed twice with different indentation is still the same
    context.
    """
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _has_marker(text: str, markers: Iterable[str]) -> str | None:
    lowered = text.lower()
    for marker in markers:
        if marker in lowered:
            return marker
    return None


def _is_noise(text: str) -> bool:
    """True when tool output carries nothing worth spending tokens on.

    A failure signal disqualifies an item from being noise regardless of how
    short it is. Dropping "exit code 1" to save eleven tokens would remove the
    single most useful line in a transcript.
    """
    stripped = text.strip()
    if _has_marker(stripped, SIGNAL_MARKERS):
        return False
    if len(stripped) < _TRIVIAL_TOOL_OUTPUT_CHARS:
        return True
    return any(pattern.match(stripped) for pattern in NOISE_PATTERNS)


def classify_item(
    item: ContextItem,
    seen_hashes: dict[str, int] | None = None,
    total_items: int | None = None,
) -> ContextItem:
    """Assign a relevance bucket to one item, with the reason recorded.

    Args:
        item: The item to classify. Not mutated — a copy is returned.
        seen_hashes: Content hashes already encountered, mapped to the index
            that first carried them. Passing this in (rather than computing
            it here) is what makes duplicate detection order-aware: the first
            occurrence is kept, later ones become `REDUNDANT`.
        total_items: Size of the full context, used for the recency heuristic.

    Returns:
        A copy of `item` with `relevance`, `relevance_reason` and
        `content_hash` filled in.
    """
    text = item.content
    digest = item.content_hash or content_hash(text)

    relevance, reason = _decide(item, text, digest, seen_hashes, total_items)

    return item.model_copy(
        update={
            "relevance": relevance,
            "relevance_reason": reason,
            "content_hash": digest,
        }
    )


def _decide(
    item: ContextItem,
    text: str,
    digest: str,
    seen_hashes: dict[str, int] | None,
    total_items: int | None,
) -> tuple[Relevance, str]:
    """The rule cascade. Returns (bucket, human-readable reason)."""

    # 1. Explicit intent wins over everything.
    marker = _has_marker(text, CRITICAL_MARKERS)
    if marker:
        return Relevance.CRITICAL, f"explicit marker {marker!r}"

    # 1b. Financial figures -- same precedence as an explicit marker, on
    # purpose. A dollar amount or account number is exact-or-wrong with no
    # middle ground, and nothing about it is safe to infer from a summary if
    # lost. Checked before duplication (a repeated figure is still each
    # independently worth protecting, same reasoning as CRITICAL_MARKERS)
    # and before recency (a figure mentioned once, early, must not depend on
    # still being "recent" to survive).
    figures = extract_financial_figures(text)
    if figures:
        return Relevance.CRITICAL, "contains a financial figure (amount/account number)"

    # Memory records are what the harness itself decided to preserve; they
    # are already the distilled form and must survive any budget.
    if item.kind is ItemKind.MEMORY:
        return Relevance.CRITICAL, "persisted memory record"

    # The system prompt defines the agent's whole operating frame. Dropping it
    # to save tokens is a false economy.
    if item.kind is ItemKind.SYSTEM:
        return Relevance.CRITICAL, "system prompt"

    # 2. Explicitly retired content.
    marker = _has_marker(text, OBSOLETE_MARKERS)
    if marker:
        return Relevance.OBSOLETE, f"obsolescence marker {marker!r}"

    # 3. Duplication. Checked before the kind heuristics so a repeated file
    #    dump is caught no matter what kind it claims to be.
    if seen_hashes is not None and digest in seen_hashes:
        first = seen_hashes[digest]
        return Relevance.REDUNDANT, f"exact duplicate of item #{first}"

    # 4. Tool noise.
    if item.kind is ItemKind.TOOL_OUTPUT and _is_noise(text):
        return Relevance.IRRELEVANT, "tool output with no information content"

    # 5. Recency. The most recent exchanges are what the agent is actually
    #    working on; older ones are candidates for pruning but never dropped
    #    on age alone, only demoted.
    if total_items and total_items > 10:
        recent_cutoff = total_items - max(3, total_items // 10)
        if item.original_index >= recent_cutoff:
            return Relevance.CRITICAL, "most recent exchange"

    # A user message is otherwise protected only by an earlier, more precise
    # rule above (an explicit marker, a financial figure, or recency) -- not
    # by its kind alone. An unconditional "every user message is critical"
    # rule used to live here; removed because a long conversation shaped like
    # many short user pointers ("also check X") and a few substantive
    # assistant replies let the pointers consume the entire un-droppable
    # critical floor before ranking ever got a chance to protect the reply
    # that actually carried the answer. See ADR-0033.
    return Relevance.RELEVANT, "no demotion rule matched"


def classify_all(items: list[ContextItem]) -> list[ContextItem]:
    """Classify a whole context in order.

    Order-sensitive by design: duplicate detection keeps the *first*
    occurrence and demotes later ones, and the recency rule needs to know
    where each item sits in the whole. Classifying items independently would
    silently change both.
    """
    seen: dict[str, int] = {}
    total = len(items)
    classified: list[ContextItem] = []

    for index, item in enumerate(items):
        staged = item if item.original_index else item.model_copy(update={"original_index": index})
        result = classify_item(staged, seen_hashes=seen, total_items=total)
        # Register the hash only for items that were kept as originals, so a
        # run of three identical blocks demotes the second and third against
        # the first rather than chaining.
        seen.setdefault(result.content_hash, index)
        classified.append(result)

    return classified


def relevance_breakdown(items: list[ContextItem]) -> dict[str, int]:
    """Token totals per relevance bucket — the §11 report table.

    Returns tokens rather than item counts because tokens are the unit the
    user is billed in, and a single huge irrelevant file dump matters more
    than thirty small ones.
    """
    breakdown = {bucket.value: 0 for bucket in Relevance}
    for item in items:
        if item.relevance is not None:
            breakdown[item.relevance.value] += item.token_count
    return breakdown
