"""Unit tests for evidence/classify.py -- the injected-LLM classification
layer. Reuses tests/fakes.py's FakeLLMClient/AlwaysFailingLLMClient
(adapted to the single-arg `generate_fn` signature via a lambda), same as
the orchestrator's own hybrid-retrieval tests do for embed_fn.
"""

import json

from tests.fakes import AlwaysFailingLLMClient, FakeLLMClient
from verityai.evidence.classify import EvidenceClassifier
from verityai.evidence.models import EvidenceRecord


def make_record(**overrides) -> EvidenceRecord:
    defaults = dict(
        id="arxiv_abc123",
        source="arxiv",
        source_url="https://arxiv.org/abs/1234.5678",
        retrieved_at="2026-07-15T00:00:00Z",
        retrieval_method="requests",
        content={"title": "On Calibration of Modern Neural Networks", "abstract": "..."},
        content_hash="a" * 64,
        feeds_topics=["T1"],
    )
    defaults.update(overrides)
    return EvidenceRecord(**defaults)


_GOOD_RESPONSE = json.dumps(
    {
        "relevance": {"T1": 0.9, "T2": 0.1, "T3": 0.0, "T4": 0.0, "T5": 0.2, "T6": 0.0},
        "extracted_claims": ["Calibration error is measurable via reliability diagrams."],
        "confidence": 0.8,
    }
)


class TestClassifyDegradedWithoutGenerateFn:
    def test_no_generate_fn_configured(self):
        classifier = EvidenceClassifier(generate_fn=None)
        result = classifier.classify(make_record())

        assert result.classified_by == "none"
        assert result.degraded_reason == "no generate_fn configured"
        assert result.classification_reviewed is False

    def test_default_constructor_has_no_generate_fn(self):
        classifier = EvidenceClassifier()
        result = classifier.classify(make_record())

        assert result.degraded_reason == "no generate_fn configured"


class TestClassifySuccess:
    def test_parses_relevance_claims_and_confidence(self):
        fake = FakeLLMClient([_GOOD_RESPONSE])
        classifier = EvidenceClassifier(
            generate_fn=lambda p: fake.generate(p), model_name="llama3.2"
        )

        result = classifier.classify(make_record())

        assert result.classified_by == "llama3.2"
        assert result.degraded_reason is None
        assert result.relevance["T1"] == 0.9
        assert result.relevance["T2"] == 0.1
        assert len(result.extracted_claims) == 1
        assert result.confidence == 0.8
        assert result.classification_reviewed is False

    def test_response_wrapped_in_markdown_fence_still_parses(self):
        fenced = f"```json\n{_GOOD_RESPONSE}\n```"
        fake = FakeLLMClient([fenced])
        classifier = EvidenceClassifier(
            generate_fn=lambda p: fake.generate(p), model_name="llama3.2"
        )

        result = classifier.classify(make_record())

        assert result.degraded_reason is None
        assert result.relevance["T1"] == 0.9

    def test_out_of_range_relevance_is_clamped(self):
        response = json.dumps(
            {
                "relevance": {"T1": 1.5, "T2": -0.3, "T3": 0.0, "T4": 0.0, "T5": 0.0, "T6": 0.0},
                "extracted_claims": [],
                "confidence": 2.0,
            }
        )
        fake = FakeLLMClient([response])
        classifier = EvidenceClassifier(generate_fn=lambda p: fake.generate(p))

        result = classifier.classify(make_record())

        assert result.relevance["T1"] == 1.0
        assert result.relevance["T2"] == 0.0
        assert result.confidence == 1.0

    def test_extracted_claims_truncated_to_three(self):
        response = json.dumps(
            {
                "relevance": {t: 0.0 for t in ("T1", "T2", "T3", "T4", "T5", "T6")},
                "extracted_claims": ["a", "b", "c", "d", "e"],
                "confidence": 0.5,
            }
        )
        fake = FakeLLMClient([response])
        classifier = EvidenceClassifier(generate_fn=lambda p: fake.generate(p))

        result = classifier.classify(make_record())

        assert len(result.extracted_claims) == 3

    def test_prompt_includes_record_content(self):
        fake = FakeLLMClient([_GOOD_RESPONSE])
        classifier = EvidenceClassifier(generate_fn=lambda p: fake.generate(p))

        classifier.classify(make_record())

        assert "On Calibration of Modern Neural Networks" in fake.prompts_seen[0]


class TestClassifyDegradationPaths:
    def test_generate_fn_raises_is_recorded_not_propagated(self):
        classifier = EvidenceClassifier(
            generate_fn=lambda p: AlwaysFailingLLMClient().generate(p), model_name="llama3.2"
        )

        result = classifier.classify(make_record())

        assert result.classified_by == "llama3.2"
        assert result.degraded_reason is not None
        assert "generate_fn raised" in result.degraded_reason

    def test_unparseable_json_response_is_recorded(self):
        fake = FakeLLMClient(["this is not json at all"])
        classifier = EvidenceClassifier(generate_fn=lambda p: fake.generate(p))

        result = classifier.classify(make_record())

        assert result.degraded_reason == "unparseable_llm_response"
        assert result.extracted_claims == ["this is not json at all"]

    def test_json_missing_relevance_key_defaults_to_all_zero(self):
        # Lenient by design: a response that just omits `relevance` (rather
        # than sending a malformed one) isn't worth a hard failure -- it's
        # treated as "rated nothing", not "couldn't be parsed at all".
        fake = FakeLLMClient([json.dumps({"extracted_claims": [], "confidence": 0.5})])
        classifier = EvidenceClassifier(generate_fn=lambda p: fake.generate(p))

        result = classifier.classify(make_record())

        assert result.degraded_reason is None
        assert result.relevance == {t: 0.0 for t in ("T1", "T2", "T3", "T4", "T5", "T6")}

    def test_json_array_instead_of_object_is_recorded(self):
        fake = FakeLLMClient([json.dumps(["not", "an", "object"])])
        classifier = EvidenceClassifier(generate_fn=lambda p: fake.generate(p))

        result = classifier.classify(make_record())

        assert result.degraded_reason == "unparseable_llm_response"
