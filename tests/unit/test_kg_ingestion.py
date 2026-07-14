"""Unit tests for KGIngestion.ingest_learned_rule (kg/ingestion.py).

Uses a fake neo4j Driver/Session instead of a live Neo4j instance --
KGIngestion takes an injected Driver, same pattern as KGClient.
"""

from verityai.kg.ingestion import KGIngestion
from verityai.ontology.models import Rule


class FakeSession:
    def __init__(self, store: dict):
        self._store = store
        self.queries: list[str] = []

    def run(self, query, **kwargs):
        self.queries.append(query)
        self._store[kwargs["id"]] = kwargs
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeDriver:
    def __init__(self):
        self.store: dict = {}
        self.sessions_created: list[FakeSession] = []

    def session(self):
        session = FakeSession(self.store)
        self.sessions_created.append(session)
        return session


def make_rule(**overrides) -> Rule:
    defaults = dict(
        name="learned_rule",
        description="a candidate rule",
        category="learned",
        condition="some condition",
        severity="medium",
        applies_to=["python"],
        test_code="x = 1\nassert x == 1",
    )
    defaults.update(overrides)
    return Rule(**defaults)


class TestIngestLearnedRule:
    def test_writes_rule_fields_to_the_kg(self):
        driver = FakeDriver()
        rule = make_rule()

        KGIngestion(driver).ingest_learned_rule(rule)

        stored = driver.store[str(rule.id)]
        assert stored["name"] == rule.name
        assert stored["test_code"] == rule.test_code
        assert stored["category"] == "learned"

    def test_uses_merge_not_create_query(self):
        """MERGE is idempotent (unlike CREATE), required since a rule can be
        re-ingested if the approval pipeline runs more than once."""
        driver = FakeDriver()
        rule = make_rule()

        KGIngestion(driver).ingest_learned_rule(rule)

        query_used = driver.sessions_created[-1].queries[0]
        assert "MERGE" in query_used
        assert "CREATE" not in query_used

    def test_reingesting_same_rule_id_does_not_error_and_overwrites(self):
        driver = FakeDriver()
        rule = make_rule()

        KGIngestion(driver).ingest_learned_rule(rule)
        rule.description = "updated description"
        KGIngestion(driver).ingest_learned_rule(rule)

        assert driver.store[str(rule.id)]["description"] == "updated description"
        assert len(driver.store) == 1  # still one entry, not two

    def test_none_test_code_is_stored_as_empty_string(self):
        driver = FakeDriver()
        rule = make_rule(test_code=None)

        KGIngestion(driver).ingest_learned_rule(rule)

        assert driver.store[str(rule.id)]["test_code"] == ""
