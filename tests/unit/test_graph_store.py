"""Tests for the SQLite graph store.

Emphasis on the two things that would corrupt the graph silently: stale nodes
left behind when code is deleted, and the cycle detector. A graph that asserts
the existence of a function someone removed commits exactly the failure the
Consistency Engine is meant to catch, so `forget_file` gets more attention
than its size suggests.
"""

import pytest

from verityai.core.models import EdgeKind, GraphEdge, GraphNode, NodeKind
from verityai.graph.store import GraphStore


@pytest.fixture
def store():
    with GraphStore() as s:
        yield s


def node(node_id, kind=NodeKind.FUNCTION, name="f", path="a.py", **kw):
    return GraphNode(id=node_id, kind=kind, name=name, path=path, **kw)


def edge(source, target, kind=EdgeKind.CALLS, resolved=True, line=1):
    return GraphEdge(source=source, target=target, kind=kind, resolved=resolved, line=line)


class TestNodeIdentity:
    def test_ids_are_deterministic(self):
        first = GraphNode.make_id(NodeKind.FUNCTION, "a/b.py", "Cls.method")
        second = GraphNode.make_id(NodeKind.FUNCTION, "a/b.py", "Cls.method")

        assert first == second
        assert first == "function:a/b.py:Cls.method"

    def test_ids_are_readable(self):
        """A node id is an address a human reads in a query result."""
        assert "prune.py" in GraphNode.make_id(NodeKind.METHOD, "src/prune.py", "P.run")

    def test_reingest_updates_rather_than_duplicates(self, store):
        store.add_nodes([node("f:a.py:g", line=1)])
        store.add_nodes([node("f:a.py:g", line=99)])

        assert len(store.all_nodes()) == 1
        assert store.get_node("f:a.py:g").line == 99


class TestForgetFile:
    def test_forgetting_removes_the_nodes(self, store):
        store.add_nodes([node("f:a.py:g", path="a.py"), node("f:b.py:h", path="b.py")])

        store.forget_file("a.py")

        assert store.get_node("f:a.py:g") is None
        assert store.get_node("f:b.py:h") is not None

    def test_forgetting_removes_edges_in_both_directions(self, store):
        store.add_nodes([node("f:a.py:g", path="a.py"), node("f:b.py:h", path="b.py")])
        store.add_edges([edge("f:a.py:g", "f:b.py:h"), edge("f:b.py:h", "f:a.py:g")])

        store.forget_file("a.py")

        assert store.edges_from("f:b.py:h") == []
        assert store.edges_to("f:b.py:h") == []

    def test_forgetting_clears_the_file_record(self, store):
        store.record_file("a.py", "hash1", "now")

        store.forget_file("a.py")

        assert store.file_hash("a.py") is None


class TestLookup:
    def test_nodes_named_returns_every_match(self, store):
        """Ambiguity is reported, not resolved by coin flip."""
        store.add_nodes(
            [
                node("class:a.py:Store", kind=NodeKind.CLASS, name="Store", path="a.py"),
                node("class:b.py:Store", kind=NodeKind.CLASS, name="Store", path="b.py"),
            ]
        )

        assert len(store.nodes_named("Store")) == 2

    def test_nodes_named_can_filter_by_kind(self, store):
        store.add_nodes(
            [
                node("class:a.py:X", kind=NodeKind.CLASS, name="X"),
                node("function:a.py:X", kind=NodeKind.FUNCTION, name="X"),
            ]
        )

        assert len(store.nodes_named("X", NodeKind.CLASS)) == 1

    def test_search_is_substring_and_case_insensitive(self, store):
        store.add_nodes([node("f:a.py:parse_context", name="parse_context")])

        assert store.search_nodes("CONTEXT")
        assert store.search_nodes("parse")

    def test_unknown_node_returns_none(self, store):
        assert store.get_node("nope") is None


class TestTraversal:
    @pytest.fixture
    def chain(self, store):
        store.add_nodes([node(f"f:a.py:n{i}", name=f"n{i}") for i in range(4)])
        store.add_edges([edge(f"f:a.py:n{i}", f"f:a.py:n{i + 1}", line=i) for i in range(3)])
        return store

    def test_neighbours_follow_direction(self, chain):
        assert [n.name for n in chain.neighbours("f:a.py:n1", direction="out")] == ["n2"]
        assert [n.name for n in chain.neighbours("f:a.py:n1", direction="in")] == ["n0"]
        assert len(chain.neighbours("f:a.py:n1", direction="both")) == 2

    def test_reachable_respects_max_depth(self, chain):
        assert set(chain.reachable("f:a.py:n0", max_depth=1)) == {"f:a.py:n1"}
        assert set(chain.reachable("f:a.py:n0", max_depth=2)) == {"f:a.py:n1", "f:a.py:n2"}

    def test_reachable_reports_depth(self, chain):
        depths = chain.reachable("f:a.py:n0", max_depth=3)

        assert depths["f:a.py:n1"] == 1
        assert depths["f:a.py:n3"] == 3

    def test_reachable_excludes_the_start(self, chain):
        assert "f:a.py:n0" not in chain.reachable("f:a.py:n0")

    def test_unresolved_edges_are_not_traversed(self, store):
        """An unresolved target is a bare name, not a node id."""
        store.add_nodes([node("f:a.py:g")])
        store.add_edges([edge("f:a.py:g", "some_builtin", resolved=False)])

        assert store.neighbours("f:a.py:g") == []
        assert len(store.unresolved_edges()) == 1


class TestCycles:
    def test_a_simple_cycle_is_found(self, store):
        store.add_nodes([node(f"file:{c}.py", kind=NodeKind.FILE, path=f"{c}.py") for c in "abc"])
        store.add_edges(
            [
                edge("file:a.py", "file:b.py", EdgeKind.IMPORTS),
                edge("file:b.py", "file:c.py", EdgeKind.IMPORTS),
                edge("file:c.py", "file:a.py", EdgeKind.IMPORTS),
            ]
        )

        cycles = store.cycles(EdgeKind.IMPORTS)

        assert len(cycles) == 1
        assert set(cycles[0]) == {"file:a.py", "file:b.py", "file:c.py"}

    def test_the_full_path_is_returned_not_just_a_boolean(self, store):
        """'There is a circular import' is not actionable; the path is."""
        store.add_nodes(
            [node("file:a.py", kind=NodeKind.FILE), node("file:b.py", kind=NodeKind.FILE)]
        )
        store.add_edges(
            [
                edge("file:a.py", "file:b.py", EdgeKind.IMPORTS),
                edge("file:b.py", "file:a.py", EdgeKind.IMPORTS),
            ]
        )

        cycle = store.cycles(EdgeKind.IMPORTS)[0]

        assert cycle[0] == cycle[-1], "a cycle path should close on itself"
        assert len(cycle) == 3

    def test_an_acyclic_graph_reports_nothing(self, store):
        store.add_nodes(
            [node("file:a.py", kind=NodeKind.FILE), node("file:b.py", kind=NodeKind.FILE)]
        )
        store.add_edges([edge("file:a.py", "file:b.py", EdgeKind.IMPORTS)])

        assert store.cycles(EdgeKind.IMPORTS) == []

    def test_the_same_cycle_is_reported_once(self, store):
        """Discovered from three roots, it is still one cycle."""
        store.add_nodes([node(f"file:{c}.py", kind=NodeKind.FILE) for c in "abc"])
        store.add_edges(
            [
                edge("file:a.py", "file:b.py", EdgeKind.IMPORTS),
                edge("file:b.py", "file:c.py", EdgeKind.IMPORTS),
                edge("file:c.py", "file:a.py", EdgeKind.IMPORTS),
            ]
        )

        assert len(store.cycles(EdgeKind.IMPORTS)) == 1

    def test_a_deep_chain_does_not_blow_the_stack(self, store):
        """Iterative DFS, not recursion -- the pathological repo is exactly
        the one where this is most worth running."""
        depth = 2000
        store.add_nodes([node(f"file:{i}.py", kind=NodeKind.FILE) for i in range(depth)])
        store.add_edges(
            [edge(f"file:{i}.py", f"file:{i + 1}.py", EdgeKind.IMPORTS) for i in range(depth - 1)]
        )

        assert store.cycles(EdgeKind.IMPORTS) == []

    def test_cycles_only_consider_the_requested_edge_kind(self, store):
        store.add_nodes([node("f:a.py:g"), node("f:b.py:h")])
        store.add_edges(
            [
                edge("f:a.py:g", "f:b.py:h", EdgeKind.CALLS),
                edge("f:b.py:h", "f:a.py:g", EdgeKind.CALLS),
            ]
        )

        assert store.cycles(EdgeKind.IMPORTS) == []
        assert len(store.cycles(EdgeKind.CALLS)) == 1


class TestPersistence:
    def test_the_graph_survives_reopening(self, tmp_path):
        path = tmp_path / "graph.db"
        with GraphStore(path) as first:
            first.add_nodes([node("f:a.py:g", name="g")])

        with GraphStore(path) as second:
            assert second.get_node("f:a.py:g") is not None

    def test_for_verity_dir_places_the_file(self, tmp_path):
        store = GraphStore.for_verity_dir(tmp_path)
        store.close()

        assert (tmp_path / "graph.db").exists()

    def test_clear_empties_everything(self, store):
        store.add_nodes([node("f:a.py:g")])
        store.add_edges([edge("f:a.py:g", "f:a.py:g")])
        store.record_file("a.py", "h", "now")

        store.clear()

        assert store.stats()["nodes.total"] == 0
        assert store.known_files() == set()


class TestStats:
    def test_stats_break_down_by_kind(self, store):
        store.add_nodes(
            [
                node("class:a.py:C", kind=NodeKind.CLASS),
                node("function:a.py:f", kind=NodeKind.FUNCTION),
            ]
        )

        stats = store.stats()

        assert stats["nodes.class"] == 1
        assert stats["nodes.function"] == 1
        assert stats["nodes.total"] == 2

    def test_unresolved_edges_are_counted_separately(self, store):
        store.add_nodes([node("f:a.py:g")])
        store.add_edges(
            [edge("f:a.py:g", "known"), edge("f:a.py:g", "unknown", resolved=False, line=2)]
        )

        assert store.stats()["edges.unresolved"] == 1
