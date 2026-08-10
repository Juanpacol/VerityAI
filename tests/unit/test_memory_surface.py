"""Tests for `memory/surface.py::candidates_for`.

This is candidate sourcing only -- ranking, budgeting, and pipeline
invariants are `context/adaptive.py`'s job and are tested there. What
matters here: every candidate is `ItemKind.MEMORY` (so `classify.py`
protects it unconditionally), record provenance survives into
`metadata['record_id']`, and resolved decisions/unresolved failures follow
the same active/inactive filtering `MemoryStore` already applies elsewhere.
"""

from verityai.core.models import Constraint, Decision, Discovery, Failure, ItemKind
from verityai.memory.surface import candidates_for

from ..conftest import FixedCounter


class TestCandidatesFor:
    def test_every_candidate_is_kind_memory(self, store):
        store.append(Decision(statement="use a token bucket"))

        candidates = candidates_for(store, task="rate limiting", counter=FixedCounter())

        assert all(c.kind is ItemKind.MEMORY for c in candidates)

    def test_covers_all_four_active_record_types(self, store):
        store.append(Decision(statement="use a token bucket"))
        store.append(Constraint(statement="must not add Redis"))
        store.append(Discovery(statement="middleware runs after auth"))
        store.append(Failure(attempted="a fixed window counter"))

        candidates = candidates_for(store, task="rate limiting", counter=FixedCounter())

        assert len(candidates) == 4

    def test_resolved_failures_are_excluded(self, store):
        store.append(Failure(attempted="already fixed", resolved=True))

        candidates = candidates_for(store, task="anything", counter=FixedCounter())

        assert candidates == []

    def test_record_id_is_preserved_in_metadata(self, store):
        decision = store.append(Decision(statement="use a token bucket"))

        candidates = candidates_for(store, task="rate limiting", counter=FixedCounter())

        assert candidates[0].metadata["record_id"] == str(decision.id)

    def test_task_is_carried_in_metadata_for_a_later_ranking_pass(self, store):
        store.append(Decision(statement="use a token bucket"))

        candidates = candidates_for(store, task="rate limiting", counter=FixedCounter())

        assert candidates[0].metadata["task"] == "rate limiting"

    def test_identical_content_hashes_identically(self, store):
        store.append(Decision(statement="use a token bucket"))

        first = candidates_for(store, task="x", counter=FixedCounter())
        second = candidates_for(store, task="x", counter=FixedCounter())

        assert first[0].content_hash == second[0].content_hash

    def test_empty_store_produces_no_candidates(self, store):
        assert candidates_for(store, task="anything", counter=FixedCounter()) == []
