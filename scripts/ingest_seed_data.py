#!/usr/bin/env python3
"""Ingest Lote 1 seed data into Neo4j."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from neo4j import GraphDatabase

from verityai.kg.ingestion import KGIngestion

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "neo4jpassword"


def main():
    """Ingest seed data into Neo4j."""
    print("Connecting to Neo4j...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    # Test connection
    with driver.session() as session:
        result = session.run("RETURN 1 as num")
        result.single()
    print("✓ Connected to Neo4j")

    ingestion = KGIngestion(driver)

    # Clear existing data
    print("\nClearing existing data...")
    ingestion.clear_all()

    # Ingest algorithms
    seed_dir = Path(__file__).parent.parent / "src" / "verityai" / "kg" / "seed_data"
    algo_path = seed_dir / "algorithms.json"
    print(f"\nIngesting algorithms from {algo_path}...")
    algo_count = ingestion.ingest_algorithms(str(algo_path))
    print(f"✓ Ingested {algo_count} algorithms")

    # Ingest rules
    rule_path = seed_dir / "security_rules.json"
    print(f"\nIngesting rules from {rule_path}...")
    rule_count = ingestion.ingest_rules(str(rule_path))
    print(f"✓ Ingested {rule_count} rules")

    # Link algorithms to rules
    print("\nLinking algorithms to rules...")
    link_count = ingestion.link_algorithms_to_rules(driver)
    print(f"✓ Created {link_count} algorithm-rule relationships")

    # Verify counts
    final_algo_count = ingestion.get_algorithm_count()
    final_rule_count = ingestion.get_rule_count()

    print("\n" + "=" * 50)
    print("Final state:")
    print(f"  Algorithms: {final_algo_count}")
    print(f"  Rules: {final_rule_count}")
    print("=" * 50)

    driver.close()
    print("\nSeed data ingestion complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
