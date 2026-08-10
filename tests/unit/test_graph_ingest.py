"""Tests for repository ingestion.

Two themes carry most of the weight:

- **Declared scope.** Non-Python files must appear in `skipped` with a reason,
  and the coverage figure must be honest about what it did not read. A graph
  that silently covers a third of a repo answers "does this exist" confidently
  and wrongly.
- **Unresolved calls survive.** A hard constraint, because a call to something
  that exists nowhere is what a hallucinated API looks like from the graph's
  side, and Phase 3 needs those edges.
"""

import pytest

from verityai.core.models import EdgeKind, NodeKind
from verityai.graph.ingest import ingest_repo, is_test_path, module_qualname, walk_repo
from verityai.graph.store import GraphStore


@pytest.fixture
def store():
    with GraphStore() as s:
        yield s


@pytest.fixture
def repo(tmp_path):
    """A small but structurally realistic project."""
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests").mkdir()

    (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "pkg" / "core.py").write_text(
        '''"""Core module."""
import json


class Base:
    """A base class."""

    def describe(self) -> str:
        return "base"


class Service(Base):
    """Does the work."""

    def __init__(self, name: str):
        self.name = name

    def run(self, count: int = 1) -> str:
        return self.helper(count)

    def helper(self, count: int) -> str:
        return json.dumps({"n": count})


def make_service(name: str) -> Service:
    """Factory."""
    return Service(name)
'''
    )
    (tmp_path / "tests" / "test_core.py").write_text(
        """from pkg.core import make_service


def test_service_runs():
    service = make_service("x")
    assert service.run(2)
"""
    )
    (tmp_path / "README.md").write_text("# readme")
    (tmp_path / "data.json").write_text("{}")
    return tmp_path


class TestWalking:
    def test_skip_dirs_are_not_walked(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "real.py").write_text("x = 1")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "junk.py").write_text("x = 1")
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "lib.py").write_text("x = 1")

        found = [p.name for p in walk_repo(tmp_path)]

        assert "real.py" in found
        assert "junk.py" not in found
        assert "lib.py" not in found

    def test_verity_state_is_not_walked(self, tmp_path):
        (tmp_path / ".verity").mkdir()
        (tmp_path / ".verity" / "thing.py").write_text("x = 1")

        assert walk_repo(tmp_path) == []

    def test_module_qualname_drops_src_and_init(self):
        from pathlib import Path

        assert module_qualname(Path("src/pkg/core.py")) == "pkg.core"
        assert module_qualname(Path("src/pkg/__init__.py")) == "pkg"
        assert module_qualname(Path("tests/test_x.py")) == "tests.test_x"

    def test_test_paths_are_recognised(self):
        from pathlib import Path

        assert is_test_path(Path("tests/test_core.py"))
        assert is_test_path(Path("src/thing_test.py"))
        assert not is_test_path(Path("src/pkg/core.py"))


class TestDeclaredScope:
    def test_non_python_files_are_recorded_with_a_reason(self, repo, store):
        report = ingest_repo(repo, store)

        assert "README.md" in report.skipped
        assert "not Python" in report.skipped["README.md"]
        assert "data.json" in report.skipped

    def test_coverage_is_relative_to_eligible_files(self, repo, store):
        """An earlier version divided by the whole tree and reported 4% on a
        repo where every Python file had in fact been ingested."""
        report = ingest_repo(repo, store)

        assert report.files_eligible == 3
        assert report.files_in_graph == 3
        assert "100%" in report.coverage_note

    def test_the_note_states_what_was_not_read(self, repo, store):
        report = ingest_repo(repo, store)

        assert "non-Python" in report.coverage_note
        assert "Python-only" in report.coverage_note

    def test_a_nested_project_is_not_ingested(self, tmp_path, store):
        """A vendored dependency answers questions about someone else's design.

        Motivating case: this repo carries research/truthfulqa/, a cloned
        reference implementation. Without this rule the graph reported numpy
        and neo4j as dependencies of a project that has neither.
        """
        (tmp_path / "mine.py").write_text("def mine():\n    pass\n")
        vendored = tmp_path / "vendor" / "otherlib"
        vendored.mkdir(parents=True)
        (vendored / "setup.py").write_text("from setuptools import setup\n")
        (vendored / "theirs.py").write_text("import numpy\n\ndef theirs():\n    pass\n")

        report = ingest_repo(tmp_path, store)

        assert store.nodes_named("mine")
        assert not store.nodes_named("theirs")
        assert report.files_vendored == 2
        assert "nested project" in report.coverage_note

    def test_the_root_project_is_not_treated_as_nested(self, tmp_path, store):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        (tmp_path / "mine.py").write_text("def mine():\n    pass\n")

        ingest_repo(tmp_path, store)

        assert store.nodes_named("mine")

    def test_vendored_files_are_counted_apart_from_non_python(self, tmp_path, store):
        """'We do not read Rust' and 'we chose not to read this Python' are
        different facts and must not be merged into one number."""
        (tmp_path / "mine.py").write_text("x = 1\n")
        (tmp_path / "notes.md").write_text("# hi\n")
        nested = tmp_path / "vendor"
        nested.mkdir()
        (nested / "setup.py").write_text("")

        report = ingest_repo(tmp_path, store)

        assert report.files_vendored == 1
        assert report.out_of_scope == 1

    def test_a_syntax_error_is_reported_not_swallowed(self, tmp_path, store):
        (tmp_path / "broken.py").write_text("def f(\n")

        report = ingest_repo(tmp_path, store)

        assert "broken.py" in report.skipped
        assert "syntax error" in report.skipped["broken.py"]
        assert report.failed == 1

    def test_one_broken_file_does_not_stop_the_rest(self, tmp_path, store):
        (tmp_path / "broken.py").write_text("def f(\n")
        (tmp_path / "fine.py").write_text("def g():\n    pass\n")

        report = ingest_repo(tmp_path, store)

        assert report.files_ingested == 1
        assert store.nodes_named("g")


class TestExtraction:
    def test_classes_functions_and_methods_are_found(self, repo, store):
        ingest_repo(repo, store)

        assert store.nodes_named("Service", NodeKind.CLASS)
        assert store.nodes_named("make_service", NodeKind.FUNCTION)
        assert store.nodes_named("run", NodeKind.METHOD)

    def test_methods_carry_their_class_in_the_qualname(self, repo, store):
        ingest_repo(repo, store)

        run = store.nodes_named("run", NodeKind.METHOD)[0]
        assert run.qualname == "Service.run"

    def test_signatures_are_captured(self, repo, store):
        ingest_repo(repo, store)

        run = store.nodes_named("run", NodeKind.METHOD)[0]
        assert "count: int" in run.signature
        assert "-> str" in run.signature

    def test_docstrings_are_captured(self, repo, store):
        ingest_repo(repo, store)

        assert "Does the work" in store.nodes_named("Service", NodeKind.CLASS)[0].docstring

    def test_line_numbers_are_recorded(self, repo, store):
        ingest_repo(repo, store)

        run = store.nodes_named("run", NodeKind.METHOD)[0]
        assert run.line and run.end_line and run.end_line >= run.line

    def test_tests_are_a_distinct_kind(self, repo, store):
        ingest_repo(repo, store)

        assert store.nodes_named("test_service_runs", NodeKind.TEST)

    def test_a_helper_named_test_outside_a_test_file_is_not_a_test(self, tmp_path, store):
        (tmp_path / "helpers.py").write_text("def test_helper():\n    pass\n")

        ingest_repo(tmp_path, store)

        assert store.nodes_named("test_helper", NodeKind.FUNCTION)
        assert not store.nodes_named("test_helper", NodeKind.TEST)

    def test_inheritance_is_recorded(self, repo, store):
        ingest_repo(repo, store)

        service = store.nodes_named("Service", NodeKind.CLASS)[0]
        assert any(e.kind is EdgeKind.INHERITS for e in store.edges_from(service.id))

    def test_containment_links_classes_to_methods(self, repo, store):
        ingest_repo(repo, store)

        service = store.nodes_named("Service", NodeKind.CLASS)[0]
        contained = {n.name for n in store.neighbours(service.id, [EdgeKind.CONTAINS])}
        assert {"run", "helper", "__init__"} <= contained


class TestResolution:
    def test_a_call_within_a_class_resolves(self, repo, store):
        ingest_repo(repo, store)

        run = store.nodes_named("run", NodeKind.METHOD)[0]
        called = {n.name for n in store.neighbours(run.id, [EdgeKind.CALLS])}
        assert "helper" in called

    def test_a_cross_file_call_resolves(self, repo, store):
        ingest_repo(repo, store)

        test = store.nodes_named("test_service_runs", NodeKind.TEST)[0]
        called = {n.name for n in store.neighbours(test.id, [EdgeKind.CALLS])}
        assert "make_service" in called

    def test_unresolved_calls_are_kept_not_dropped(self, repo, store):
        """A hard constraint: these are Phase 3's raw material."""
        ingest_repo(repo, store)

        unresolved = store.unresolved_edges()

        assert unresolved
        assert any(e.target == "json.dumps" for e in unresolved)

    def test_an_ambiguous_name_stays_unresolved(self, tmp_path, store):
        """A wrong edge is worse than a missing one."""
        (tmp_path / "a.py").write_text("def shared():\n    pass\n")
        (tmp_path / "b.py").write_text("def shared():\n    pass\n")
        (tmp_path / "c.py").write_text("def caller():\n    shared()\n")

        ingest_repo(tmp_path, store)

        caller = store.nodes_named("caller")[0]
        assert store.neighbours(caller.id, [EdgeKind.CALLS]) == []
        assert any(e.target == "shared" for e in store.unresolved_edges())

    def test_tests_edges_are_derived_from_calls(self, repo, store):
        ingest_repo(repo, store)

        factory = store.nodes_named("make_service", NodeKind.FUNCTION)[0]
        testers = store.neighbours(factory.id, [EdgeKind.TESTS], direction="in")
        assert [n.name for n in testers] == ["test_service_runs"]

    def test_from_package_import_submodule_resolves_to_the_submodule_file(self, tmp_path, store):
        """Regression (ADR-0018): `from billing import tax_rates` is a real
        import of `billing/tax_rates.py`, not of `billing/__init__.py` --
        but grammatically it looks identical to importing a plain symbol
        defined inside `__init__.py`. Only a post-walk file-existence check
        can tell them apart, and it must prefer the submodule file when one
        exists."""
        (tmp_path / "billing").mkdir()
        (tmp_path / "billing" / "__init__.py").write_text("")
        (tmp_path / "billing" / "tax_rates.py").write_text("REGION_RATES = {}\n")
        (tmp_path / "billing" / "tax.py").write_text(
            "from billing import tax_rates\n\n\ndef apply_tax():\n    return tax_rates.REGION_RATES\n"
        )

        ingest_repo(tmp_path, store)

        tax_file = next(n for n in store.all_nodes(NodeKind.FILE) if n.path == "billing/tax.py")
        imports = store.neighbours(tax_file.id, [EdgeKind.IMPORTS], direction="out")
        assert "billing/tax_rates.py" in {n.path for n in imports}
        assert "billing/__init__.py" not in {n.path for n in imports}

    def test_from_package_import_symbol_still_resolves_to_the_package(self, tmp_path, store):
        """The other half of the same ambiguity: `from billing import
        HELPER` where `HELPER` is a plain symbol defined in
        `billing/__init__.py`, not a submodule file -- must still resolve
        to the package, unchanged from before this fix."""
        (tmp_path / "billing").mkdir()
        (tmp_path / "billing" / "__init__.py").write_text("HELPER = 1\n")
        (tmp_path / "billing" / "tax.py").write_text("from billing import HELPER\n")

        ingest_repo(tmp_path, store)

        tax_file = next(n for n in store.all_nodes(NodeKind.FILE) if n.path == "billing/tax.py")
        imports = store.neighbours(tax_file.id, [EdgeKind.IMPORTS], direction="out")
        assert "billing/__init__.py" in {n.path for n in imports}


class TestIncrementality:
    def test_unchanged_files_are_skipped_on_reingest(self, repo, store):
        ingest_repo(repo, store)

        second = ingest_repo(repo, store)

        assert second.files_ingested == 0
        assert second.files_unchanged == 3

    def test_a_changed_file_is_reingested(self, repo, store):
        ingest_repo(repo, store)
        (repo / "src" / "pkg" / "core.py").write_text("def brand_new():\n    pass\n")

        second = ingest_repo(repo, store)

        assert second.files_ingested == 1
        assert store.nodes_named("brand_new")

    def test_deleted_symbols_do_not_linger(self, repo, store):
        """The graph must never assert the existence of removed code."""
        ingest_repo(repo, store)
        assert store.nodes_named("make_service")

        (repo / "src" / "pkg" / "core.py").write_text("def only_this():\n    pass\n")
        ingest_repo(repo, store)

        assert not store.nodes_named("make_service")

    def test_a_deleted_file_is_removed_from_the_graph(self, repo, store):
        ingest_repo(repo, store)
        (repo / "src" / "pkg" / "core.py").unlink()

        ingest_repo(repo, store)

        assert not store.nodes_named("Service")
        assert "src/pkg/core.py" not in store.known_files()

    def test_force_reingests_everything(self, repo, store):
        ingest_repo(repo, store)

        forced = ingest_repo(repo, store, force=True)

        assert forced.files_ingested == 3

    def test_incrementality_is_keyed_on_content_not_mtime(self, repo, store):
        """A checkout or rebase changes mtime without changing content."""
        import os
        import time

        ingest_repo(repo, store)
        target = repo / "src" / "pkg" / "core.py"
        future = time.time() + 1000
        os.utime(target, (future, future))

        second = ingest_repo(repo, store)

        assert second.files_ingested == 0
