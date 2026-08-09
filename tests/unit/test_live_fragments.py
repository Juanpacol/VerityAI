"""Tests for live-view HTML fragments and T5 condition masking."""

from uuid import uuid4

import pytest

from verityai.agent import events
from verityai.agent.events import StageEvent
from verityai.api.live_fragments import (
    CONDITION_EVERYTHING,
    CONDITION_SCORE_ONLY,
    CONDITION_Z3_ONLY,
    apply_condition,
    build_html,
)
from verityai.ontology.models import Counterexample, VerificationResult, VerificationStatus


def _event(event_type, **data):
    return StageEvent(run_id=uuid4(), type=event_type, message="narrated", data=data)


@pytest.fixture
def ctx():
    return {}


class TestBuildHtml:
    def test_events_without_a_panel_render_nothing(self, ctx):
        for event_type in (events.RUN_STARTED, events.ATTEMPT_STARTED, events.RUN_COMPLETED):
            assert build_html(_event(event_type), ctx) is None

    def test_generation_completed_stashes_the_code_for_later_events(self, ctx):
        """verification_completed needs the code, which only arrives on this earlier event."""
        assert build_html(_event(events.GENERATION_COMPLETED, code="def f(): pass"), ctx) is None
        assert ctx["code"] == "def f(): pass"

    def test_hybrid_retrieval_renders_the_provenance_table(self, ctx):
        html = build_html(
            _event(
                events.RETRIEVAL_COMPLETED,
                strategy="hybrid",
                mode="hybrid",
                rules=[
                    {
                        "name": "no_sql_concat",
                        "description": "d",
                        "provenance": {"method": "hybrid", "fused_score": 0.42},
                    }
                ],
            ),
            ctx,
        )
        assert "no_sql_concat" in html
        assert "0.420" in html

    def test_degraded_retrieval_says_so_in_the_panel(self, ctx):
        html = build_html(
            _event(
                events.RETRIEVAL_COMPLETED,
                strategy="hybrid",
                mode="lexical",
                degraded_reason="no embedding function",
                rules=[{"name": "r", "description": "d", "provenance": {}}],
            ),
            ctx,
        )
        assert "Degraded to lexical-only" in html
        assert "no embedding function" in html

    def test_failed_verification_renders_the_counterexample_values(self, ctx):
        result = VerificationResult(
            code_id="",
            status=VerificationStatus.FAIL,
            confidence=0.0,
            violations=[
                Counterexample(
                    rule_id="no_negative",
                    description="index goes negative",
                    input_values={"x": -1},
                )
            ],
        )
        ctx["code"] = "def f(x):\n    return x\n"
        html = build_html(
            _event(
                events.VERIFICATION_COMPLETED,
                status="fail",
                verification_result=result.model_dump(mode="json"),
            ),
            ctx,
        )
        assert "index goes negative" in html
        assert "-1" in html

    def test_malformed_code_degrades_gracefully_instead_of_raising(self, ctx):
        """SymbolicDebugger can't parse everything; the panel must still render."""
        result = VerificationResult(
            code_id="", status=VerificationStatus.FAIL, confidence=0.0, violations=[]
        )
        ctx["code"] = "this is (((not python"
        html = build_html(
            _event(
                events.VERIFICATION_COMPLETED,
                status="fail",
                verification_result=result.model_dump(mode="json"),
            ),
            ctx,
        )
        assert isinstance(html, str)

    def test_verification_without_a_result_payload_renders_nothing(self, ctx):
        assert build_html(_event(events.VERIFICATION_COMPLETED, status="fail"), ctx) is None

    def test_confidence_renders_the_weighted_breakdown_bar(self, ctx):
        html = build_html(
            _event(
                events.CONFIDENCE_COMPUTED,
                total=0.55,
                components={
                    "verification": 1.0,
                    "pattern_similarity": 0.2,
                    "complexity": 0.0,
                    "test_coverage": 0.0,
                },
                weights={
                    "verification": 0.5,
                    "pattern_similarity": 0.25,
                    "complexity": 0.15,
                    "test_coverage": 0.1,
                },
            ),
            ctx,
        )
        assert "Total: 55.0%" in html
        assert "Verification" in html
        assert "Pattern similarity" in html


class TestConditionMasking:
    """The T5 manipulation. Masking must remove html, data AND narration."""

    def _masked(self, condition, event_type, **data):
        event = _event(event_type, **data)
        event.html = "<div>secret panel</div>"
        apply_condition(event, condition)
        return event

    def test_condition_c_shows_everything_untouched(self):
        event = self._masked(CONDITION_EVERYTHING, events.VERIFICATION_COMPLETED, status="fail")
        assert event.html == "<div>secret panel</div>"
        assert event.message == "narrated"
        assert event.data["status"] == "fail"

    @pytest.mark.parametrize(
        "event_type", [events.VERIFICATION_COMPLETED, events.RETRIEVAL_COMPLETED]
    )
    def test_condition_a_hides_z3_and_retrieval(self, event_type):
        event = self._masked(CONDITION_SCORE_ONLY, event_type, status="fail", secret=123)
        assert event.html is None
        assert event.data == {"suppressed": True}
        assert "secret" not in str(event.data)

    def test_condition_a_still_shows_confidence(self):
        event = self._masked(CONDITION_SCORE_ONLY, events.CONFIDENCE_COMPUTED, total=0.9)
        assert event.html == "<div>secret panel</div>"
        assert event.data["total"] == 0.9

    @pytest.mark.parametrize(
        "event_type", [events.CONFIDENCE_COMPUTED, events.RETRIEVAL_COMPLETED]
    )
    def test_condition_b_hides_confidence_and_retrieval(self, event_type):
        event = self._masked(CONDITION_Z3_ONLY, event_type, total=0.9)
        assert event.html is None
        assert event.data == {"suppressed": True}

    def test_condition_b_still_shows_the_z3_panel(self):
        event = self._masked(CONDITION_Z3_ONLY, events.VERIFICATION_COMPLETED, status="fail")
        assert event.html == "<div>secret panel</div>"
        assert event.data["status"] == "fail"

    def test_masking_replaces_narration_so_the_message_does_not_leak_the_panel(self):
        """A message like 'Z3 found x=-1' would leak the hidden panel's whole point."""
        event = _event(events.VERIFICATION_COMPLETED, status="fail")
        event.message = "Z3 found a case where this fails: x=-1."
        apply_condition(event, CONDITION_SCORE_ONLY)
        assert "x=-1" not in event.message
        assert event.message == "Verification step completed."

    def test_masking_leaves_unrelated_events_alone(self):
        event = self._masked(CONDITION_SCORE_ONLY, events.GENERATION_COMPLETED, code="x")
        assert event.html == "<div>secret panel</div>"
        assert event.data["code"] == "x"
