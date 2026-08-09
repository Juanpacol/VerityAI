"""Tests for the in-memory live-run registry."""

import threading
from uuid import uuid4

import pytest

from verityai.agent import events as event_types
from verityai.agent.events import StageEvent
from verityai.api.live_runs import (
    FINISHED_TTL_SECONDS,
    HARD_TTL_SECONDS,
    MAX_EVENTS_PER_RUN,
    LiveRunRegistry,
    get_live_run_registry,
    reset_live_run_state,
)


@pytest.fixture
def registry():
    return LiveRunRegistry()


def _event(run_id, event_type=event_types.RUN_STARTED):
    return StageEvent(run_id=run_id, type=event_type)


class TestCreate:
    def test_create_assigns_the_condition_and_an_id(self, registry):
        run = registry.create("B")
        assert run.condition == "B"
        assert registry.get(run.run_id) is run

    def test_create_accepts_a_caller_supplied_id(self, registry):
        """The API allocates the id before the run starts so it can hand back a stream URL."""
        run_id = uuid4()
        assert registry.create("C", run_id=run_id).run_id == run_id

    def test_invalid_condition_is_rejected(self, registry):
        with pytest.raises(ValueError):
            registry.create("Z")

    def test_get_returns_none_for_an_unknown_run(self, registry):
        assert registry.get(uuid4()) is None


class TestSequencing:
    def test_sequences_start_at_one_and_are_monotonic(self, registry):
        run = registry.create("C")
        for _ in range(5):
            registry.append(run.run_id, _event(run.run_id))
        assert [e.sequence for e in run.events] == [1, 2, 3, 4, 5]

    def test_events_since_slices_on_sequence(self, registry):
        run = registry.create("C")
        for _ in range(5):
            registry.append(run.run_id, _event(run.run_id))
        assert [e.sequence for e in registry.events_since(run.run_id, 3)] == [4, 5]
        assert len(registry.events_since(run.run_id, 0)) == 5
        assert registry.events_since(run.run_id, 5) == []

    def test_append_to_an_unknown_run_is_a_no_op(self, registry):
        registry.append(uuid4(), _event(uuid4()))  # must not raise

    def test_append_sets_the_tick_so_the_sse_generator_wakes(self, registry):
        run = registry.create("C")
        run.tick.clear()
        registry.append(run.run_id, _event(run.run_id))
        assert run.tick.is_set()

    def test_buffer_overflow_drops_oldest_without_renumbering(self, registry):
        """A gap in `events_since` must be visible, not papered over by renumbering."""
        run = registry.create("C")
        for _ in range(MAX_EVENTS_PER_RUN + 10):
            registry.append(run.run_id, _event(run.run_id))

        assert len(run.events) == MAX_EVENTS_PER_RUN
        remaining = registry.events_since(run.run_id, 0)
        assert remaining[0].sequence == 11
        assert remaining[-1].sequence == MAX_EVENTS_PER_RUN + 10

    def test_concurrent_appends_produce_unique_sequences(self, registry):
        run = registry.create("C")
        per_thread = 50

        def worker():
            for _ in range(per_thread):
                registry.append(run.run_id, _event(run.run_id))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        sequences = [e.sequence for e in run.events]
        assert len(sequences) == 8 * per_thread  # 400 total, under the cap
        assert len(set(sequences)) == len(sequences)


class TestLifecycle:
    def test_active_count_tracks_unfinished_runs(self, registry):
        first = registry.create("A")
        registry.create("B")
        assert registry.active_count() == 2
        registry.finish(first.run_id)
        assert registry.active_count() == 1

    def test_finish_records_the_error_and_wakes_the_stream(self, registry):
        run = registry.create("A")
        run.tick.clear()
        registry.finish(run.run_id, error="ollama unreachable")
        assert run.finished is True
        assert run.error == "ollama unreachable"
        assert run.tick.is_set()

    def test_finish_on_an_unknown_run_is_a_no_op(self, registry):
        registry.finish(uuid4())  # must not raise


class TestSweep:
    def test_sweep_keeps_active_and_recently_finished_runs(self, registry):
        active = registry.create("A")
        recent = registry.create("B")
        registry.finish(recent.run_id)

        assert registry.sweep() == 0
        assert registry.get(active.run_id) is not None
        assert registry.get(recent.run_id) is not None

    def test_sweep_evicts_runs_finished_past_the_ttl(self, registry):
        run = registry.create("A")
        registry.finish(run.run_id)
        later = run.finished_at + FINISHED_TTL_SECONDS + 1

        assert registry.sweep(now=later) == 1
        assert registry.get(run.run_id) is None

    def test_sweep_evicts_wedged_runs_that_never_finished(self, registry):
        """Backstop for a worker thread that died without calling finish()."""
        run = registry.create("A")
        later = run.created_at + HARD_TTL_SECONDS + 1

        assert registry.sweep(now=later) == 1
        assert registry.get(run.run_id) is None


def test_reset_live_run_state_swaps_in_a_clean_registry():
    get_live_run_registry().create("C")
    assert get_live_run_registry().active_count() == 1
    reset_live_run_state()
    assert get_live_run_registry().active_count() == 0
