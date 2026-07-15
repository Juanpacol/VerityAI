"""File-backed storage for `EvidenceRecord`s.

One JSON file per record under `<root>/<source>/<id>.json` -- diffable,
auditable, committed to the repo like `docs/results/*.json`. A single
`manifest.json` at the root indexes every record's hash/topics/source so
`known_hashes()` and `iter_records()` don't need to open every file on disk.

Writes are write-if-changed: saving a record whose content hash already
matches what's on disk is a no-op (besides confirming the manifest entry),
which is what makes repeated fetcher runs idempotent.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from verityai.evidence.models import EvidenceRecord, ResearchTopic

_MANIFEST_NAME = "manifest.json"


class EvidenceStore:
    """Reads and writes `EvidenceRecord`s under `root`."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self.root / _MANIFEST_NAME

    def _load_manifest(self) -> dict:
        if not self._manifest_path.exists():
            return {}
        manifest: dict = json.loads(self._manifest_path.read_text())
        return manifest

    def _write_manifest(self, manifest: dict) -> None:
        self._manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    def _record_path(self, source: str, record_id: str) -> Path:
        return self.root / source / f"{record_id}.json"

    def save(self, record: EvidenceRecord) -> bool:
        """Persist `record`. Returns True if content changed, False if it was already current.

        The "already current" short-circuit also checks the backing file
        actually exists on disk -- not just that the manifest says so.
        Without that check, a record file deleted out-of-band (while the
        manifest survives) would be silently treated as "still saved" and
        never rewritten, leaving a manifest entry that points at nothing.
        """
        manifest = self._load_manifest()
        existing = manifest.get(record.id)
        path = self._record_path(record.source, record.id)
        if (
            existing is not None
            and existing.get("content_hash") == record.content_hash
            and path.exists()
        ):
            return False

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(indent=2))

        manifest[record.id] = {
            "source": record.source,
            "content_hash": record.content_hash,
            "retrieved_at": record.retrieved_at,
            "feeds_topics": list(record.feeds_topics),
        }
        self._write_manifest(manifest)
        return True

    def load(self, record_id: str) -> Optional[EvidenceRecord]:
        manifest = self._load_manifest()
        entry = manifest.get(record_id)
        if entry is None:
            return None
        path = self._record_path(entry["source"], record_id)
        if not path.exists():
            return None
        return EvidenceRecord.model_validate_json(path.read_text())

    def iter_records(
        self,
        source: Optional[str] = None,
        topic: Optional[ResearchTopic] = None,
    ) -> Iterator[EvidenceRecord]:
        manifest = self._load_manifest()
        for record_id, entry in manifest.items():
            if source is not None and entry.get("source") != source:
                continue
            if topic is not None and topic not in entry.get("feeds_topics", []):
                continue
            record = self.load(record_id)
            if record is not None:
                yield record

    def known_hashes(self) -> set:
        manifest = self._load_manifest()
        return {entry["content_hash"] for entry in manifest.values()}
