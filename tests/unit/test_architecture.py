"""Tests for the architecture dependency policy checker.

`TestRealDrift` pins down the finding from running this against the project's
own graph for the first time: `memory/handoff.py` imports `context.tokenizer`,
which CLAUDE.md's dependency diagram didn't list. The need was legitimate (a
handoff document has to fit a token budget), so the policy was corrected to
match reality rather than the import removed -- and this test exists so the
next drift in the *wrong* direction gets caught instead of quietly re-blessed.
"""

from pathlib import Path

import pytest

from verityai.core.models import ArchitecturePolicy, EdgeKind, GraphEdge, GraphNode, NodeKind
from verityai.graph.store import GraphStore
from verityai.reliability.architecture import (
    DEFAULT_POLICY,
    check_architecture,
    check_architecture_at,
    top_package,
)


def file_node(qualname, path=None):
    return GraphNode(
        id=GraphNode.make_id(NodeKind.FILE, path or qualname.replace(".", "/") + ".py"),
        kind=NodeKind.FILE,
        name=qualname.rsplit(".", 1)[-1],
        qualname=qualname,
        path=path or qualname.replace(".", "/") + ".py",
    )


class TestTopPackage:
    def test_extracts_the_engine_package(self):
        assert top_package("verityai.context.prune") == "context"

    def test_a_two_segment_qualname_still_resolves(self):
        assert top_package("verityai.cli") == "cli"

    def test_non_verityai_modules_are_out_of_scope(self):
        assert top_package("tests.unit.test_x") is None

    def test_bare_verityai_has_no_package(self):
        assert top_package("verityai") is None


class TestPolicyEnforcement:
    @pytest.fixture
    def store(self):
        with GraphStore() as s:
            yield s

    def test_an_allowed_import_is_not_flagged(self, store):

        a = file_node("verityai.graph.query")
        b = file_node("verityai.context.rank")
        store.add_nodes([a, b])
        store.add_edges([GraphEdge(source=a.id, target=b.id, kind=EdgeKind.IMPORTS)])

        assert check_architecture(store).is_clean

    def test_a_disallowed_import_is_flagged(self, store):

        a = file_node("verityai.graph.query")
        b = file_node("verityai.memory.store")
        store.add_nodes([a, b])
        store.add_edges([GraphEdge(source=a.id, target=b.id, kind=EdgeKind.IMPORTS)])

        report = check_architecture(store)

        assert not report.is_clean
        assert "graph" in report.violations[0].message
        assert "memory" in report.violations[0].message

    def test_core_is_always_an_allowed_target(self, store):

        a = file_node("verityai.context.prune")
        core = file_node("verityai.core.models")
        store.add_nodes([a, core])
        store.add_edges([GraphEdge(source=a.id, target=core.id, kind=EdgeKind.IMPORTS)])

        assert check_architecture(store).is_clean

    def test_same_package_imports_are_never_flagged(self, store):

        a = file_node("verityai.context.prune")
        b = file_node("verityai.context.rank")
        store.add_nodes([a, b])
        store.add_edges([GraphEdge(source=a.id, target=b.id, kind=EdgeKind.IMPORTS)])

        assert check_architecture(store).is_clean

    def test_a_star_policy_allows_anything(self, store):

        cli = file_node("verityai.cli.main")
        memory = file_node("verityai.memory.store")
        store.add_nodes([cli, memory])
        store.add_edges([GraphEdge(source=cli.id, target=memory.id, kind=EdgeKind.IMPORTS)])

        assert check_architecture(store).is_clean

    def test_an_unresolved_import_is_not_checked(self, store):
        """An edge still pointing at an EXTERNAL placeholder is a third-party
        dependency question, not this policy's concern."""

        a = file_node("verityai.graph.query")
        external = GraphNode(
            id=GraphNode.make_id(NodeKind.EXTERNAL, "somelib"),
            kind=NodeKind.EXTERNAL,
            name="somelib",
            qualname="somelib",
        )
        store.add_nodes([a, external])
        store.add_edges(
            [GraphEdge(source=a.id, target=external.id, kind=EdgeKind.IMPORTS, resolved=False)]
        )

        assert check_architecture(store).is_clean

    def test_non_verityai_files_are_not_scanned(self, store):
        a = file_node("tests.unit.test_something", path="tests/unit/test_something.py")
        store.add_nodes([a])

        report = check_architecture(store)

        assert report.files_scanned == 0

    def test_a_custom_policy_can_be_stricter(self, store):

        a = file_node("verityai.graph.query")
        b = file_node("verityai.context.rank")
        store.add_nodes([a, b])
        store.add_edges([GraphEdge(source=a.id, target=b.id, kind=EdgeKind.IMPORTS)])

        strict = ArchitecturePolicy(allowed_imports={"graph": []})

        assert not check_architecture(store, policy=strict).is_clean


class TestRealDrift:
    """The finding from running this against the project's own graph."""

    def test_memory_importing_context_is_now_declared_and_allowed(self):
        """CLAUDE.md's diagram said memory depended on core alone; the actual
        code (handoff.py needs token counting) already imported context.
        The policy was corrected to match reality, not the import removed."""
        assert "context" in DEFAULT_POLICY.allowed_imports["memory"]

    def test_the_project_itself_has_no_undeclared_violations(self):
        """The acid test: running this on the real codebase must be clean,
        or the policy has drifted from the code again."""
        report = check_architecture_at(Path(__file__).parents[2])

        assert report.is_clean, report.violations

    def test_the_project_scan_covers_a_meaningful_number_of_files(self):
        report = check_architecture_at(Path(__file__).parents[2])

        assert report.files_scanned > 30
