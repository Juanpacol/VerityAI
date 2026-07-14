"""Ingest seed data (JSON) into Neo4j knowledge graph."""

import json
import logging
from typing import Any

from neo4j import Driver

from verityai.ontology.models import Rule

logger = logging.getLogger(__name__)


class KGIngestion:
    """Load seed data from JSON files into Neo4j."""

    def __init__(self, driver: Driver):
        """Initialize ingestion.

        Args:
            driver: Neo4j driver instance
        """
        self.driver = driver

    def ingest_algorithms(self, json_path: str) -> int:
        """Load algorithms from JSON file into Neo4j.

        Args:
            json_path: Path to algorithms.json

        Returns:
            Number of algorithms ingested
        """
        with open(json_path) as f:
            algorithms = json.load(f)

        count = 0
        with self.driver.session() as session:
            for algo in algorithms:
                self._create_algorithm_node(session, algo)
                count += 1
                logger.info(f"Ingested algorithm: {algo['name']}")

        return count

    def ingest_rules(self, json_path: str) -> int:
        """Load rules from JSON file into Neo4j.

        Args:
            json_path: Path to security_rules.json

        Returns:
            Number of rules ingested
        """
        with open(json_path) as f:
            rules = json.load(f)

        count = 0
        with self.driver.session() as session:
            for rule in rules:
                self._create_rule_node(session, rule)
                count += 1
                logger.info(f"Ingested rule: {rule['name']}")

        return count

    def link_algorithms_to_rules(self, driver: Driver) -> int:
        """Create relationships between algorithms and rules they satisfy.

        Args:
            driver: Neo4j driver

        Returns:
            Number of relationships created
        """
        query = """
        MATCH (algo:Algorithm), (rule:Rule)
        WHERE rule.id IN algo.rules_satisfied
        MERGE (algo)-[:SATISFIES]->(rule)
        RETURN COUNT(*)
        """

        with driver.session() as session:
            record = session.run(query).single()
            assert record is not None, "COUNT query must return exactly one row"
            count = int(record[0])
            logger.info(f"Created {count} algorithm-rule relationships")
            return count

    def _create_algorithm_node(self, session: Any, algo_data: dict) -> None:
        """Create Algorithm node in Neo4j.

        Args:
            session: Neo4j session
            algo_data: Algorithm data from JSON
        """
        query = """
        CREATE (a:Algorithm {
            id: $id,
            name: $name,
            description: $description,
            code: $code,
            language: $language,
            complexity_time: $complexity_time,
            complexity_space: $complexity_space,
            verified: $verified,
            rules_satisfied: $rules_satisfied
        })
        RETURN a
        """

        session.run(
            query,
            id=algo_data["id"],
            name=algo_data["name"],
            description=algo_data["description"],
            code=algo_data["code"],
            language=algo_data["language"],
            complexity_time=algo_data["complexity_time"],
            complexity_space=algo_data["complexity_space"],
            verified=algo_data["verified"],
            rules_satisfied=algo_data.get("rules_satisfied", []),
        )

    def _create_rule_node(self, session: Any, rule_data: dict) -> None:
        """Create Rule node in Neo4j.

        Args:
            session: Neo4j session
            rule_data: Rule data from JSON
        """
        query = """
        CREATE (r:Rule {
            id: $id,
            name: $name,
            description: $description,
            category: $category,
            severity: $severity,
            formal_spec: $formal_spec,
            applies_to: $applies_to,
            test_code: $test_code
        })
        RETURN r
        """

        session.run(
            query,
            id=rule_data["id"],
            name=rule_data["name"],
            description=rule_data["description"],
            category=rule_data["category"],
            severity=rule_data["severity"],
            formal_spec=rule_data["formal_spec"],
            applies_to=rule_data["applies_to"],
            test_code=rule_data.get("test_code", ""),
        )

    def ingest_learned_rule(self, rule: Rule) -> None:
        """Write a human-approved candidate rule (continuous learning loop) to the KG.

        Unlike ingest_rules() (bulk seed loading from JSON, CREATE-only),
        this MERGEs by id: a rule approved via RuleApprovalQueue may be
        ingested more than once if the approval pipeline is re-run, and
        that must not create duplicate nodes.

        Args:
            rule: An approved Rule (see agent/rule_validation.py). Callers
                are responsible for the Z3 + human approval gate — this
                method does not re-check either.
        """
        query = """
        MERGE (r:Rule {id: $id})
        SET r.name = $name,
            r.description = $description,
            r.category = $category,
            r.severity = $severity,
            r.condition = $condition,
            r.formal_spec = $formal_spec,
            r.applies_to = $applies_to,
            r.test_code = $test_code
        RETURN r
        """

        with self.driver.session() as session:
            session.run(
                query,
                id=str(rule.id),
                name=rule.name,
                description=rule.description,
                category=rule.category,
                severity=rule.severity,
                condition=rule.condition,
                formal_spec=rule.formal_spec,
                applies_to=rule.applies_to,
                test_code=rule.test_code or "",
            )
        logger.info(f"Ingested learned rule '{rule.name}' (id={rule.id}) into KG")

    def set_rule_embedding(self, rule_id: str, embedding: list[float], model: str) -> None:
        """Store an embedding vector for an existing rule.

        Used by scripts/backfill_rule_embeddings.py. Records `model`
        alongside the vector so a later change to `OLLAMA_EMBED_MODEL`
        produces detectably-stale embeddings rather than silently mixing
        vectors from two different embedding spaces.

        Args:
            rule_id: Rule.id (string form of the UUID)
            embedding: Embedding vector
            model: Name of the embedding model used to produce it
        """
        query = """
        MATCH (r:Rule {id: $id})
        SET r.embedding = $embedding, r.embedding_model = $model
        RETURN r
        """
        with self.driver.session() as session:
            session.run(query, id=rule_id, embedding=embedding, model=model)
        logger.info(f"Stored embedding (model={model}) for rule {rule_id}")

    def clear_all(self) -> None:
        """Delete all nodes from database (be careful!)."""
        query = "MATCH (n) DETACH DELETE n"
        with self.driver.session() as session:
            session.run(query)
            logger.warning("Cleared all nodes from Neo4j")

    def get_algorithm_count(self) -> int:
        """Get count of Algorithm nodes in database.

        Returns:
            Number of algorithms
        """
        query = "MATCH (a:Algorithm) RETURN COUNT(a)"
        with self.driver.session() as session:
            record = session.run(query).single()
            assert record is not None, "COUNT query must return exactly one row"
            return int(record[0])

    def get_rule_count(self) -> int:
        """Get count of Rule nodes in database.

        Returns:
            Number of rules
        """
        query = "MATCH (r:Rule) RETURN COUNT(r)"
        with self.driver.session() as session:
            record = session.run(query).single()
            assert record is not None, "COUNT query must return exactly one row"
            return int(record[0])
