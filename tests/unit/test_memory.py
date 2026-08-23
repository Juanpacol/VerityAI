"""Tests for the persistent memory store.

Two properties get the most attention here, because both are the kind that
look fine until the day they matter:

- **Append-only really is append-only.** Superseding a decision must not erase
  the original, or the harness loses the ability to notice an agent
  re-proposing something already rejected.
- **A corrupt line does not destroy the file.** These files live in a user's
  repository and will be hand-edited and merge-conflicted.
"""

import pytest

from verityai.core.models import (
    Constraint,
    Decision,
    DecisionStatus,
    Discovery,
    Evidence,
    Fact,
    Failure,
    Task,
)
from verityai.memory.store import MemoryStore


class TestInitAndDiscovery:
    def test_init_creates_the_expected_layout(self, tmp_path):
        store = MemoryStore.init(tmp_path)

        assert (store.root / "state").is_dir()
        assert (store.root / "memory").is_dir()
        assert (store.root / "snapshots").is_dir()
        assert (store.root / "config.toml").exists()

    def test_init_is_idempotent(self, tmp_path):
        MemoryStore.init(tmp_path)
        (MemoryStore.init(tmp_path).root / "config.toml").write_text("custom = true")
        store = MemoryStore.init(tmp_path)

        assert "custom = true" in (store.root / "config.toml").read_text()

    def test_discover_walks_up_from_a_subdirectory(self, tmp_path):
        MemoryStore.init(tmp_path)
        nested = tmp_path / "src" / "deep" / "nested"
        nested.mkdir(parents=True)

        found = MemoryStore.discover(nested)

        assert found is not None
        assert found.root == tmp_path / ".verity"

    def test_discover_returns_none_when_absent(self, tmp_path):
        assert MemoryStore.discover(tmp_path) is None


class TestAppendOnly:
    def test_superseding_preserves_the_original(self, store):
        original = store.append(Decision(statement="use redis"))
        store.supersede(original.id, Decision(statement="use postgres"))

        everything = store.read(Decision)

        assert len(everything) == 2
        assert any(d.statement == "use redis" for d in everything)

    def test_rejected_decisions_are_retained_but_not_active(self, store):
        store.append(Decision(statement="use mongo", status=DecisionStatus.REJECTED))
        store.append(Decision(statement="use postgres"))

        assert len(store.decisions()) == 1
        assert len(store.decisions(include_inactive=True)) == 2

    def test_supersedes_link_is_recorded(self, store):
        original = store.append(Decision(statement="first"))
        replacement = store.supersede(original.id, Decision(statement="second"))

        stored = [d for d in store.read(Decision) if d.id == replacement.id][0]
        assert stored.supersedes == original.id

    def test_superseded_decision_is_excluded_from_active(self, store):
        # ADR-0034 regression: the original record's status stayed ACTIVE
        # forever (append-only, never rewritten), and decisions() did a flat
        # status filter with no supersedes-chain walking, so a superseded
        # decision kept showing up as active alongside its replacement.
        original = store.append(Decision(statement="use redis for caching"))
        store.supersede(original.id, Decision(statement="use postgres for caching"))

        active = store.decisions()

        assert len(active) == 1
        assert active[0].statement == "use postgres for caching"

        inactive = store.decisions(include_inactive=True)
        reclassified = [d for d in inactive if d.id == original.id][0]
        assert reclassified.status is DecisionStatus.SUPERSEDED


class TestRoundTrip:
    def test_every_record_type_round_trips(self, store):
        store.append(Decision(statement="d", rationale="because"))
        store.append(Constraint(statement="c", hard=False))
        store.append(Discovery(statement="disc"))
        store.append(Failure(attempted="f", error="boom"))
        store.append(Fact(statement="fact", confidence=0.9))

        assert store.decisions()[0].rationale == "because"
        assert store.constraints()[0].hard is False
        assert store.discoveries()[0].statement == "disc"
        assert store.failures()[0].error == "boom"
        assert store.facts()[0].confidence == 0.9

    def test_evidence_round_trips(self, store):
        store.append(
            Fact(
                statement="timeout is 15 minutes",
                evidence=[Evidence(kind="file", locator="auth/config.py:12")],
            )
        )

        fact = store.facts()[0]
        assert fact.is_grounded
        assert fact.evidence[0].locator == "auth/config.py:12"

    def test_task_round_trips(self, store):
        store.set_task(Task(title="add retries", next_action="write the test"))

        task = store.task()
        assert task.title == "add retries"
        assert task.next_action == "write the test"

    def test_task_is_none_before_being_set(self, store):
        assert store.task() is None

    def test_reading_an_empty_store_returns_empty_lists(self, store):
        assert store.decisions() == []
        assert store.facts() == []


class TestGrounding:
    def test_a_fact_without_evidence_is_not_grounded(self):
        assert Fact(statement="redis is configured").is_grounded is False

    def test_a_fact_with_evidence_is_grounded(self):
        fact = Fact(
            statement="redis is configured",
            evidence=[Evidence(kind="file", locator="docker-compose.yml")],
        )
        assert fact.is_grounded is True

    def test_high_confidence_does_not_imply_grounding(self):
        """Confidence is what the recorder believed, not proof of anything."""
        assert Fact(statement="x", confidence=1.0).is_grounded is False


class TestCorruption:
    def test_a_malformed_line_does_not_destroy_the_file(self, store):
        store.append(Decision(statement="good one"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ this is not valid json\n")
        store.append(Decision(statement="another good one"))

        decisions = store.decisions()

        assert len(decisions) == 2
        assert {d.statement for d in decisions} == {"good one", "another good one"}

    def test_blank_lines_are_ignored(self, store):
        store.append(Decision(statement="only one"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("\n\n   \n")

        assert len(store.decisions()) == 1

    def test_an_unreadable_task_file_returns_none_rather_than_raising(self, store):
        (store.root / "state" / "task.json").write_text("not json at all")

        assert store.task() is None

    def test_set_task_leaves_the_previous_task_intact_if_the_write_fails(self, store, monkeypatch):
        """ADR-0038: set_task() writes atomically. If the write step raises
        partway through, the previous task.json must still be readable, not
        truncated -- proof of the same guarantee a real kill mid-write needs."""
        store.set_task(Task(title="original"))

        import verityai.core.atomic as atomic_module

        monkeypatch.setattr(
            atomic_module.os, "fdopen", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError):
            store.set_task(Task(title="replacement"))

        assert store.task().title == "original"


class TestParseReport:
    """ADR-0037: read_report()/integrity() discharge invariants 5 and 6,
    which the plain accessors above intentionally left unaddressed."""

    def test_read_report_sums_to_whole(self, store):
        store.append(Decision(statement="a"))
        store.append(Decision(statement="b"))
        store.append(Decision(statement="c"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ not valid json\n")

        records, report = store.read_report(Decision)

        assert len(records) == 3
        assert report.lines_seen == 4
        assert report.parsed == 3
        assert len(report.skipped) == 1
        assert report.sums_to_whole

    def test_blank_lines_are_not_counted_as_lines_seen(self, store):
        store.append(Decision(statement="only one"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("\n\n   \n")

        _, report = store.read_report(Decision)

        assert report.lines_seen == report.parsed == 1
        assert report.clean

    def test_truncated_final_line_names_its_line_number(self, store):
        store.append(Decision(statement="a"))
        store.append(Decision(statement="b"))
        store.append(Decision(statement="c"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write('{"statement": "truncated mid-wr\n')

        _, report = store.read_report(Decision)

        assert 4 in report.skipped
        assert "invalid JSON" in report.skipped[4]

    def test_valid_json_wrong_schema_is_skipped_with_a_schema_reason(self, store):
        path = store.root / "state" / "decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write('{"nonsense": 1}\n')

        records, report = store.read_report(Decision)

        assert records == []
        assert "Decision" in report.skipped[1]

    def test_valid_json_non_object_line_is_skipped(self, store):
        path = store.root / "state" / "decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write("[1, 2, 3]\n")

        records, report = store.read_report(Decision)

        assert records == []
        assert report.skipped[1] == "not a JSON object"

    def test_read_returns_the_same_records_as_read_report(self, store):
        store.append(Decision(statement="good"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ bad\n")

        assert store.read(Decision) == store.read_report(Decision)[0]

    def test_summary_reports_corrupt_lines(self, store):
        store.append(Decision(statement="a"))
        store.append(Decision(statement="b"))
        store.append(Decision(statement="c"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ bad\n")

        summary = store.summary()

        assert summary["corrupt_lines"] == 1
        assert summary["corrupt_files"] == 1
        assert summary["decisions"] == 3

    def test_summary_is_clean_on_a_healthy_store(self, store):
        store.append(Decision(statement="a"))

        summary = store.summary()

        assert summary["corrupt_lines"] == 0
        assert summary["corrupt_files"] == 0

    def test_integrity_covers_every_backing_file_and_task_json(self, store):
        sources = {r.source for r in store.integrity()}

        assert sources == {
            "state/decisions.jsonl",
            "state/constraints.jsonl",
            "state/discoveries.jsonl",
            "state/failures.jsonl",
            "memory/facts.jsonl",
            "memory/surfacings.jsonl",
            "state/task.json",
        }

    def test_task_absent_and_task_corrupt_are_distinguishable(self, store):
        absent, absent_report = store.task_report()
        assert absent is None
        assert absent_report.exists is False

        (store.root / "state" / "task.json").write_text("not json at all")
        corrupt, corrupt_report = store.task_report()
        assert corrupt is None
        assert corrupt_report.exists is True
        assert not corrupt_report.clean

    def test_parse_report_note_is_empty_when_clean(self, store):
        store.append(Decision(statement="a"))

        _, report = store.read_report(Decision)

        assert report.note == ""


class TestSummary:
    def test_summary_distinguishes_active_from_total(self, store):
        store.append(Decision(statement="active one"))
        store.append(Decision(statement="dead one", status=DecisionStatus.REJECTED))
        store.append(Failure(attempted="fixed", resolved=True))
        store.append(Failure(attempted="still broken"))

        summary = store.summary()

        assert summary["decisions"] == 1
        assert summary["decisions_total"] == 2
        assert summary["failures"] == 1
        assert summary["failures_total"] == 2
