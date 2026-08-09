# ADR-0006: SQLite for the code graph, and declared ingestion scope

- **Status**: Accepted
- **Date**: 2026-08-09
- **Context**: Phase 2 of the harness (ADR-0005)

## Context

The Knowledge Graph has to answer "what code is relevant to this task" by
relationship, not only by text similarity — that is the claim that makes it
worth building rather than pointing an embedding model at the repository.

Three choices had to be made: where the graph lives, what gets ingested, and
what happens to structure the parser cannot resolve.

## Decision 1: SQLite, with traversals written by hand

Neo4j was already in the pre-pivot `docker-compose.yml` and was the obvious
continuation. Rejected on adoption grounds: a harness whose first instruction
is "start a graph database" will not be installed, and the whole premise of
this pivot is a tool that sits quietly beside whatever agent someone already
uses. `sqlite3` is in the standard library, the graph is one file inside
`.verity/`, and there is nothing to run.

networkx was rejected too, for a smaller reason: it would have been the first
new runtime dependency since the pivot, in exchange for traversals that are
about twenty lines each. Writing them by hand also let them be shaped to the
problem — `cycles()` returns the *path* of a circular import rather than a
boolean, because "there is a circular import somewhere" is not actionable.

Cycle detection is iterative DFS with an explicit stack rather than recursion.
A deep import chain would otherwise risk `RecursionError` on precisely the
pathological codebase where the check is most worth running.

Neo4j remains a plausible optional backend. `query.py`'s surface is narrow
enough to reimplement if graph size ever demands it.

## Decision 2: incrementality keyed on content hash, not mtime

A checkout, a rebase, or a stray `touch` all change mtime without changing
content. Re-parsing a thousand unchanged files because git updated a timestamp
is the difference between a tool someone runs constantly and one they avoid.

The corollary matters as much: when a file changes, its old nodes are deleted
before the new ones are written, and a file that disappears has its nodes
removed entirely. Without that, deleting a function would leave its node in the
graph forever — the graph would assert the existence of something that is gone,
which is exactly the failure the Consistency Engine exists to catch, committed
by the harness itself.

## Decision 3: declared scope, not implied coverage

Python only, for now. Every other file is recorded in `IngestReport.skipped`
with a reason. This is the `NOT_VERIFIED` discipline from ADR-0001 carried
across the pivot: "we did not read this" and "this is fine" must never look
alike.

Two refinements came from running the ingester on this repository:

**The coverage denominator.** The first version divided files-in-graph by every
file in the tree and reported *4% coverage* — technically true, wildly
misleading, since every Python file had in fact been ingested and the other
1,266 files were JSON evidence records it was never going to read. Coverage is
now relative to *eligible* files, with out-of-scope files reported separately.

**Nested projects.** `research/truthfulqa/` is a cloned reference
implementation living in the working tree. Ingesting it made the graph report
`numpy` and `neo4j` as dependencies of a project that has neither, and made
"where is X defined" ambiguous between this codebase and somebody else's. Any
subdirectory carrying its own `pyproject.toml` / `setup.py` / `Cargo.toml` is
now treated as a separate project and excluded — counted apart from non-Python
files, because "we do not read Rust" and "we chose not to read this Python" are
different facts.

## Decision 4: unresolved edges are kept, never dropped

Python is dynamic. A name at a call site may be a local, an import, a method on
an inferred type, or built at runtime. The resolver tries same-module, then
imported, then any *unique* definition in the repository — and stops there. An
ambiguous name (three classes called `Store`) is deliberately left unresolved
rather than guessed at, because a wrong edge is worse than a missing one for
everything built on top.

Calls that cannot be resolved are recorded with `resolved=False` and their raw
name preserved. Dropping them would discard precisely the signal Phase 3 needs:
a call to something that exists nowhere in the repository is what a
hallucinated API looks like from the graph's side.

The cost is a number that must always carry a caveat. `untested()` can only see
resolved call edges, so code exercised indirectly — a CLI command driven
through `runner.invoke`, framework dispatch, a method on an untyped local —
appears untested when it is not. `untested_caveat()` exists so that number is
never displayed bare, and the CLI prints it every time. This is the same rule
that came out of T1: a plausible-looking number nobody can audit will be
believed, and then it will be wrong.

## Consequences

- Zero new runtime dependencies. The core is still pydantic, typer, rich,
  python-dotenv.
- The graph is a file a developer can open with any SQLite browser.
- Ingesting this repository: 50 files, ~740 nodes, ~3,700 edges, 0.3 seconds.
- ~35% of call edges are unresolved, almost all builtins and methods on locals.
  That is expected, reported, and Phase 3's filtering problem.
- Non-Python codebases get nothing from Phase 2 and are told so plainly rather
  than shown an empty graph.
