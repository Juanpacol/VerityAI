"""Walking a repository and turning it into graph nodes and edges.

Scope is declared, not implied: **Python only.** Every other file is recorded
in `IngestReport.skipped` with a reason rather than quietly passed over. This
is the same discipline as the pre-pivot `NOT_VERIFIED` status (ADR-0001) — a
graph that silently covered a third of a polyglot repository would answer
"does this function exist" confidently and wrongly, and there would be nothing
in the output to suggest otherwise.

Call resolution is best-effort and says so. Python is dynamic; a name at a
call site can be a local, an import, a method on an inferred type, or built at
runtime. The resolver tries, in order: names defined in the same module, then
names brought in by an import, then gives up. **A call it cannot resolve is
recorded with `resolved=False`, never dropped.** That is a hard constraint, and
it is what makes Phase 3 possible — a call to something that exists nowhere is
what a hallucinated API looks like from the graph's side.
"""

import ast
import hashlib
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from verityai.core.models import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    IngestReport,
    NodeKind,
)
from verityai.graph.store import GraphStore

# Directories never worth walking. Kept as a literal set rather than read from
# .gitignore: this list is about what is *code we wrote*, which is a different
# question from what git tracks (a vendored dependency may well be committed).
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "dist",
        "build",
        "site-packages",
        ".eggs",
        ".verity",
        "htmlcov",
        ".idea",
        ".vscode",
    }
)

# Files above this size are almost always generated or vendored, and parsing
# them costs more than the structure is worth.
MAX_FILE_BYTES = 1_000_000

# A subdirectory carrying one of these is its own project, not part of this
# one -- a vendored dependency, a cloned reference repo, an example app. Its
# code answers questions about somebody else's design, so putting it in the
# graph makes "where is X defined" ambiguous for no benefit.
#
# This repository is the motivating case: research/truthfulqa/ is a cloned
# reference implementation, and without this rule the graph reported numpy and
# neo4j as dependencies of a project that has neither.
NESTED_PROJECT_MARKERS = frozenset({"pyproject.toml", "setup.py", "setup.cfg", "Cargo.toml"})


def find_nested_projects(root: Path, files: list[Path]) -> set[Path]:
    """Directories under `root` that are their own project.

    The root itself is never nested, however many markers it has.
    """
    nested: set[Path] = set()
    for path in files:
        if path.name in NESTED_PROJECT_MARKERS and path.parent != root:
            nested.add(path.parent)
    return nested


_TEST_FILE_PREFIXES = ("test_",)
_TEST_FILE_SUFFIXES = ("_test.py",)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def is_test_path(path: Path) -> bool:
    """Whether a file holds tests, by the conventions pytest itself uses."""
    name = path.name
    return (
        name.startswith(_TEST_FILE_PREFIXES)
        or name.endswith(_TEST_FILE_SUFFIXES)
        or "tests" in path.parts
    )


def walk_repo(root: Path, skip_dirs: Iterable[str] = SKIP_DIRS) -> list[Path]:
    """Every file under `root`, minus the directories not worth walking.

    Returns all files, not just Python ones, because the report has to state
    how much of the tree is out of scope — and it cannot do that without
    having counted what it declined to parse.
    """
    root = Path(root).resolve()
    skip = set(skip_dirs)
    found: list[Path] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        found.append(path)
    return found


class ModuleExtractor(ast.NodeVisitor):
    """Pulls nodes and edges out of one parsed module.

    A visitor rather than a recursive function so nesting is handled by the
    traversal instead of by hand: `_scope` is the qualname stack, which is
    what makes `Class.method` come out as `Class.method` and not `method`.
    """

    def __init__(self, rel_path: str, module_qualname: str, is_test_file: bool):
        self.rel_path = rel_path
        self.module_qualname = module_qualname
        self.is_test_file = is_test_file

        self.nodes: list[GraphNode] = []
        self.edges: list[GraphEdge] = []

        self._scope: list[str] = []
        # qualname of the callable currently being visited, for CALLS edges.
        self._current_callable: str | None = None
        # Local name -> the module it came from, for import-aware resolution.
        self.imported_names: dict[str, str] = {}
        # Every callable defined here, so calls can be resolved within the file.
        self.defined: dict[str, str] = {}

        self.file_id = GraphNode.make_id(NodeKind.FILE, rel_path)

    # --- imports ---------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.imported_names[local] = alias.name
            self._add_import(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # `from . import x` has module None; record the package itself.
        module = node.module or ""
        if node.level:
            module = "." * node.level + module

        for alias in node.names:
            local = alias.asname or alias.name
            self.imported_names[local] = f"{module}.{alias.name}" if module else alias.name
        self._add_import(module or ".", node.lineno)
        self.generic_visit(node)

    def _add_import(self, module: str, line: int) -> None:
        """Record an imported module as an EXTERNAL node.

        Every import becomes a node even when the target is a first-party
        module. The resolver in `ingest_repo` rewrites first-party targets to
        their real MODULE node afterwards, once every file has been seen —
        resolving during the walk would fail for anything not yet parsed.
        """
        target = GraphNode.make_id(NodeKind.EXTERNAL, module)
        self.nodes.append(
            GraphNode(
                id=target,
                kind=NodeKind.EXTERNAL,
                name=module.split(".")[-1],
                qualname=module,
                metadata={"unresolved_module": module},
            )
        )
        self.edges.append(
            GraphEdge(
                source=self.file_id,
                target=target,
                kind=EdgeKind.IMPORTS,
                # Starts unresolved even though `target` is a real node id.
                # `_resolve` short-circuits on `resolved`, so leaving the
                # default True here would mean no first-party import ever got
                # rewritten to the file that actually defines the module.
                resolved=False,
                # The bare module name, kept separately from `target` because
                # `target` is already a synthesized EXTERNAL node id. The
                # resolver needs the name to look the module up among
                # first-party files; without this it can only ever see
                # "external:b" and no first-party import would ever resolve.
                metadata={"module": module},
                line=line,
            )
        )

    # --- definitions -----------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = ".".join([*self._scope, node.name])
        node_id = GraphNode.make_id(NodeKind.CLASS, self.rel_path, qualname)

        self.nodes.append(
            GraphNode(
                id=node_id,
                kind=NodeKind.CLASS,
                name=node.name,
                qualname=qualname,
                path=self.rel_path,
                line=node.lineno,
                end_line=getattr(node, "end_lineno", None),
                signature=f"class {node.name}({', '.join(_base_names(node))})",
                docstring=(ast.get_docstring(node) or "")[:500],
            )
        )
        self.edges.append(GraphEdge(source=self.file_id, target=node_id, kind=EdgeKind.CONTAINS))
        self.defined[qualname] = node_id
        self.defined.setdefault(node.name, node_id)

        for base in _base_names(node):
            self.edges.append(
                GraphEdge(
                    source=node_id,
                    target=base,
                    kind=EdgeKind.INHERITS,
                    resolved=False,
                    line=node.lineno,
                )
            )

        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_callable(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_callable(node)

    def _visit_callable(self, node) -> None:
        qualname = ".".join([*self._scope, node.name])

        # A test is a function named test_* in a test file. Both halves
        # matter: a helper called `test_helper` in src/ is not a test, and a
        # fixture in a test file is not one either.
        if self.is_test_file and node.name.startswith("test_"):
            kind = NodeKind.TEST
        elif self._scope:
            kind = NodeKind.METHOD
        else:
            kind = NodeKind.FUNCTION

        node_id = GraphNode.make_id(kind, self.rel_path, qualname)
        self.nodes.append(
            GraphNode(
                id=node_id,
                kind=kind,
                name=node.name,
                qualname=qualname,
                path=self.rel_path,
                line=node.lineno,
                end_line=getattr(node, "end_lineno", None),
                signature=_signature(node),
                docstring=(ast.get_docstring(node) or "")[:500],
                metadata={"is_async": isinstance(node, ast.AsyncFunctionDef)},
            )
        )

        parent = (
            GraphNode.make_id(NodeKind.CLASS, self.rel_path, ".".join(self._scope))
            if self._scope
            else self.file_id
        )
        self.edges.append(GraphEdge(source=parent, target=node_id, kind=EdgeKind.CONTAINS))

        self.defined[qualname] = node_id
        self.defined.setdefault(node.name, node_id)

        previous = self._current_callable
        self._current_callable = node_id
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()
        self._current_callable = previous

    # --- calls -----------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name and self._current_callable:
            # Recorded unresolved; `ingest_repo` resolves what it can once the
            # whole repository has been seen. Never dropped -- see the module
            # docstring.
            self.edges.append(
                GraphEdge(
                    source=self._current_callable,
                    target=name,
                    kind=EdgeKind.CALLS,
                    resolved=False,
                    line=node.lineno,
                    metadata={"raw_name": name},
                )
            )
        self.generic_visit(node)


def _base_names(node: ast.ClassDef) -> list[str]:
    return [name for name in (_call_name(base) for base in node.bases) if name]


def _call_name(expr) -> str:
    """Best-effort dotted name for a call target or base class.

    Returns "" for anything computed (a call returning a callable, a
    subscript). Those are real, but there is no name to resolve, and inventing
    one would put a fictional edge in the graph.
    """
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        prefix = _call_name(expr.value)
        return f"{prefix}.{expr.attr}" if prefix else expr.attr
    return ""


def _signature(node) -> str:
    """Render a callable's parameter list, without evaluating annotations."""
    args = node.args
    parts: list[str] = []

    for arg in [*getattr(args, "posonlyargs", []), *args.args]:
        parts.append(_render_arg(arg))
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    for arg in args.kwonlyargs:
        parts.append(_render_arg(arg))
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")

    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{node.name}({', '.join(parts)}){returns}"


def _render_arg(arg: ast.arg) -> str:
    return f"{arg.arg}: {ast.unparse(arg.annotation)}" if arg.annotation else arg.arg


def module_qualname(rel_path: Path) -> str:
    """Dotted module name for a path, dropping a leading `src/`."""
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def ingest_repo(
    root: Path,
    store: GraphStore,
    force: bool = False,
) -> IngestReport:
    """Walk `root`, parse its Python, and write the graph into `store`.

    Unchanged files are skipped by content hash. Files that have disappeared
    since the last run have their nodes removed, so the graph never asserts
    the existence of something that is gone.
    """
    started = time.monotonic()
    root = Path(root).resolve()
    report = IngestReport()

    all_files = walk_repo(root)
    report.files_scanned = len(all_files)

    nested_projects = find_nested_projects(root, all_files)

    seen_paths: set[str] = set()
    # Populated as files are parsed, then used to resolve cross-file calls.
    definitions: dict[str, list[str]] = {}
    pending: list[tuple[ModuleExtractor, str]] = []

    for path in all_files:
        rel = str(path.relative_to(root))
        seen_paths.add(rel)

        if path.suffix != ".py":
            report.skipped[rel] = f"not Python ({path.suffix or 'no extension'})"
            continue

        owner = next((p for p in nested_projects if p in path.parents), None)
        if owner is not None:
            report.skipped[rel] = f"belongs to nested project {owner.relative_to(root)}/"
            report.files_vendored += 1
            continue

        report.files_eligible += 1

        try:
            size = path.stat().st_size
        except OSError as exc:
            report.skipped[rel] = f"unreadable: {exc}"
            continue

        if size > MAX_FILE_BYTES:
            report.skipped[rel] = f"too large ({size:,} bytes)"
            continue

        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report.skipped[rel] = f"unreadable: {exc}"
            continue

        digest = content_hash(source)
        if not force and store.file_hash(rel) == digest:
            report.files_unchanged += 1
            # Its definitions still have to be registered, or a call from a
            # file that DID change could not resolve into it.
            for node in store.nodes_in_file(rel):
                if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
                    definitions.setdefault(node.name, []).append(node.id)
            continue

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            report.skipped[rel] = f"syntax error at line {exc.lineno}"
            continue

        store.forget_file(rel)

        qualname = module_qualname(path.relative_to(root))
        extractor = ModuleExtractor(rel, qualname, is_test_path(path.relative_to(root)))

        file_node = GraphNode(
            id=GraphNode.make_id(NodeKind.FILE, rel),
            kind=NodeKind.FILE,
            name=path.name,
            qualname=qualname,
            path=rel,
            docstring=(ast.get_docstring(tree) or "")[:500],
            metadata={"is_test": extractor.is_test_file, "bytes": size},
        )
        extractor.nodes.append(file_node)
        extractor.visit(tree)

        for name, node_id in extractor.defined.items():
            definitions.setdefault(name, []).append(node_id)

        pending.append((extractor, rel))
        store.record_file(rel, digest, datetime.now(timezone.utc).isoformat())
        report.files_ingested += 1

    # Files that vanished since the last run.
    for gone in store.known_files() - seen_paths:
        store.forget_file(gone)

    # Second pass: resolve now that every definition in the repo is known.
    module_ids = {
        node.qualname: node.id for node in store.all_nodes(NodeKind.FILE) if node.qualname
    }
    for extractor, _rel in pending:
        module_ids.setdefault(extractor.module_qualname, extractor.file_id)

    for extractor, _rel in pending:
        store.add_nodes(extractor.nodes)
        resolved_edges = [
            _resolve(edge, extractor, definitions, module_ids) for edge in extractor.edges
        ]
        store.add_edges(resolved_edges)
        report.nodes += len(extractor.nodes)
        report.edges += len(resolved_edges)
        report.unresolved_edges += sum(1 for edge in resolved_edges if not edge.resolved)

    # An EXTERNAL node is created for every import before anything is known
    # about whether the module is first-party. Once resolution has pointed
    # those edges at real files, the placeholders are unreferenced and would
    # otherwise show up in stats as third-party dependencies that do not exist.
    _drop_orphan_externals(store)

    # A test file's calls into first-party code are what it exercises.
    _link_tests(store)

    report.duration_seconds = round(time.monotonic() - started, 3)
    return report


def _resolve(
    edge: GraphEdge,
    extractor: ModuleExtractor,
    definitions: dict[str, list[str]],
    module_ids: dict[str, str],
) -> GraphEdge:
    """Try to point an edge at a real node. Leaves it unresolved if it cannot.

    Resolution order is narrowest-scope-first, which is how Python itself
    resolves names: the same module, then something imported, then any unique
    definition in the repository. An ambiguous name — three classes called
    `Store` — is deliberately left unresolved rather than guessed at, because a
    wrong edge is worse than a missing one for everything built on top.
    """
    if edge.resolved:
        return edge

    raw = edge.target

    if edge.kind is EdgeKind.IMPORTS:
        module = edge.metadata.get("module", "")
        target_id = module_ids.get(module)
        if target_id is None and module:
            # `from pkg.core import X` names a module that is a file; `import
            # pkg` names a package whose file is `pkg/__init__.py`. Try the
            # parent so both shapes resolve to a first-party file.
            target_id = module_ids.get(module.rsplit(".", 1)[0])
        if target_id:
            return edge.model_copy(update={"target": target_id, "resolved": True})
        return edge

    # A dotted call: only the final attribute is a name we could know.
    base = raw.split(".")[0]
    tail = raw.split(".")[-1]

    if raw in extractor.defined:
        return edge.model_copy(update={"target": extractor.defined[raw], "resolved": True})
    if tail in extractor.defined:
        return edge.model_copy(update={"target": extractor.defined[tail], "resolved": True})

    imported = extractor.imported_names.get(base) or extractor.imported_names.get(raw)
    if imported:
        candidates = definitions.get(imported.split(".")[-1], [])
        if len(candidates) == 1:
            return edge.model_copy(update={"target": candidates[0], "resolved": True})

    candidates = definitions.get(tail, [])
    if len(candidates) == 1:
        return edge.model_copy(update={"target": candidates[0], "resolved": True})

    return edge


def _drop_orphan_externals(store: GraphStore) -> None:
    """Remove EXTERNAL placeholders that nothing points at any more."""
    for node in store.all_nodes(NodeKind.EXTERNAL):
        if not store.edges_to(node.id):
            store.conn.execute("DELETE FROM nodes WHERE id = ?", (node.id,))
    store.conn.commit()


def _link_tests(store: GraphStore) -> None:
    """Add TESTS edges from test nodes to what they call.

    Derived from resolved CALLS edges rather than from naming convention.
    `test_prune_drops_duplicates` might exercise `ContextPipeline.run`, and no
    amount of string matching on the test's name would find that.
    """
    edges: list[GraphEdge] = []
    for test in store.all_nodes(NodeKind.TEST):
        for call in store.edges_from(test.id, EdgeKind.CALLS):
            if not call.resolved:
                continue
            target = store.get_node(call.target)
            if target is None or target.kind is NodeKind.TEST:
                continue
            edges.append(
                GraphEdge(source=test.id, target=target.id, kind=EdgeKind.TESTS, line=call.line)
            )
    if edges:
        store.add_edges(edges)
