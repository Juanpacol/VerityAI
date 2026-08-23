"""Tests for relevance ranking.

Carried over from the pre-pivot retrieval layer along with the code: BM25 with
a non-negative IDF term, RRF fusion, and an optional semantic half that
reports why it degraded instead of quietly returning worse results.
"""

from verityai.context.rank import ContextRanker, bm25_rank

from ..conftest import item


class TestBM25:
    def test_matching_documents_outrank_non_matching(self):
        ranks, _ = bm25_rank("caching layer", ["the caching layer design", "unrelated text"])

        assert ranks[0] == 1
        assert 1 not in ranks or ranks.get(1) != 1

    def test_documents_with_no_overlap_get_no_rank(self):
        """No rank is meaningfully different from 'ranked last'."""
        ranks, _ = bm25_rank("caching", ["about caching", "entirely unrelated"])

        assert 0 in ranks
        assert 1 not in ranks

    def test_idf_stays_non_negative_on_a_small_corpus(self):
        """The classic IDF formula goes negative for terms in >half the corpus,
        which inverts rankings. A conversation is a small corpus."""
        documents = ["common term here", "common term there", "common term everywhere"]

        _, scores = bm25_rank("common", documents)

        assert all(score > 0 for score in scores.values())

    def test_ranking_is_deterministic_across_ties(self):
        documents = ["identical text", "identical text", "identical text"]

        first, _ = bm25_rank("identical", documents)
        second, _ = bm25_rank("identical", documents)

        assert first == second

    def test_an_empty_query_ranks_nothing(self):
        ranks, _ = bm25_rank("", ["some document"])

        assert ranks == {}

    def test_identifiers_are_split_on_underscores(self):
        ranks, _ = bm25_rank("context item", ["parse_context_item does the work", "unrelated"])

        assert ranks.get(0) == 1


class TestDegradation:
    def test_without_an_embed_fn_the_mode_is_lexical_only(self):
        result = ContextRanker().rank("query", [item("some content about the query")])

        assert result.mode == "lexical_only"
        assert result.degraded_reason == "no embed_fn configured"

    def test_a_raising_embed_fn_degrades_with_a_reason(self):
        def broken(text):
            raise RuntimeError("the embedding service is down")

        result = ContextRanker(embed_fn=broken).rank("query", [item("content about query")])

        assert result.mode == "lexical_only"
        assert "the embedding service is down" in result.degraded_reason

    def test_an_empty_vector_degrades_with_a_reason(self):
        result = ContextRanker(embed_fn=lambda text: []).rank("query", [item("query content")])

        assert result.mode == "lexical_only"
        assert "empty vector" in result.degraded_reason

    def test_degradation_never_raises(self):
        """A ranking failure must cost relevance quality, never the whole run."""
        result = ContextRanker(embed_fn=lambda t: 1 / 0).rank("q", [item("content")])

        assert result.mode == "lexical_only"

    def test_empty_input_is_reported_not_crashed(self):
        result = ContextRanker().rank("query", [])

        assert result.items == []
        assert result.degraded_reason == "no items to rank"


class TestHybridFusion:
    def test_a_working_embed_fn_produces_hybrid_mode(self):
        def embed(text):
            return [float(len(text)), 1.0, 0.5]

        result = ContextRanker(embed_fn=embed).rank("query", [item("query content here")])

        assert result.mode == "hybrid"
        assert result.degraded_reason is None

    def test_partial_embedding_failure_sets_degraded_reason(self):
        """ADR-0037: mode stays hybrid -- most documents genuinely got a
        semantic score -- but invariant 5 says the partial loss must still
        be reported, not swallowed the way it was before this fix."""

        def embed(text):
            if text == "bad document":
                raise RuntimeError("boom")
            return [float(len(text)), 1.0, 0.5]

        items = [item("bad document"), item("query content here")]
        result = ContextRanker(embed_fn=embed).rank("query", items)

        assert result.mode == "hybrid"
        assert result.degraded_reason is not None
        assert "1/2" in result.degraded_reason

    def test_no_degraded_reason_when_all_embeddings_succeed(self):
        def embed(text):
            return [float(len(text)), 1.0, 0.5]

        items = [item("first"), item("second query content")]
        result = ContextRanker(embed_fn=embed).rank("query", items)

        assert result.mode == "hybrid"
        assert result.degraded_reason is None

    def test_provenance_names_the_method_used(self):
        result = ContextRanker().rank("caching", [item("the caching layer")])

        assert result.items[0].provenance["method"] == "lexical"
        assert result.items[0].provenance["lexical_rank"] == 1

    def test_provenance_carries_the_fused_score(self):
        result = ContextRanker().rank("caching", [item("the caching layer")])

        assert result.items[0].provenance["fused_score"] == result.items[0].score

    def test_unmatched_items_are_omitted_not_appended(self):
        result = ContextRanker().rank(
            "caching", [item("the caching layer"), item("completely unrelated")]
        )

        assert len(result.items) == 1
