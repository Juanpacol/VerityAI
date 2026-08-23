"""Tests for the persistent memory store.

Two properties get the most attention here, because both are the kind that
look fine until the day they matter:

- **Append-only really is append-only.** Superseding a decision must not erase
  the original, or the harness loses the ability to notice an agent
  re-proposing something already rejected.
- **A corrupt line does not destroy the file.** These files live in a user's
  repository and will be hand-edited and merge-conflicted.
"""

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
