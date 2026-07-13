"""Neo4j client for querying knowledge graph."""

import logging
from typing import Any, Optional

from neo4j import Driver

from verityai.ontology.models import Algorithm, Rule

logger = logging.getLogger(__name__)


class KGClient:
    """Client for querying and managing the Neo4j knowledge graph."""

    def __init__(self, driver: Driver):
        """Initialize KG client.

        Args:
            driver: Neo4j driver instance
        """
        self.driver = driver

    def get_rule_by_id(self, rule_id: str) -> Optional[Rule]:
        """Fetch a rule by ID.

        Args:
            rule_id: Rule ID

        Returns:
            Rule object or None if not found
        """
        query = """
        MATCH (r:Rule {id: $rule_id})
        RETURN r.name, r.description, r.category, r.severity,
               r.formal_spec, r.applies_to
        """

        with self.driver.session() as session:
            result = session.run(query, rule_id=rule_id)
            record = result.single()

            if not record:
                logger.warning(f"Rule {rule_id} not found")
                return None

            return Rule(
                name=record["r.name"],
                description=record["r.description"],
                category=record["r.category"],
                severity=record["r.severity"],
                formal_spec=record["r.formal_spec"],
                applies_to=record["r.applies_to"],
            )

    def get_rules_by_category(self, category: str, language: str = "python") -> list[Rule]:
        """Fetch rules by category and language.

        Args:
            category: Rule category (e.g., "safety", "correctness", "security")
            language: Programming language

        Returns:
            List of Rule objects
        """
        query = """
        MATCH (r:Rule)
        WHERE r.category = $category AND $language IN r.applies_to
        RETURN r.id, r.name, r.description, r.severity, r.formal_spec, r.applies_to
        ORDER BY r.severity DESC
        """

        rules = []
        with self.driver.session() as session:
            result = session.run(query, category=category, language=language)

            for record in result:
                rule = Rule(
                    name=record["r.name"],
                    description=record["r.description"],
                    category=category,
                    severity=record["r.severity"],
                    formal_spec=record["r.formal_spec"],
                    applies_to=record["r.applies_to"],
                )
                rules.append(rule)

        logger.info(f"Fetched {len(rules)} rules for category={category}, language={language}")
        return rules

    def get_rules_for_algorithm(self, algo_id: str) -> list[Rule]:
        """Fetch rules that an algorithm should satisfy.

        Args:
            algo_id: Algorithm ID

        Returns:
            List of Rule objects
        """
        query = """
        MATCH (a:Algorithm {id: $algo_id})-[:SATISFIES]->(r:Rule)
        RETURN r.id, r.name, r.description, r.category, r.severity,
               r.formal_spec, r.applies_to
        """

        rules = []
        with self.driver.session() as session:
            result = session.run(query, algo_id=algo_id)

            for record in result:
                rule = Rule(
                    name=record["r.name"],
                    description=record["r.description"],
                    category=record["r.category"],
                    severity=record["r.severity"],
                    formal_spec=record["r.formal_spec"],
                    applies_to=record["r.applies_to"],
                )
                rules.append(rule)

        logger.info(f"Fetched {len(rules)} rules for algorithm={algo_id}")
        return rules

    def get_algorithm_by_id(self, algo_id: str) -> Optional[Algorithm]:
        """Fetch an algorithm by ID.

        Args:
            algo_id: Algorithm ID

        Returns:
            Algorithm object or None if not found
        """
        query = """
        MATCH (a:Algorithm {id: $algo_id})
        RETURN a.name, a.description, a.code, a.language,
               a.complexity_time, a.complexity_space, a.verified
        """

        with self.driver.session() as session:
            result = session.run(query, algo_id=algo_id)
            record = result.single()

            if not record:
                logger.warning(f"Algorithm {algo_id} not found")
                return None

            return Algorithm(
                name=record["a.name"],
                code=record["a.code"],
                language=record["a.language"],
                complexity_time=record["a.complexity_time"],
                complexity_space=record["a.complexity_space"],
                verified=record["a.verified"],
            )

    def get_all_algorithms(self, language: str = "python") -> list[Algorithm]:
        """Fetch all algorithms for a language.

        Args:
            language: Programming language

        Returns:
            List of Algorithm objects
        """
        query = """
        MATCH (a:Algorithm {language: $language})
        RETURN a.name, a.description, a.code, a.complexity_time,
               a.complexity_space, a.verified
        ORDER BY a.name
        """

        algos = []
        with self.driver.session() as session:
            result = session.run(query, language=language)

            for record in result:
                algo = Algorithm(
                    name=record["a.name"],
                    code=record["a.code"],
                    language=language,
                    complexity_time=record["a.complexity_time"],
                    complexity_space=record["a.complexity_space"],
                    verified=record["a.verified"],
                )
                algos.append(algo)

        logger.info(f"Fetched {len(algos)} algorithms for language={language}")
        return algos

    def get_rule_count(self) -> int:
        """Get total count of rules in KG.

        Returns:
            Number of rules
        """
        query = "MATCH (r:Rule) RETURN COUNT(r) as count"
        with self.driver.session() as session:
            result = session.run(query)
            return result.single()["count"]

    def get_algorithm_count(self) -> int:
        """Get total count of algorithms in KG.

        Returns:
            Number of algorithms
        """
        query = "MATCH (a:Algorithm) RETURN COUNT(a) as count"
        with self.driver.session() as session:
            result = session.run(query)
            return result.single()["count"]
