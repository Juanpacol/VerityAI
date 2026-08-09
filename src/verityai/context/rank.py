"""Relevance ranking of context items against the current task.

This is `kg/retrieval.py` from the pre-pivot codebase, generalized. That
module ranked `Rule` objects for a generation prompt using BM25 fused with
embedding cosine similarity via Reciprocal Rank Fusion; the scoring maths was
never rule-specific, only its type signature was. Here it ranks arbitrary text
against a task description, and the semantic half stays optional and injected
for the same reason it was before — the ranker must work with nothing but the
standard library, and must say so when it is running degraded rather than
quietly returning worse results.

BM25 detail worth preserving: the IDF term is the `ln(1 + ...)` Lucene variant,
not the classic `ln((N - df + 0.5) / (df + 0.5))`. Classic IDF goes negative
for terms appearing in more than half the corpus, which silently inverts
rankings on small corpora. A single conversation's context is a small corpus.
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from verityai.core.models import ContextItem

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_BM25_K1 = 1.5
_BM25_B = 0.75

DEFAULT_RRF_K = 60


@dataclass
class ScoredItem:
    """A context item with its rank and full scoring provenance."""

    item: ContextItem
    score: float
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class RankingResult:
    """Outcome of a ranking pass, including why it degraded if it did."""

    items: list[ScoredItem]
    mode: str  # "hybrid" | "lexical_only"
    degraded_reason: str | None = None


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on any non-alphanumeric run.

    Underscores separate rather than join, so `parse_context_item` tokenizes
    as `["parse", "context", "item"]`. Identifiers are most of what a code
    context contains, and matching their parts is the whole point.
    """
    return _TOKEN_RE.findall(text.lower())


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0.0 for empty, mismatched, or zero-norm vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    # strict=True is safe and desirable: the length guard above already
    # returned for mismatched vectors, so a raise here would mean that guard
    # was bypassed -- a bug worth surfacing rather than silently truncating.
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def bm25_rank(query: str, documents: list[str]) -> tuple[dict[int, int], dict[int, float]]:
    """Rank `documents` against `query`. Returns (ranks, scores) by index.

    Only strictly-positive scores get a rank. A zero score means no query-term
    overlap at all, which is "not lexically relevant" — materially different
    from "the worst of the relevant results", and conflating them puts
    unrelated content into a pruned context.
    """
    query_terms = _tokenize(query)
    if not query_terms or not documents:
        return {}, {}

    docs_tokens = [_tokenize(doc) for doc in documents]
    doc_lengths = [len(toks) for toks in docs_tokens]
    n_docs = len(documents)
    avgdl = sum(doc_lengths) / n_docs if n_docs else 0.0

    unique_terms = set(query_terms)
    doc_freq = {term: sum(1 for toks in docs_tokens if term in toks) for term in unique_terms}

    scores: list[float] = []
    for idx, toks in enumerate(docs_tokens):
        term_counts: dict[str, int] = {}
        for tok in toks:
            term_counts[tok] = term_counts.get(tok, 0) + 1

        dl = doc_lengths[idx]
        length_norm = 1 - _BM25_B + _BM25_B * (dl / avgdl if avgdl else 0.0)

        doc_score = 0.0
        for term in query_terms:
            tf = term_counts.get(term, 0)
            if tf == 0:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            doc_score += idf * (tf * (_BM25_K1 + 1)) / (tf + _BM25_K1 * length_norm)
        scores.append(doc_score)

    positive = [(idx, s) for idx, s in enumerate(scores) if s > 0]
    # Tie-break on index so ranking is deterministic across runs — a ranker
    # that reorders equal-scoring items run to run would make the benchmark
    # unreproducible for no reason.
    positive.sort(key=lambda pair: (-pair[1], pair[0]))

    ranks: dict[int, int] = {}
    score_map: dict[int, float] = {}
    for rank, (idx, s) in enumerate(positive, start=1):
        ranks[idx] = rank
        score_map[idx] = s
    return ranks, score_map


class ContextRanker:
    """Ranks context items against a task, lexically and optionally semantically.

    `embed_fn` is injected rather than constructed. In the pre-pivot codebase
    that was to stop `kg/` importing `neural/`; here the reason is stronger —
    embedding the context costs tokens or an API round trip, so it must be an
    explicit, opt-in choice by the caller, never something the ranker decides
    to do on its own behalf.
    """

    def __init__(
        self,
        embed_fn: Callable[[str], list[float]] | None = None,
        rrf_k: int = DEFAULT_RRF_K,
    ):
        self.embed_fn = embed_fn
        self.rrf_k = rrf_k

    def rank(self, task: str, items: list[ContextItem]) -> RankingResult:
        """Rank `items` by relevance to `task`, best first.

        Items with no lexical and no semantic signal are omitted entirely
        rather than appended at the end — the caller needs to distinguish
        "ranked last" from "never matched".
        """
        if not items:
            return RankingResult(items=[], mode="lexical_only", degraded_reason="no items to rank")

        documents = [item.content for item in items]
        lexical_ranks, lexical_scores = bm25_rank(task, documents)
        semantic_ranks, semantic_scores, mode, degraded_reason = self._semantic_rank(
            task, documents
        )

        fused: dict[int, float] = {}
        for idx in range(len(items)):
            score = 0.0
            if idx in lexical_ranks:
                score += 1.0 / (self.rrf_k + lexical_ranks[idx])
            if idx in semantic_ranks:
                score += 1.0 / (self.rrf_k + semantic_ranks[idx])
            if score > 0:
                fused[idx] = score

        ordered = sorted(fused.keys(), key=lambda idx: (-fused[idx], idx))

        scored: list[ScoredItem] = []
        for idx in ordered:
            has_lexical = idx in lexical_ranks
            has_semantic = idx in semantic_ranks
            if has_lexical and has_semantic:
                method = "hybrid"
            elif has_semantic:
                method = "semantic"
            else:
                method = "lexical"

            scored.append(
                ScoredItem(
                    item=items[idx],
                    score=fused[idx],
                    provenance={
                        "method": method,
                        "lexical_rank": lexical_ranks.get(idx),
                        "lexical_score": lexical_scores.get(idx),
                        "semantic_rank": semantic_ranks.get(idx),
                        "semantic_score": semantic_scores.get(idx),
                        "fused_score": fused[idx],
                    },
                )
            )

        return RankingResult(items=scored, mode=mode, degraded_reason=degraded_reason)

    def _semantic_rank(
        self, query: str, documents: list[str]
    ) -> tuple[dict[int, int], dict[int, float], str, str | None]:
        """Cosine ranking, or a stated reason why it could not run."""
        if self.embed_fn is None:
            return {}, {}, "lexical_only", "no embed_fn configured"

        try:
            query_vector = self.embed_fn(query)
        except Exception as e:
            return {}, {}, "lexical_only", f"embed_fn raised on query: {e}"

        if not query_vector:
            return {}, {}, "lexical_only", "embed_fn returned an empty vector"

        similarities: list[tuple[int, float]] = []
        for idx, doc in enumerate(documents):
            try:
                vector = self.embed_fn(doc)
            except Exception:
                # One bad document must not sink the whole pass; it simply
                # gets no semantic signal and falls back to its lexical rank.
                continue
            if vector:
                similarities.append((idx, _cosine_similarity(query_vector, vector)))

        if not similarities:
            return {}, {}, "lexical_only", "embed_fn produced no usable document vectors"

        similarities.sort(key=lambda pair: (-pair[1], pair[0]))

        ranks: dict[int, int] = {}
        scores: dict[int, float] = {}
        for rank, (idx, sim) in enumerate(similarities, start=1):
            ranks[idx] = rank
            scores[idx] = sim

        return ranks, scores, "hybrid", None
