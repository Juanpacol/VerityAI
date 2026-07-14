#!/usr/bin/env python3
"""Backfill embedding vectors onto existing Rule nodes for hybrid retrieval.

Wiring script: unlike kg/retrieval.py (kg-only, no neural/ import) or
kg/client.py, this script is allowed to import both kg and neural — it's
exactly the layer responsible for injecting an embed_fn into the KG in the
first place (see docs/adr/0003-hybrid-retrieval.md).

Idempotent: only (re-)embeds rules whose stored `embedding_model` doesn't
match `--model`, so re-running after switching OLLAMA_EMBED_MODEL only
re-embeds what's now stale, and re-running with no model change is a no-op.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from neo4j import Driver, GraphDatabase

from verityai.kg.ingestion import KGIngestion
from verityai.neural.ollama_client import OllamaClient, OllamaEmbeddingError


def rules_needing_backfill(
    driver: Driver, language: str, target_model: str
) -> list[tuple[str, str, str]]:
    """Return (id, name, description) for rules not yet embedded with target_model."""
    query = """
    MATCH (r:Rule)
    WHERE $language IN r.applies_to
    RETURN r.id AS id, r.name AS name, r.description AS description,
           r.embedding_model AS embedding_model
    """
    with driver.session() as session:
        result = session.run(query, language=language)
        return [
            (record["id"], record["name"], record["description"])
            for record in result
            if record["embedding_model"] != target_model
        ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default="python", help="Language filter (default: python)")
    parser.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_EMBED_MODEL", "llama3.2"),
        help="Embedding model name (default: $OLLAMA_EMBED_MODEL or llama3.2)",
    )
    args = parser.parse_args()

    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "neo4jpassword")
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    print(f"Connecting to Neo4j at {neo4j_uri}...")
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    ingestion = KGIngestion(driver)
    ollama = OllamaClient(embed_model=args.model, base_url=ollama_host)

    pending = rules_needing_backfill(driver, args.language, args.model)
    print(f"{len(pending)} rule(s) need embeddings for model={args.model!r}")

    embedded, failed = 0, 0
    for rule_id, name, description in pending:
        text = f"{name} {description}"
        try:
            vector = ollama.embed(text, model=args.model)
        except OllamaEmbeddingError as e:
            print(f"  x {name}: {e}")
            failed += 1
            continue

        ingestion.set_rule_embedding(rule_id, vector, args.model)
        print(f"  + {name} ({len(vector)}-dim)")
        embedded += 1

    ollama.close()
    driver.close()

    print(f"\nDone: {embedded} embedded, {failed} failed, model={args.model!r}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
