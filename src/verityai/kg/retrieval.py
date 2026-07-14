"""Hybrid (lexical + semantic) rule retrieval with explainable provenance.

Deliberately depends on nothing but `ontology` and the stdlib — no `neural`
import, per the module dependency rule in CLAUDE.md (`kg/` must never depend
on `neural/`). Semantic scoring is optional by design: callers inject an
`embed_fn`, and any failure to use it (missing, raises, no stored vectors)
degrades gracefully to lexical-only ranking. The degradation is always
reported in `RetrievalResult`, never silently swallowed — same philosophy as
the `NOT_VERIFIED` status in the symbolic layer (ADR-0001).
"""

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from verityai.ontology.models import Rule

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_BM25_K1 = 1.5
_BM25_B = 0.75


class SupportsRuleEmbeddingLookup(Protocol):
    """Structural type for the KG client dependency this module needs.

    Deliberately a Protocol rather than importing `kg.client.KGClient`
    directly: it lets `HybridRetriever` be developed and tested against a
    lightweight fake before `KGClient.get_rules_with_embeddings` exists.
    """

    def get_rules_with_embeddings(
        self, language: str
    ) -> list[tuple[Rule, Optional[list[float]]]]: ...


@dataclass
class ScoredRule:
    """A rule ranked by the hybrid retriever, with full scoring provenance."""

    rule: Rule
    score: float
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    """Outcome of a `HybridRetriever.retrieve()` call."""

    rules: list[ScoredRule]
    mode: str  # "hybrid" | "lexical_only"
    degraded_reason: Optional[str] = None
    top_semantic_similarity: Optional[float] = None  # clamped to [0, 1]


def _tokenize(text: str) -> list[str]:
    """Lowercase and split on any run of non-alphanumeric characters.

    Underscores are treated as separators too (not part of `[a-z0-9]+`), so
    `no_null_dereference` tokenizes as `["no", "null", "dereference"]` —
    important since rule names are snake_case.
    """
    return _TOKEN_RE.findall(text.lower())


def _rule_document(rule: Rule) -> str:
    return f"{rule.name} {rule.description} {rule.condition}"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]; 0.0 for empty, mismatched, or zero vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _bm25_rank(query: str, rules: list[Rule]) -> tuple[dict[int, int], dict[int, float]]:
    """Rank rules against a query with BM25 (non-negative IDF variant).

    Classic IDF (`ln((N - df + 0.5) / (df + 0.5))`) goes negative for terms
    that appear in more than half the corpus, which silently inverts
    rankings on a small (~50-doc) corpus. The `ln(1 + ...)` variant used
    here (Lucene/Okapi-style) stays non-negative for any df.

    Only rules with a strictly positive score receive a rank — a score of
    0 means no query-term overlap, which is "not lexically relevant," not
    "worst-ranked relevant result."
    """
    query_terms = _tokenize(query)
    if not query_terms or not rules:
        return {}, {}

    docs_tokens = [_tokenize(_rule_document(rule)) for rule in rules]
    doc_lengths = [len(toks) for toks in docs_tokens]
    n_docs = len(rules)
    avgdl = sum(doc_lengths) / n_docs if n_docs else 0.0

    unique_query_terms = set(query_terms)
    doc_freq = {term: sum(1 for toks in docs_tokens if term in toks) for term in unique_query_terms}

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
    positive.sort(key=lambda pair: (-pair[1], rules[pair[0]].name))

    ranks: dict[int, int] = {}
    score_map: dict[int, float] = {}
    for rank, (idx, s) in enumerate(positive, start=1):
        ranks[idx] = rank
        score_map[idx] = s
    return ranks, score_map


class HybridRetriever:
    """Fuses lexical (BM25) and semantic (cosine) rule rankings via RRF."""

    def __init__(
        self,
        kg_client: SupportsRuleEmbeddingLookup,
        embed_fn: Optional[Callable[[str], list[float]]] = None,
        rrf_k: int = 60,
    ):
        """Initialize the retriever.

        Args:
            kg_client: Anything exposing `get_rules_with_embeddings(language)`
            embed_fn: Optional embedding function (e.g. `OllamaClient.embed`).
                Injected rather than constructed here so `kg/` never imports
                `neural/` directly.
            rrf_k: Reciprocal Rank Fusion constant (higher = flatter fusion)
        """
        self.kg_client = kg_client
        self.embed_fn = embed_fn
        self.rrf_k = rrf_k

    def retrieve(self, query: str, language: str = "python", top_k: int = 8) -> RetrievalResult:
        """Retrieve the top-k rules for `query`, fusing lexical + semantic signal.

        Args:
            query: Free-text query (typically the user's generation prompt)
            language: Programming language filter
            top_k: Max number of rules to return

        Returns:
            RetrievalResult with ranked rules and full mode/degradation provenance
        """
        pairs = self.kg_client.get_rules_with_embeddings(language)
        if not pairs:
            return RetrievalResult(
                rules=[],
                mode="lexical_only",
                degraded_reason=f"no rules found for language={language}",
                top_semantic_similarity=None,
            )

        rules = [pair[0] for pair in pairs]
        embeddings = [pair[1] for pair in pairs]

        lexical_ranks, lexical_scores = _bm25_rank(query, rules)
        semantic_ranks, semantic_scores, mode, degraded_reason = self._semantic_rank(
            query, rules, embeddings
        )

        fused_scores: dict[int, float] = {}
        for idx in range(len(rules)):
            score = 0.0
            if idx in lexical_ranks:
                score += 1.0 / (self.rrf_k + lexical_ranks[idx])
            if idx in semantic_ranks:
                score += 1.0 / (self.rrf_k + semantic_ranks[idx])
            if score > 0:
                fused_scores[idx] = score

        ordered = sorted(fused_scores.keys(), key=lambda idx: (-fused_scores[idx], rules[idx].name))
        top_indices = ordered[:top_k]

        scored_rules = []
        for idx in top_indices:
            has_lexical = idx in lexical_ranks
            has_semantic = idx in semantic_ranks
            if has_lexical and has_semantic:
                method = "hybrid"
            elif has_semantic:
                method = "semantic"
            else:
                method = "lexical"

            provenance = {
                "method": method,
                "lexical_rank": lexical_ranks.get(idx),
                "lexical_score": lexical_scores.get(idx),
                "semantic_rank": semantic_ranks.get(idx),
                "semantic_score": semantic_scores.get(idx),
                "fused_score": fused_scores[idx],
            }
            scored_rules.append(
                ScoredRule(rule=rules[idx], score=fused_scores[idx], provenance=provenance)
            )

        top_semantic_similarity: Optional[float] = None
        if mode == "hybrid":
            top_sims = [semantic_scores[idx] for idx in top_indices if idx in semantic_scores]
            if top_sims:
                top_semantic_similarity = max(0.0, min(1.0, max(top_sims)))

        return RetrievalResult(
            rules=scored_rules,
            mode=mode,
            degraded_reason=degraded_reason,
            top_semantic_similarity=top_semantic_similarity,
        )

    def _semantic_rank(
        self,
        query: str,
        rules: list[Rule],
        embeddings: list[Optional[list[float]]],
    ) -> tuple[dict[int, int], dict[int, float], str, Optional[str]]:
        """Rank rules by cosine similarity, or explain why that's not possible.

        Returns (semantic_ranks, semantic_scores, mode, degraded_reason).
        """
        if self.embed_fn is None:
            return {}, {}, "lexical_only", "no embed_fn configured"

        if not any(e is not None for e in embeddings):
            return {}, {}, "lexical_only", "no stored embeddings for this language"

        try:
            query_vector = self.embed_fn(query)
        except Exception as e:
            return {}, {}, "lexical_only", f"embed_fn raised: {e}"

        if not query_vector:
            return {}, {}, "lexical_only", "embed_fn returned an empty vector"

        similarities = []
        for idx, vector in enumerate(embeddings):
            if vector is None:
                continue
            similarities.append((idx, _cosine_similarity(query_vector, vector)))

        if not similarities:
            return {}, {}, "lexical_only", "no stored embeddings for this language"

        similarities.sort(key=lambda pair: (-pair[1], rules[pair[0]].name))

        ranks: dict[int, int] = {}
        scores: dict[int, float] = {}
        for rank, (idx, sim) in enumerate(similarities, start=1):
            ranks[idx] = rank
            scores[idx] = sim

        return ranks, scores, "hybrid", None
