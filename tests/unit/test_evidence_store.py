"""Unit tests for evidence/store.py -- file-backed EvidenceRecord storage.

Uses `tmp_path` for a real (but throwaway) filesystem -- no mocking needed,
these are just JSON files on disk.
"""

from verityai.evidence.models import EvidenceRecord
from verityai.evidence.store import EvidenceStore


def make_record(**overrides) -> EvidenceRecord:
    defaults = dict(
        id="arxiv_abc123",
        source="arxiv",
        source_url="https://arxiv.org/abs/1234.5678",
        retrieved_at="2026-07-15T00:00:00Z",
        retrieval_method="requests",
        content={"title": "On Calibration"},
        content_hash="a" * 64,
        feeds_topics=["T1"],
    )
    defaults.update(overrides)
    return EvidenceRecord(**defaults)


class TestSaveAndLoad:
    def test_save_then_load_round_trips(self, tmp_path):
        store = EvidenceStore(tmp_path)
        record = make_record()

        changed = store.save(record)

        assert changed is True
        loaded = store.load(record.id)
        assert loaded == record

    def test_load_unknown_id_returns_none(self, tmp_path):
        store = EvidenceStore(tmp_path)
        assert store.load("does_not_exist") is None

    def test_record_written_under_source_subdir(self, tmp_path):
        store = EvidenceStore(tmp_path)
        record = make_record(source="mbpp", id="mbpp_1")

        store.save(record)

        assert (tmp_path / "mbpp" / "mbpp_1.json").exists()

    def test_manifest_created_at_root(self, tmp_path):
        store = EvidenceStore(tmp_path)
        store.save(make_record())

        assert (tmp_path / "manifest.json").exists()


class TestIdempotentSave:
    def test_resaving_identical_content_is_a_noop(self, tmp_path):
        store = EvidenceStore(tmp_path)
        record = make_record()
        store.save(record)

        changed = store.save(record)

        assert changed is False

    def test_resave_after_out_of_band_file_deletion_rewrites(self, tmp_path):
        """Regression: if the backing file is deleted while the manifest
        survives (e.g. `rm -rf` on a source subdir without also clearing
        manifest.json), re-saving the identical record must NOT be treated
        as a no-op -- otherwise the manifest keeps pointing at a file that
        no longer exists, and the record silently vanishes from
        `iter_records()` even though `save()` reported nothing changed.
        """
        store = EvidenceStore(tmp_path)
        record = make_record()
        store.save(record)
        (tmp_path / record.source / f"{record.id}.json").unlink()

        changed = store.save(record)

        assert changed is True
        assert store.load(record.id) == record

    def test_resaving_changed_content_hash_rewrites(self, tmp_path):
        store = EvidenceStore(tmp_path)
        record = make_record()
        store.save(record)

        updated = make_record(content_hash="b" * 64)
        changed = store.save(updated)

        assert changed is True
        assert store.load(record.id).content_hash == "b" * 64


class TestIterRecords:
    def test_iter_all_records(self, tmp_path):
        store = EvidenceStore(tmp_path)
        store.save(make_record(id="a", source="arxiv"))
        store.save(make_record(id="b", source="mbpp"))

        ids = {r.id for r in store.iter_records()}
        assert ids == {"a", "b"}

    def test_iter_filtered_by_source(self, tmp_path):
        store = EvidenceStore(tmp_path)
        store.save(make_record(id="a", source="arxiv"))
        store.save(make_record(id="b", source="mbpp"))

        ids = {r.id for r in store.iter_records(source="mbpp")}
        assert ids == {"b"}

    def test_iter_filtered_by_topic(self, tmp_path):
        store = EvidenceStore(tmp_path)
        store.save(make_record(id="a", feeds_topics=["T1"]))
        store.save(make_record(id="b", feeds_topics=["T3"]))

        ids = {r.id for r in store.iter_records(topic="T3")}
        assert ids == {"b"}


class TestKnownHashes:
    def test_known_hashes_reflects_all_saved_records(self, tmp_path):
        store = EvidenceStore(tmp_path)
        store.save(make_record(id="a", content_hash="a" * 64))
        store.save(make_record(id="b", content_hash="b" * 64))

        assert store.known_hashes() == {"a" * 64, "b" * 64}

    def test_known_hashes_empty_for_fresh_store(self, tmp_path):
        store = EvidenceStore(tmp_path)
        assert store.known_hashes() == set()
