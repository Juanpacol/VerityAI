"""Tests for token counting.

The recurring theme: a count is never allowed to travel without its method.
Every savings figure this project publishes rests on that, and the failure
mode -- an estimate presented as a measurement -- is silent by nature, so it
has to be caught structurally rather than noticed.
"""

from verityai.context.tokenizer import (
    DEFAULT_WINDOW,
    TokenCount,
    TokenCounter,
    window_for_model,
)


class TestMethodReporting:
    def test_every_count_carries_its_method(self):
        counter = TokenCounter()
        result = counter.count("some text to count")

        assert result.method == counter.method
        assert result.method != "unmeasured"

    def test_estimates_are_marked_as_such(self):
        counter = TokenCounter()
        counter._encoder = None
        counter.method = "heuristic:chars/4"

        assert counter.count("hello world").is_estimate is True
        assert "(est.)" in str(counter.count("hello world"))

    def test_exact_counts_are_not_marked_as_estimates(self):
        count = TokenCount(100, "tiktoken:cl100k_base")

        assert count.is_estimate is False
        assert "(est.)" not in str(count)

    def test_a_failed_encoder_degrades_rather_than_raising(self):
        counter = TokenCounter(encoding="no-such-encoding-exists")

        assert counter.method == "heuristic:chars/4"
        assert counter.count("still works").tokens > 0


class TestCounting:
    def test_empty_text_is_zero_tokens(self):
        assert TokenCounter().count("").tokens == 0

    def test_whitespace_only_text_is_zero_tokens(self):
        counter = TokenCounter()
        counter._encoder = None
        counter.method = "heuristic:chars/4"

        assert counter.count("   \n\n  ").tokens == 0

    def test_longer_text_counts_more(self):
        counter = TokenCounter()

        short = counter.count("hello").tokens
        long = counter.count("hello " * 100).tokens

        assert long > short

    def test_count_all_equals_the_sum_of_parts(self):
        """The savings table must add up, so the total is defined as the sum."""
        counter = TokenCounter()
        texts = ["first piece", "second piece", "third piece"]

        total = counter.count_all(texts).tokens
        parts = sum(counter.count(t).tokens for t in texts)

        assert total == parts

    def test_the_heuristic_collapses_whitespace(self):
        """Indentation is nearly free in real tokenizers; chars/4 must not
        charge full price for it, or it would badly over-count formatted code."""
        counter = TokenCounter()
        counter._encoder = None
        counter.method = "heuristic:chars/4"

        dense = counter.count("a b c d").tokens
        padded = counter.count("a     b\n\n\n     c        d").tokens

        assert dense == padded


class TestWindows:
    def test_known_models_resolve(self):
        assert window_for_model("claude-sonnet-5") == 200_000
        assert window_for_model("gpt-4o") == 128_000

    def test_dated_model_ids_resolve_by_prefix(self):
        assert window_for_model("claude-haiku-4-5-20251001") == 200_000

    def test_unknown_models_get_the_conservative_default(self):
        assert window_for_model("some-future-model") == DEFAULT_WINDOW

    def test_no_model_gets_the_default(self):
        assert window_for_model(None) == DEFAULT_WINDOW

    def test_matching_is_case_insensitive(self):
        assert window_for_model("Claude-Sonnet-5") == 200_000
