"""Unit tests for kg/retrieval.py — BM25 + cosine + RRF hybrid retriever."""

from typing import Optional

import pytest

from verityai.kg.retrieval import (
    HybridRetriever,
    _bm25_rank,
    _cosine_similarity,
    _tokenize,
)
from verityai.ontology.models import Rule


def make_rule(name: str, description: str = "", condition: str = "", category: str = "security"):
    return Rule(
        name=name,
        description=description or f"{name} description",
        category=category,
        condition=condition or f"{name} condition",
        severity="high",
        applies_to=["python"],
    )


class FakeKGClient:
    """Minimal stand-in for KGClient.get_rules_with_embeddings (Commit 3 adds the real one)."""

    def __init__(self, pairs: list[tuple[Rule, Optional[list[float]]]]):
        self._pairs = pairs

    def get_rules_with_embeddings(self, language: str):
        return self._pairs


class TestTokenize:
    def test_lowercases_and_splits_on_non_alphanumeric(self):
        assert _tokenize("Null Pointer!") == ["null", "pointer"]

    def test_splits_on_underscores_for_snake_case_names(self):
        assert _tokenize("no_null_dereference") == ["no", "null", "dereference"]

    def test_empty_string_yields_no_tokens(self):
        assert _tokenize("") == []


class TestBM25:
    def test_ranks_term_relevant_doc_first(self):
        rules = [
            make_rule("division_safety", description="check divide by zero errors"),
            make_rule("naming_convention", description="use descriptive variable names"),
        ]
        ranks, scores = _bm25_rank("divide by zero", rules)

        assert ranks[0] == 1
        assert 1 not in ranks  # no overlap with the naming_convention doc
        assert scores[0] > 0

    def test_idf_non_negative_when_term_in_all_docs(self):
        # "python" appears in every doc's condition text below (via make_rule
        # defaults) -- classic IDF would go negative here for a query term
        # that appears in >50% of the corpus; the ln(1+...) variant must not.
        rules = [
            make_rule("rule_a", description="python safety rule", condition="python check a"),
            make_rule("rule_b", description="python safety rule", condition="python check b"),
            make_rule("rule_c", description="python safety rule", condition="python check c"),
        ]
        ranks, scores = _bm25_rank("python", rules)

        assert all(score >= 0 for score in scores.values())
        assert len(ranks) == 3  # all three matched "python", none dropped/inverted

    def test_no_query_terms_yields_no_ranks(self):
        rules = [make_rule("rule_a")]
        ranks, scores = _bm25_rank("", rules)
        assert ranks == {}
        assert scores == {}

    def test_no_rules_yields_no_ranks(self):
        ranks, scores = _bm25_rank("anything", [])
        assert ranks == {}
        assert scores == {}

    def test_zero_score_docs_get_no_rank(self):
        rules = [
            make_rule("division_safety", description="check divide by zero errors"),
            make_rule("naming_convention", description="use descriptive variable names"),
        ]
        ranks, _ = _bm25_rank("divide by zero", rules)
        assert 1 not in ranks


class TestCosineSimilarity:
    def test_identical_vectors_similarity_one(self):
        assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_similarity_zero(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_guard(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_empty_vector_guard(self):
        assert _cosine_similarity([], [1.0]) == 0.0

    def test_mismatched_dimensions_guard(self):
        assert _cosine_similarity([1.0, 2.0], [1.0]) == 0.0


class TestHybridRetrieverDegradation:
    def test_no_embed_fn_degrades_to_lexical_only(self):
        rules = [make_rule("division_safety", description="check divide by zero")]
        client = FakeKGClient([(rules[0], None)])
        retriever = HybridRetriever(client, embed_fn=None)

        result = retriever.retrieve("divide by zero")

        assert result.mode == "lexical_only"
        assert result.degraded_reason == "no embed_fn configured"
        assert result.top_semantic_similarity is None

    def test_no_rules_for_language(self):
        client = FakeKGClient([])
        retriever = HybridRetriever(client, embed_fn=lambda text: [1.0])

        result = retriever.retrieve("anything", language="rust")

        assert result.rules == []
        assert result.mode == "lexical_only"
        assert "no rules found for language=rust" in result.degraded_reason

    def test_no_stored_embeddings_degrades(self):
        rules = [make_rule("division_safety", description="check divide by zero")]
        client = FakeKGClient([(rules[0], None)])
        retriever = HybridRetriever(client, embed_fn=lambda text: [1.0, 0.0])

        result = retriever.retrieve("divide by zero")

        assert result.mode == "lexical_only"
        assert result.degraded_reason == "no stored embeddings for this language"

    def test_embed_fn_raises_degrades_with_exception_text(self):
        rules = [make_rule("division_safety", description="check divide by zero")]
        client = FakeKGClient([(rules[0], [1.0, 0.0])])

        def broken_embed(text: str) -> list:
            raise RuntimeError("ollama unreachable")

        retriever = HybridRetriever(client, embed_fn=broken_embed)
        result = retriever.retrieve("divide by zero")

        assert result.mode == "lexical_only"
        assert result.degraded_reason == "embed_fn raised: ollama unreachable"

    def test_embed_fn_empty_vector_degrades(self):
        rules = [make_rule("division_safety", description="check divide by zero")]
        client = FakeKGClient([(rules[0], [1.0, 0.0])])
        retriever = HybridRetriever(client, embed_fn=lambda text: [])

        result = retriever.retrieve("divide by zero")

        assert result.mode == "lexical_only"
        assert result.degraded_reason == "embed_fn returned an empty vector"

    def test_partial_embedding_coverage_is_hybrid(self):
        rule_a = make_rule("division_safety", description="check divide by zero")
        rule_b = make_rule("naming_convention", description="use descriptive names")
        client = FakeKGClient([(rule_a, [1.0, 0.0]), (rule_b, None)])
        retriever = HybridRetriever(client, embed_fn=lambda text: [1.0, 0.0])

        result = retriever.retrieve("divide by zero")

        assert result.mode == "hybrid"
        assert result.degraded_reason is None


class TestHybridRetrieverFusion:
    def test_rrf_fused_score_matches_hand_calculation(self):
        # rule_a: lexical rank 1 (query term overlap), semantic rank 1 (identical vector)
        # rule_b: no lexical overlap, semantic rank 2
        rule_a = make_rule("division_safety", description="check divide by zero")
        rule_b = make_rule("naming_convention", description="use descriptive variable names")
        client = FakeKGClient(
            [
                (rule_a, [1.0, 0.0]),
                (rule_b, [0.0, 1.0]),
            ]
        )
        retriever = HybridRetriever(client, embed_fn=lambda text: [1.0, 0.0], rrf_k=60)

        result = retriever.retrieve("divide by zero")

        assert result.mode == "hybrid"
        top = result.rules[0]
        assert top.rule.name == "division_safety"
        expected_fused = 1.0 / (60 + 1) + 1.0 / (60 + 1)  # rank 1 in both scorers
        assert top.score == pytest.approx(expected_fused)
        assert top.provenance["method"] == "hybrid"
        assert top.provenance["lexical_rank"] == 1
        assert top.provenance["semantic_rank"] == 1

    def test_lexical_only_result_has_lexical_method(self):
        rule = make_rule("division_safety", description="check divide by zero")
        client = FakeKGClient([(rule, None)])
        retriever = HybridRetriever(client, embed_fn=None)

        result = retriever.retrieve("divide by zero")

        assert len(result.rules) == 1
        assert result.rules[0].provenance["method"] == "lexical"
        assert result.rules[0].provenance["semantic_rank"] is None

    def test_semantic_only_result_has_semantic_method(self):
        # No query-term overlap at all -> zero BM25 score -> no lexical rank,
        # but the embedding still matches perfectly.
        rule = make_rule("division_safety", description="zzz completely unrelated text")
        client = FakeKGClient([(rule, [1.0, 0.0])])
        retriever = HybridRetriever(client, embed_fn=lambda text: [1.0, 0.0])

        result = retriever.retrieve("qqq nomatch")

        assert len(result.rules) == 1
        assert result.rules[0].provenance["method"] == "semantic"
        assert result.rules[0].provenance["lexical_rank"] is None

    def test_ties_broken_by_rule_name_for_determinism(self):
        rule_b = make_rule("bbb_rule", description="shared term overlap")
        rule_a = make_rule("aaa_rule", description="shared term overlap")
        client = FakeKGClient([(rule_b, None), (rule_a, None)])
        retriever = HybridRetriever(client, embed_fn=None)

        result = retriever.retrieve("shared term overlap")

        assert [r.rule.name for r in result.rules] == ["aaa_rule", "bbb_rule"]

    def test_top_k_truncation(self):
        rules = [
            make_rule(f"rule_{i}", description="shared overlap term", condition="shared")
            for i in range(5)
        ]
        client = FakeKGClient([(r, None) for r in rules])
        retriever = HybridRetriever(client, embed_fn=None)

        result = retriever.retrieve("shared overlap term", top_k=2)

        assert len(result.rules) == 2

    def test_top_semantic_similarity_clamped_and_populated(self):
        rule = make_rule("division_safety", description="check divide by zero")
        client = FakeKGClient([(rule, [1.0, 0.0])])
        retriever = HybridRetriever(client, embed_fn=lambda text: [1.0, 0.0])

        result = retriever.retrieve("divide by zero")

        assert result.top_semantic_similarity is not None
        assert 0.0 <= result.top_semantic_similarity <= 1.0

    def test_provenance_keys_always_present(self):
        rule = make_rule("division_safety", description="check divide by zero")
        client = FakeKGClient([(rule, None)])
        retriever = HybridRetriever(client, embed_fn=None)

        result = retriever.retrieve("divide by zero")

        provenance = result.rules[0].provenance
        for key in (
            "method",
            "lexical_rank",
            "lexical_score",
            "semantic_rank",
            "semantic_score",
            "fused_score",
        ):
            assert key in provenance

    def test_no_overlap_and_no_embeddings_yields_empty_results(self):
        rule = make_rule("division_safety", description="check divide by zero")
        client = FakeKGClient([(rule, None)])
        retriever = HybridRetriever(client, embed_fn=None)

        result = retriever.retrieve("completely unrelated query text")

        assert result.rules == []
