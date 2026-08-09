"""Round-trip tests for T5 study response persistence."""

from datetime import datetime
from uuid import uuid4

import pytest

from verityai.study.models import (
    KeptElement,
    MergeIntent,
    StudyResponse,
)
from verityai.study.store import CSV_COLUMNS, StudyResponseStore, to_csv


@pytest.fixture
def store(db_session):
    return StudyResponseStore(db_session)


def _response(**overrides):
    defaults = dict(
        run_id=uuid4(),
        condition="C",
        trusts_code=True,
        trust_reason="the counterexample panel convinced me",
        merge_intent=MergeIntent.FULL_REVIEW,
        kept_element=KeptElement.Z3,
        experience_with_ai_tools="daily copilot user",
    )
    defaults.update(overrides)
    return StudyResponse(**defaults)


class TestPersistence:
    def test_round_trips_every_field(self, store):
        original = _response(
            reduced_trust_note="the score felt arbitrary",
            comparison_to_current_tools="more than copilot",
        )
        store.save(original)
        loaded = store.get(original.id)

        assert loaded == original

    def test_save_upserts_by_id(self, store):
        response = _response()
        store.save(response)
        response.trust_reason = "changed my mind"
        store.save(response)

        assert len(store.list_all()) == 1
        assert store.get(response.id).trust_reason == "changed my mind"

    def test_get_returns_none_for_unknown_id(self, store):
        assert store.get(uuid4()) is None

    def test_list_all_is_ordered_oldest_first(self, store):
        older = _response(created_at=datetime(2026, 1, 1))
        newer = _response(created_at=datetime(2026, 6, 1))
        store.save(newer)
        store.save(older)

        assert [r.id for r in store.list_all()] == [older.id, newer.id]

    def test_optional_free_text_fields_may_be_absent(self, store):
        response = _response(kept_element=None, experience_with_ai_tools=None)
        store.save(response)
        loaded = store.get(response.id)
        assert loaded.kept_element is None
        assert loaded.experience_with_ai_tools is None


class TestAttitudinalBehaviouralSeparation:
    """The methodological point the model exists to preserve."""

    def test_trust_and_merge_intent_are_stored_independently(self, store):
        # "I trust it" + "but I'd still demand a full review" is the exact
        # combination a single boolean would erase.
        response = _response(trusts_code=True, merge_intent=MergeIntent.FULL_REVIEW)
        store.save(response)
        loaded = store.get(response.id)

        assert loaded.trusts_code is True
        assert loaded.merge_intent == MergeIntent.FULL_REVIEW

    def test_merge_intent_is_required(self):
        with pytest.raises(Exception):
            StudyResponse(run_id=uuid4(), condition="C", trusts_code=True)


class TestCsvExport:
    def test_header_matches_the_declared_column_order(self, store):
        assert to_csv([]).strip().split(",") == CSV_COLUMNS

    def test_a_response_becomes_one_row(self, store):
        response = _response()
        csv_text = to_csv([response])
        lines = csv_text.strip().splitlines()

        assert len(lines) == 2
        assert str(response.run_id) in lines[1]
        assert "full_review" in lines[1]
        assert "z3" in lines[1]

    def test_free_text_with_commas_is_quoted_not_split(self, store):
        response = _response(trust_reason="yes, mostly, but not fully")
        rows = to_csv([response]).strip().splitlines()
        assert '"yes, mostly, but not fully"' in rows[1]
