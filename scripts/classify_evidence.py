#!/usr/bin/env python3
"""Classify stored evidence records' relevance to T1-T6 via a local LLM.

Wiring script: like scripts/backfill_rule_embeddings.py, this is allowed to
import both evidence/ and neural/ -- it's exactly the layer responsible for
injecting a generate_fn into EvidenceClassifier (see evidence/classify.py's
embed_fn-style injection pattern, kept dependency-free of neural/ itself).

Skips records that already have a classification unless --force. Never
silently "classifies" with no model reachable -- EvidenceClassifier's own
degradation ladder (no generate_fn / generate_fn raises / unparseable
response) always stamps classified_by + degraded_reason.

Usage:
  python scripts/classify_evidence.py --model llama3.2
  python scripts/classify_evidence.py --source arxiv --force
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from verityai.evidence.classify import EvidenceClassifier  # noqa: E402
from verityai.evidence.store import EvidenceStore  # noqa: E402
from verityai.neural.ollama_client import OllamaClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=None, help="Only classify records from this source")
    parser.add_argument("--model", default="llama3.2")
    parser.add_argument("--ollama-host", default="http://localhost:11434")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-classify records that already have a classification",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    store = EvidenceStore(repo_root / "docs" / "evidence")
    llm = OllamaClient(model=args.model, base_url=args.ollama_host)
    classifier = EvidenceClassifier(generate_fn=llm.generate, model_name=args.model)

    classified = 0
    degraded = 0
    skipped = 0
    for record in store.iter_records(source=args.source):
        if record.classification is not None and not args.force:
            skipped += 1
            continue

        record.classification = classifier.classify(record)
        store.save(record)

        if record.classification.degraded_reason:
            degraded += 1
            print(f"{record.id}: degraded ({record.classification.degraded_reason})")
        else:
            classified += 1
            print(f"{record.id}: classified")

    print(f"\n{classified} classified, {degraded} degraded, {skipped} skipped (already classified)")
    llm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
