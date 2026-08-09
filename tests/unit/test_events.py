"""Tests for the stage-event transport model."""

import json
from uuid import uuid4

import pytest

from verityai.agent import events
from verityai.agent.events import EVENT_TYPES, StageEvent, null_emitter


@pytest.mark.parametrize("event_type", sorted(EVENT_TYPES))
def test_every_event_type_round_trips_as_json(event_type):
    """Every event must survive model_dump_json() -- the SSE layer depends on it.

    This is the guard for the failure mode described in events.py: a
    non-serializable value in `data` would raise inside an
    exception-swallowing emitter and make the event silently disappear.
    """
    event = StageEvent(run_id=uuid4(), type=event_type, data={"k": "v"})
    payload = json.loads(event.model_dump_json())
    assert payload["type"] == event_type
    assert payload["data"] == {"k": "v"}


def test_sequence_defaults_to_zero_and_html_to_none():
    """The registry stamps sequence; the API layer attaches html. Neither is the emitter's job."""
    event = StageEvent(run_id=uuid4(), type=events.RUN_STARTED)
    assert event.sequence == 0
    assert event.html is None
    assert event.message == ""


def test_null_emitter_discards_and_returns_none():
    assert null_emitter(StageEvent(run_id=uuid4(), type=events.RUN_STARTED)) is None


def test_event_type_constants_are_all_in_the_frozenset():
    """Catches a constant added without registering it in EVENT_TYPES."""
    declared = {
        value
        for name, value in vars(events).items()
        if name.isupper() and isinstance(value, str) and not name.startswith("_")
    }
    assert declared == set(EVENT_TYPES)
