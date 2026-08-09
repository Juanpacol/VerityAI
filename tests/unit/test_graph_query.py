"""Tests for graph queries.

`TestRelationshipExpansion` is the one that matters. It pins down the claim
that justifies building a graph at all: that code with *no lexical overlap*
with the task still surfaces, because an edge connects it to something that
does. If that stops being true, the graph is an expensive way to do text
search and this phase was not worth doing.
"""

import pytest

from verityai.core.models import NodeKind
from verityai.graph.ingest import ingest_repo
from verityai.graph.query import GraphQuery, render_relevant
from verityai.graph.store import GraphStore


@pytest.fixture
def project(tmp_path):
    """A project where the interesting relationships are not lexical.

    `apply_ceiling` is deliberately named so that a search for "rate limiting"
    cannot match it. It is reachable only because `rate_limit_request` calls
    it -- which is exactly the case a vector store misses.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()

    (tmp_path / "src" / "limits.py").write_text(
        '''"""Request throttling."""


def apply_ceiling(count, maximum):
    """Clamp a value. Deliberately unrelated vocabulary."""
    return min(count, maximum)


def rate_limit_request(key, count):
    """Apply rate limiting to an incoming request."""
    return apply_ceiling(count, 100)


def unrelated_helper(x):
    """Nothing to do with throttling whatsoever."""
    return x * 2
'''
    )
    (tmp_path / "tests" / "test_limits.py").write_text(
        """from limits import rate_limit_request


def test_requests_are_throttled():
    assert rate_limit_request("k", 500) == 100
"""
    )
    return tmp_path


@pytest.fixture
def query(project):
    store = GraphStore()
    ingest_repo(project, store)
    yield GraphQuery(store)
    store.close()


class TestRelationshipExpansion:
    """The claim that justifies the graph."""

    def test_code_with_no_lexical_overlap_still_surfaces(self, query):
        results = query.context_for("rate limiting", limit=10)
        names = [r.node.name for r in results]

        assert "rate_limit_request" in names, "the lexical match should be found"
        assert "apply_ceiling" in names, (
            "apply_ceiling shares no vocabulary with 'rate limiting' -- it can only "
            "have been found by following the call edge, which is the whole point"
        )

    def test_the_lexical_match_outranks_its_neighbours(self, query):
        results = query.context_for("rate limiting", limit=10)
        by_name = {r.node.name: r for r in results}

        assert by_name["rate_limit_request"].score > by_name["apply_ceiling"].score

    def test_expanded_results_are_marked_with_their_depth(self, query):
        results = query.context_for("rate limiting", limit=10)
        by_name = {r.node.name: r for r in results}

        assert by_name["rate_limit_request"].depth == 0
        assert by_name["apply_ceiling"].depth >= 1

    def test_every_result_explains_itself(self, query):
        for result in query.context_for("rate limiting", limit=10):
            assert result.reasons, result.node.name

    def test_the_explanation_names_the_relationship(self, query):
        results = query.context_for("rate limiting", limit=10)
        ceiling = next(r for r in results if r.node.name == "apply_ceiling")

        assert any("rate_limit_request" in reason for reason in ceiling.reasons)

    def test_tests_surface_alongside_what_they_exercise(self, query):
        names = [r.node.name for r in query.context_for("rate limiting", limit=10)]

        assert "test_requests_are_throttled" in names

    def test_truly_unrelated_code_ranks_below_related_code(self, query):
        results = query.context_for("rate limiting", limit=10)
        by_name = {r.node.name: r.score for r in results}

        if "unrelated_helper" in by_name:
            assert by_name["unrelated_helper"] < by_name["apply_ceiling"]

    def test_an_empty_task_returns_nothing(self, query):
        assert query.context_for("") == []

    def test_depth_zero_disables_expansion(self, query):
        results = query.context_for("rate limiting", limit=10, max_depth=0)

        assert all(r.depth == 0 for r in results)


class TestDefinitions:
    def test_define_finds_a_symbol(self, query):
        assert [n.name for n in query.define("rate_limit_request")] == ["rate_limit_request"]

    def test_exists_is_true_for_real_symbols(self, query):
        assert query.exists("apply_ceiling")

    def test_exists_is_false_for_invented_symbols(self, query):
        """The seed of Phase 3's hallucination check."""
        assert not query.exists("AuthService_refresh_token_that_never_existed")

    def test_define_returns_location_information(self, query):
        node = query.define("apply_ceiling")[0]

        assert node.path.endswith("limits.py")
        assert node.line


class TestRelationships:
    def test_callers_are_found(self, query):
        ceiling = query.define("apply_ceiling")[0]

        assert [n.name for n in query.callers(ceiling.id)] == ["rate_limit_request"]

    def test_callees_are_found(self, query):
        limiter = query.define("rate_limit_request")[0]

        assert "apply_ceiling" in [n.name for n in query.callees(limiter.id)]

    def test_tests_for_uses_edges_not_name_matching(self, query):
        """test_requests_are_throttled does not contain 'rate_limit_request'."""
        limiter = query.define("rate_limit_request")[0]

        assert [n.name for n in query.tests_for(limiter.id)] == ["test_requests_are_throttled"]

    def test_untested_code_is_identified(self, query):
        untested = {n.name for n in query.untested()}

        assert "unrelated_helper" in untested
        assert "rate_limit_request" not in untested

    def test_private_names_are_not_reported_as_untested(self, tmp_path):
        (tmp_path / "m.py").write_text("def _internal():\n    pass\n")
        store = GraphStore()
        ingest_repo(tmp_path, store)

        assert "_internal" not in {n.name for n in GraphQuery(store).untested()}
        store.close()

    def test_file_dependencies_report_both_directions(self, query):
        deps = query.file_dependencies("tests/test_limits.py")

        assert any("limits" in imported for imported in deps["imports"])

    def test_unresolved_calls_are_queryable(self, query):
        assert isinstance(query.unresolved_calls(), list)


class TestCycles:
    def test_a_clean_project_has_none(self, query):
        assert query.import_cycles() == []

    def test_a_circular_import_is_reported_as_a_path(self, tmp_path):
        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("import a\n")
        store = GraphStore()
        ingest_repo(tmp_path, store)

        cycles = GraphQuery(store).import_cycles()

        assert cycles
        assert len(cycles[0]) >= 2
        store.close()


class TestRendering:
    def test_results_render_with_location_and_reason(self, query):
        rendered = render_relevant(query.context_for("rate limiting", limit=3))

        assert "limits.py" in rendered
        assert "why:" in rendered

    def test_an_empty_result_suggests_building_the_graph(self):
        assert "verity graph build" in render_relevant([])


class TestSeedKinds:
    def test_files_are_not_returned_as_results(self, query):
        """A whole file is too coarse to be an answer."""
        results = query.context_for("rate limiting", limit=20)

        assert all(r.node.kind is not NodeKind.FILE for r in results)

    def test_external_modules_are_not_returned(self, query):
        results = query.context_for("rate limiting", limit=20)

        assert all(r.node.kind is not NodeKind.EXTERNAL for r in results)
