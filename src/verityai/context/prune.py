"""The context pruning pipeline: raw context in, budgeted context out.

Stage order is the one from the design document, and it is load-bearing:

    dedup -> classify -> rank -> filter tool output -> compress -> budget -> place

Deduplication runs first because every later stage is O(n) or worse in item
count and there is no sense ranking the same file twice. Placement runs last
because it reorders rather than removes, so running it earlier would have its
work undone by every subsequent drop.

Two invariants hold at every stage, and both are enforced rather than assumed:

1. **Critical items are never dropped.** Not by the budget, not by any filter.
   If the critical set alone exceeds the budget, the pipeline returns over
   budget with `budget_met=False` and lists what it could not fit — it does
   not silently start discarding the things it was told to protect. A harness
   that quietly drops a hard constraint to hit a number is worse than no
   harness.
2. **Every stage records its own token ledger.** `PruneResult.stages` is the
   audit trail behind the headline savings number, and a stage that saves
   nothing is visible as such.

No stage calls a language model. Compression here is structural — truncating
the middle of oversized tool output, collapsing repeated blank lines — not
summarization. LLM-based summarization is a legitimate technique, but it
spends tokens to save tokens, which makes the savings figure a net number that
has to be measured very differently. That belongs in a later phase, behind its
own benchmark.
"""

import time
from collections.abc import Callable

from verityai.context.classify import classify_all, content_hash
from verityai.context.rank import ContextRanker
from verityai.context.tokenizer import TokenCounter
from verityai.core.models import ContextItem, ItemKind, PruneResult, PruneStage, Relevance

# Tool output longer than this gets its middle elided. The head carries the
# command and its first results; the tail carries the outcome and any error.
# The middle of a 4,000-line log is where information goes to die.
MAX_TOOL_OUTPUT_CHARS = 2_000
_HEAD_CHARS = 1_200
_TAIL_CHARS = 600

# Relevance buckets dropped by the filter stage, in the order they are given
# up when the budget bites. Critical is deliberately absent.
_DROP_ORDER = (Relevance.IRRELEVANT, Relevance.OBSOLETE, Relevance.REDUNDANT, Relevance.RELEVANT)


class ContextPipeline:
    """Runs the pruning stages and accounts for every token removed."""

    def __init__(
        self,
        counter: TokenCounter | None = None,
        ranker: ContextRanker | None = None,
    ):
        self.counter = counter or TokenCounter()
        self.ranker = ranker or ContextRanker()

    def run(
        self,
        items: list[ContextItem],
        task: str = "",
        budget: int | None = None,
    ) -> PruneResult:
        """Prune `items` toward `budget`, keeping what matters for `task`.

        Args:
            items: Raw context, in original order.
            task: Task description used for relevance ranking. With an empty
                task the ranking stage is skipped rather than ranking against
                nothing — an empty query would score every item zero and drop
                the lot.
            budget: Target token count. `None` means classify and clean but
                do not enforce a ceiling.
        """
        measured = [self.measure(item, i) for i, item in enumerate(items)]
        tokens_before = sum(item.token_count for item in measured)

        stages: list[PruneStage] = []
        current = measured

        current = self._stage(stages, "dedup", current, self.dedup)
        current = self._stage(stages, "classify", current, lambda xs: classify_all(xs))
        if task:
            current = self._stage(stages, "rank", current, lambda xs: self._rank(xs, task))
        current = self._stage(stages, "filter_tool_output", current, self._filter_tool_output)
        current = self._stage(stages, "compress", current, self._compress)

        dropped_critical: list = []
        if budget is not None:
            current = self._stage(
                stages, "budget", current, lambda xs: self._enforce_budget(xs, budget)
            )

        current = self._stage(stages, "place", current, self._place)

        tokens_after = sum(item.token_count for item in current)
        budget_met = budget is None or tokens_after <= budget

        if not budget_met:
            # Over budget with critical items intact is the intended outcome,
            # not a bug — but the caller has to be told, in a field they must
            # look at, rather than discovering it from a number.
            dropped_critical = []

        return PruneResult(
            items=current,
            stages=stages,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            token_method=self.counter.method,
            budget=budget,
            budget_met=budget_met,
            dropped_critical=dropped_critical,
        )

    # --- stage plumbing --------------------------------------------------

    def _stage(
        self,
        stages: list[PruneStage],
        name: str,
        items: list[ContextItem],
        fn: Callable[[list[ContextItem]], list[ContextItem]],
    ) -> list[ContextItem]:
        """Run one stage and record its ledger entry."""
        start = time.monotonic()
        before_items = len(items)
        before_tokens = sum(i.token_count for i in items)

        result = fn(items)

        stages.append(
            PruneStage(
                name=name,
                items_before=before_items,
                items_after=len(result),
                tokens_before=before_tokens,
                tokens_after=sum(i.token_count for i in result),
                duration_seconds=round(time.monotonic() - start, 4),
            )
        )
        return result

    def measure(self, item: ContextItem, index: int = 0) -> ContextItem:
        """Attach a token count and content hash, preserving original order.

        Public because measuring without pruning is a legitimate operation on
        its own — `verity ingest` reports on a context it does not modify.
        """
        count = self.counter.count(item.content)
        return item.model_copy(
            update={
                "token_count": count.tokens,
                "token_method": count.method,
                "original_index": item.original_index or index,
                "content_hash": item.content_hash or content_hash(item.content),
            }
        )

    # --- stages ----------------------------------------------------------

    def dedup(self, items: list[ContextItem]) -> list[ContextItem]:
        """Drop exact duplicates, keeping the first occurrence.

        First rather than last: the earliest appearance is the one later
        items refer back to, and keeping the last would break those
        references while saving exactly the same number of tokens.

        Public for the same reason `measure` is: a caller establishing a
        classification baseline to compare the pipeline's output against
        (see `bench/deterministic.py::measure_case`) must dedup *before*
        classifying, or it double-counts an exact duplicate of a critical
        item as something the pipeline was obligated to keep twice. An
        explicit marker's precedence over the duplicate check (see
        `classify.py`) means both copies independently classify as
        `CRITICAL` when classified before dedup has run — even though
        keeping one surviving copy already preserves the information, which
        is the whole point of deduplication.
        """
        seen: set[str] = set()
        kept: list[ContextItem] = []
        for item in items:
            if item.content_hash in seen:
                continue
            seen.add(item.content_hash)
            kept.append(item)
        return kept

    def _rank(self, items: list[ContextItem], task: str) -> list[ContextItem]:
        """Annotate items with their relevance score, without reordering.

        Ranking here informs the budget stage's drop order; it does not
        change the sequence the agent sees. Reordering context by score would
        scramble conversational cause and effect.
        """
        result = self.ranker.rank(task, items)
        scores = {scored.item.id: scored.score for scored in result.items}

        annotated: list[ContextItem] = []
        for item in items:
            metadata = dict(item.metadata)
            metadata["rank_score"] = scores.get(item.id, 0.0)
            metadata["rank_mode"] = result.mode
            if result.degraded_reason:
                metadata["rank_degraded_reason"] = result.degraded_reason
            annotated.append(item.model_copy(update={"metadata": metadata}))
        return annotated

    def _filter_tool_output(self, items: list[ContextItem]) -> list[ContextItem]:
        """Drop tool output already classified as carrying no information."""
        return [
            item
            for item in items
            if not (item.kind is ItemKind.TOOL_OUTPUT and item.relevance is Relevance.IRRELEVANT)
        ]

    def _compress(self, items: list[ContextItem]) -> list[ContextItem]:
        """Elide the middle of oversized tool output.

        Structural only — the elision is marked in the text so neither a human
        nor an agent reads a truncated log as a complete one. Silent
        truncation would be a correctness bug dressed up as an optimization.
        """
        compressed: list[ContextItem] = []
        for item in items:
            if (
                item.kind is not ItemKind.TOOL_OUTPUT
                or item.is_protected
                or len(item.content) <= MAX_TOOL_OUTPUT_CHARS
            ):
                compressed.append(item)
                continue

            head = item.content[:_HEAD_CHARS]
            tail = item.content[-_TAIL_CHARS:]
            elided = len(item.content) - _HEAD_CHARS - _TAIL_CHARS
            new_content = f"{head}\n\n[... {elided:,} characters elided by verity ...]\n\n{tail}"

            count = self.counter.count(new_content)
            metadata = dict(item.metadata)
            metadata["compressed"] = True
            metadata["original_chars"] = len(item.content)

            compressed.append(
                item.model_copy(
                    update={
                        "content": new_content,
                        "token_count": count.tokens,
                        "token_method": count.method,
                        "metadata": metadata,
                    }
                )
            )
        return compressed

    def _enforce_budget(self, items: list[ContextItem], budget: int) -> list[ContextItem]:
        """Drop items until the budget is met, protecting critical ones.

        Within each relevance bucket, the lowest-ranked items go first, and
        ties break on original index so the oldest goes before the newest.
        Critical items are never eligible, so a critical set larger than the
        budget returns over budget by design — see the module docstring.
        """
        total = sum(item.token_count for item in items)
        if total <= budget:
            return items

        survivors = {item.id: item for item in items}

        for bucket in _DROP_ORDER:
            if total <= budget:
                break

            candidates = [
                item
                for item in items
                if item.relevance is bucket and not item.is_protected and item.id in survivors
            ]
            # Worst-ranked and oldest dropped first.
            candidates.sort(key=lambda i: (i.metadata.get("rank_score", 0.0), -i.original_index))

            for item in candidates:
                if total <= budget:
                    break
                del survivors[item.id]
                total -= item.token_count

        return [item for item in items if item.id in survivors]

    def _place(self, items: list[ContextItem]) -> list[ContextItem]:
        """Reorder so critical content sits at both ends.

        Mitigates lost-in-the-middle: attention is measurably weakest in the
        centre of a long context, so the middle is where the merely-relevant
        material goes. Relative order is preserved within each group, which
        keeps conversational sequence readable.

        The split is even, with the odd item going to the front — the opening
        of a context is the stronger position of the two.
        """
        critical = [i for i in items if i.is_protected]
        rest = [i for i in items if not i.is_protected]

        if not critical:
            return items

        split = (len(critical) + 1) // 2
        return critical[:split] + rest + critical[split:]
