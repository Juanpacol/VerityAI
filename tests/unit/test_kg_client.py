"""Unit tests for kg/client.py -- including regression coverage for a real
bug found while building Phase 4 Part C: several methods queried
`r.description`/`a.description` from Neo4j but never passed it to the
Rule/Algorithm Pydantic constructor (Rule.condition and Algorithm.description
are both required fields with no default), so every one of these methods
would have raised a ValidationError against real KG data. There was no
prior test coverage for this module, so the bug had never been exercised.

Uses a fake neo4j Driver/Session (plain dicts as records -- dicts already
support the `record["r.name"]` indexing neo4j Records provide), never a
live Neo4j instance.
"""

from verityai.kg.client import KGClient


class FakeResult:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class FakeSession:
    def __init__(self, records):
        self._records = records

    def run(self, query, **params):
        return FakeResult(self._records)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeDriver:
    def __init__(self, records):
        self._records = records

    def session(self):
        return FakeSession(self._records)


ALGORITHM_RECORD = {
    "a.name": "binary_search",
    "a.description": "Search for target in sorted array",
    "a.code": "def binary_search(arr, target): ...",
    "a.language": "python",
    "a.complexity_time": "O(log n)",
    "a.complexity_space": "O(1)",
    "a.verified": True,
}

RULE_RECORD = {
    "r.name": "no_null_deref",
    "r.description": "Ensure no null pointer dereferences",
    "r.category": "security",
    "r.severity": "critical",
    "r.formal_spec": None,
    "r.applies_to": ["python"],
}

RULE_RECORD_WITH_EMBEDDING = {
    **RULE_RECORD,
    "r.embedding": [0.1, 0.2, 0.3],
    "r.embedding_model": "llama3.2",
}

RULE_RECORD_NO_EMBEDDING = {
    **RULE_RECORD,
    "r.embedding": None,
    "r.embedding_model": None,
}


class TestGetAlgorithmById:
    def test_found_includes_description(self):
        client = KGClient(FakeDriver([ALGORITHM_RECORD]))
        algo = client.get_algorithm_by_id("algo_binary_search")

        assert algo is not None
        assert algo.description == "Search for target in sorted array"
        assert algo.name == "binary_search"

    def test_not_found_returns_none(self):
        client = KGClient(FakeDriver([]))
        assert client.get_algorithm_by_id("nope") is None


class TestGetAllAlgorithms:
    def test_includes_description(self):
        client = KGClient(FakeDriver([ALGORITHM_RECORD]))
        algorithms = client.get_all_algorithms(language="python")

        assert len(algorithms) == 1
        assert algorithms[0].description == "Search for target in sorted array"


class TestGetRuleById:
    def test_found_includes_condition(self):
        client = KGClient(FakeDriver([RULE_RECORD]))
        rule = client.get_rule_by_id("rule_no_null_deref")

        assert rule is not None
        assert rule.condition == "Ensure no null pointer dereferences"

    def test_not_found_returns_none(self):
        client = KGClient(FakeDriver([]))
        assert client.get_rule_by_id("nope") is None


class TestGetRulesByCategory:
    def test_includes_condition(self):
        client = KGClient(FakeDriver([RULE_RECORD]))
        rules = client.get_rules_by_category("security", language="python")

        assert len(rules) == 1
        assert rules[0].condition == "Ensure no null pointer dereferences"


class TestGetRulesForAlgorithm:
    def test_includes_condition(self):
        client = KGClient(FakeDriver([RULE_RECORD]))
        rules = client.get_rules_for_algorithm("algo_binary_search")

        assert len(rules) == 1
        assert rules[0].condition == "Ensure no null pointer dereferences"


class TestGetAllRules:
    def test_includes_condition(self):
        client = KGClient(FakeDriver([RULE_RECORD]))
        rules = client.get_all_rules(language="python")

        assert len(rules) == 1
        assert rules[0].name == "no_null_deref"
        assert rules[0].condition == "Ensure no null pointer dereferences"

    def test_empty_kg_returns_empty_list(self):
        client = KGClient(FakeDriver([]))
        assert client.get_all_rules() == []


class TestGetRulesWithEmbeddings:
    def test_returns_rule_and_embedding_pair(self):
        client = KGClient(FakeDriver([RULE_RECORD_WITH_EMBEDDING]))
        pairs = client.get_rules_with_embeddings(language="python")

        assert len(pairs) == 1
        rule, embedding = pairs[0]
        assert rule.name == "no_null_deref"
        assert rule.condition == "Ensure no null pointer dereferences"
        assert embedding == [0.1, 0.2, 0.3]

    def test_rule_without_embedding_yields_none(self):
        client = KGClient(FakeDriver([RULE_RECORD_NO_EMBEDDING]))
        pairs = client.get_rules_with_embeddings(language="python")

        assert len(pairs) == 1
        rule, embedding = pairs[0]
        assert rule.name == "no_null_deref"
        assert embedding is None

    def test_empty_kg_returns_empty_list(self):
        client = KGClient(FakeDriver([]))
        assert client.get_rules_with_embeddings() == []
