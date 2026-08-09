"""Context health: how good is this context, not just how full.

"Context: 73%" answers the wrong question. It measures the container, not the
contents — a window that is 73% full of exactly the right material is healthy,
and one that is 30% full of stale duplicates is not. Every dimension below is
computed independently and reported independently.

The aggregate `ContextHealth.score` exists because people ask for one number,
but the rule in this codebase is that it never appears without its components.
The precedent is direct and expensive: the pre-pivot confidence score was a
single weighted number that looked authoritative, and T1 found it uncalibrated
(ECE 0.14–0.50) and in one configuration *inverted* — near-zero-confidence
verdicts were right 75% of the time. A composite that hides its parts cannot
be audited, and so its failures go unnoticed. `render_health` therefore prints
the breakdown first and the score last.
"""

from verityai.context.tokenizer import TokenCounter
from verityai.core.models import ContextHealth, ContextItem, ItemKind, Relevance


def compute_health(
    items: list[ContextItem],
    window: int | None = None,
    counter: TokenCounter | None = None,
    stale_count: int = 0,
    contradiction_count: int = 0,
) -> ContextHealth:
    """Measure the health of a classified context.

    Args:
        items: Context items. Must already be classified — an unclassified
            context has no relevance signal, and the ratios below would be
            silently zero rather than obviously wrong.
        window: Context window size. Defaults to the counter's model window.
        counter: Token counter, for reporting which method produced the counts.
        stale_count: Facts whose evidence no longer matches. Supplied by the
            Memory Engine, which owns evidence freshness.
        contradiction_count: Supplied by the Consistency Engine (Phase 3);
            zero until that engine exists, and reported as zero rather than
            hidden, so the dimension is visibly present but unpopulated.
    """
    counter = counter or TokenCounter()
    window = window or counter.window

    total_tokens = sum(item.token_count for item in items)

    notes: list[str] = []
    if not items:
        notes.append("empty context")
    unclassified = sum(1 for item in items if item.relevance is None)
    if unclassified:
        notes.append(
            f"{unclassified} of {len(items)} items unclassified; "
            "relevance ratios below exclude them"
        )
    if not counter.is_exact:
        notes.append(f"token counts are estimates ({counter.method}), not exact")

    by_bucket = {bucket: 0 for bucket in Relevance}
    for item in items:
        if item.relevance is not None:
            by_bucket[item.relevance] += item.token_count

    classified_tokens = sum(by_bucket.values())
    useful = by_bucket[Relevance.CRITICAL] + by_bucket[Relevance.RELEVANT]
    wasted = by_bucket[Relevance.REDUNDANT] + by_bucket[Relevance.OBSOLETE]

    tool_tokens = sum(
        item.token_count
        for item in items
        if item.kind is ItemKind.TOOL_OUTPUT and item.relevance is not Relevance.CRITICAL
    )

    return ContextHealth(
        window_usage=_ratio(total_tokens, window),
        relevant_ratio=_ratio(useful, classified_tokens),
        # Nothing has been pruned yet at measurement time, so every critical
        # item is by definition still present. This dimension only becomes
        # informative when comparing a pruned context against its original,
        # which is what `critical_retention` below is for.
        critical_retained=1.0,
        redundancy=_ratio(wasted, classified_tokens),
        tool_noise=_ratio(tool_tokens, total_tokens),
        stale_count=stale_count,
        contradiction_count=contradiction_count,
        total_tokens=total_tokens,
        token_method=counter.method,
        notes=notes,
    )


def critical_retention(before: list[ContextItem], after: list[ContextItem]) -> float:
    """Fraction of critical items that survived pruning. Must be 1.0.

    This is the pipeline's central safety property, expressed as a number so
    it can be asserted in a test rather than eyeballed. A value below 1.0
    means the budget stage dropped something it had promised to protect,
    which is a bug in `prune.py`, not a tuning decision.
    """
    critical_before = {item.id for item in before if item.is_protected}
    if not critical_before:
        return 1.0
    surviving = {item.id for item in after} & critical_before
    return round(len(surviving) / len(critical_before), 4)


def _ratio(numerator: int, denominator: int) -> float:
    """Clamped ratio; 0.0 when the denominator is zero.

    Returning 0.0 for an empty context is a deliberate choice over raising:
    health is a report, and a report that crashes on an empty input is
    useless at exactly the moment someone is debugging why their context is
    empty. The `notes` field carries the caveat instead.
    """
    if denominator <= 0:
        return 0.0
    return round(max(0.0, min(1.0, numerator / denominator)), 4)


def render_health(health: ContextHealth) -> str:
    """Format health for a terminal, components first and score last.

    The ordering is the point. See the module docstring.
    """
    lines = [
        "VERITY CONTEXT HEALTH",
        "",
        f"  Window usage        {health.window_usage:>7.1%}",
        f"  Relevant context    {health.relevant_ratio:>7.1%}",
        f"  Critical retained   {health.critical_retained:>7.1%}",
        f"  Redundancy          {health.redundancy:>7.1%}",
        f"  Tool noise          {health.tool_noise:>7.1%}",
        f"  Stale facts         {health.stale_count:>7}",
        f"  Contradictions      {health.contradiction_count:>7}",
        "",
        f"  Total tokens        {health.total_tokens:>7,}  [{health.token_method}]",
        "",
        f"  Health              {health.score:>7.1%}",
    ]

    if health.notes:
        lines.append("")
        lines.extend(f"  note: {note}" for note in health.notes)

    return "\n".join(lines)
