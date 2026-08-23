"""Tests for snapshots.

Round-trip fidelity is the headline property: a snapshot that silently loses a
constraint is worse than no snapshot, because it invites trust it has not
earned. The second theme is that restoring is additive — it must never delete
the records written after the snapshot, since those are the evidence of
whatever went wrong.
"""

import pytest

from verityai.core.models import Constraint, Decision, Discovery, Fact, Failure, Task
from verityai.memory.snapshot import SnapshotManager
from verityai.memory.store import CorruptStateError


def populate(store):
    store.set_task(Task(title="the task", next_action="do the thing"))
    store.append(Decision(statement="a decision", rationale="a reason"))
    store.append(Constraint(statement="a constraint"))
    store.append(Discovery(statement="a discovery"))
    store.append(Failure(attempted="a failure", error="an error"))
    store.append(Fact(statement="a fact", confidence=0.8))
    return store


class TestRoundTrip:
    def test_every_record_type_survives_a_round_trip(self, store):
        manager = SnapshotManager(populate(store))
        created = manager.create(label="checkpoint")

        restored = manager.get(created.number)

        assert restored.task.title == "the task"
        assert restored.decisions[0].statement == "a decision"
        assert restored.constraints[0].statement == "a constraint"
        assert restored.discoveries[0].statement == "a discovery"
        assert restored.failures[0].attempted == "a failure"
        assert restored.facts[0].confidence == 0.8

    def test_record_ids_are_preserved(self, store):
        decision = store.append(Decision(statement="identity matters"))
        manager = SnapshotManager(store)

        restored = manager.get(manager.create().number)

        assert restored.decisions[0].id == decision.id

    def test_the_label_is_preserved(self, store):
        manager = SnapshotManager(store)
        number = manager.create(label="before the refactor").number

        assert manager.get(number).label == "before the refactor"


class TestNumbering:
    def test_numbering_starts_at_one(self, store):
        assert SnapshotManager(store).create().number == 1

    def test_numbering_increments(self, store):
        manager = SnapshotManager(store)

        assert [manager.create().number for _ in range(3)] == [1, 2, 3]

    def test_numbering_survives_a_manual_deletion(self, store):
        """The directory is the source of truth, not a counter file."""
        manager = SnapshotManager(store)
        manager.create()
        second = manager.create()
        (manager.snapshots_dir / f"{second.number:03d}" / "snapshot.json").unlink()
        (manager.snapshots_dir / f"{second.number:03d}").rmdir()

        assert manager.create().number == 2

    def test_directories_are_zero_padded(self, store):
        manager = SnapshotManager(store)
        manager.create()

        assert (manager.snapshots_dir / "001").is_dir()

    def test_path_for_matches_where_create_actually_writes(self, store):
        manager = SnapshotManager(store)
        snap = manager.create()

        assert manager.path_for(snap.number).exists()
        assert manager.path_for(snap.number) == manager.snapshots_dir / "001" / "snapshot.json"


class TestRestore:
    def test_restore_is_additive_not_destructive(self, store):
        manager = SnapshotManager(store)
        store.append(Decision(statement="before the snapshot"))
        number = manager.create().number
        store.append(Decision(statement="after the snapshot"))

        manager.restore(number)

        statements = {d.statement for d in store.decisions()}
        assert "before the snapshot" in statements
        assert "after the snapshot" in statements

    def test_restore_is_idempotent(self, store):
        manager = SnapshotManager(populate(store))
        number = manager.create().number

        manager.restore(number)
        manager.restore(number)

        assert len(store.decisions()) == 1

    def test_restoring_a_missing_snapshot_returns_none(self, store):
        assert SnapshotManager(store).restore(99) is None

    def test_restore_reinstates_the_task(self, store):
        manager = SnapshotManager(store)
        store.set_task(Task(title="original"))
        number = manager.create().number
        store.set_task(Task(title="changed"))

        manager.restore(number)

        assert store.task().title == "original"

    def test_restore_never_touches_the_working_tree(self, store, tmp_path):
        """Code rollback is git's job. This is asserted structurally: the
        snapshot module must not contain any file-writing outside .verity/."""
        manager = SnapshotManager(store)
        marker = tmp_path / "source_file.py"
        marker.write_text("original source")
        number = manager.create().number
        marker.write_text("modified source")

        manager.restore(number)

        assert marker.read_text() == "modified source"


class TestListing:
    def test_snapshots_are_listed_oldest_first(self, store):
        manager = SnapshotManager(store)
        for label in ("first", "second", "third"):
            manager.create(label=label)

        assert [s.label for s in manager.list()] == ["first", "second", "third"]

    def test_listing_an_empty_store_returns_nothing(self, store):
        assert SnapshotManager(store).list() == []

    def test_a_corrupt_snapshot_is_skipped_not_fatal(self, store):
        manager = SnapshotManager(store)
        manager.create(label="good")
        bad = manager.create(label="bad")
        (manager.snapshots_dir / f"{bad.number:03d}" / "snapshot.json").write_text("not json")

        listed = manager.list()

        assert len(listed) == 1
        assert listed[0].label == "good"


class TestCorruptStateRefusal:
    """ADR-0037: create() is the one read path here that would otherwise
    turn a corrupt line into a permanent, clean-looking artifact."""

    def test_get_report_distinguishes_missing_from_corrupt(self, store):
        manager = SnapshotManager(store)

        missing, missing_report = manager.get_report(1)
        assert missing is None
        assert missing_report.exists is False

        manager.create()
        (manager.snapshots_dir / "001" / "snapshot.json").write_text("not json")
        corrupt, corrupt_report = manager.get_report(1)
        assert corrupt is None
        assert corrupt_report.exists is True
        assert not corrupt_report.clean

    def test_list_omits_corrupt_but_integrity_names_it(self, store):
        manager = SnapshotManager(store)
        manager.create(label="good")
        bad = manager.create(label="bad")
        (manager.snapshots_dir / f"{bad.number:03d}" / "snapshot.json").write_text("not json")

        assert len(manager.list()) == 1
        integrity = manager.integrity()
        assert len(integrity) == 1
        assert integrity[0].source == f"snapshots/{bad.number:03d}/snapshot.json"

    def test_create_refuses_on_corrupt_state(self, store):
        store.append(Decision(statement="good"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ bad\n")
        manager = SnapshotManager(store)

        with pytest.raises(CorruptStateError):
            manager.create()

        assert not manager.snapshots_dir.exists() or list(manager.snapshots_dir.iterdir()) == []

    def test_create_with_force_succeeds_despite_corruption(self, store):
        store.append(Decision(statement="good"))
        path = store.root / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ bad\n")
        manager = SnapshotManager(store)

        snap = manager.create(force=True)

        assert snap.number == 1
