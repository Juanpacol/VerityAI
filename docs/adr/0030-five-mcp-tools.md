# ADR-0030: five MCP tools, not twenty-one

- **Status**: accepted
- **Date**: 2026-08-10
- **Context**: an audit of `mcp/server.py`, prompted by the question of whether
  the surface was coherent at all.

## What was wrong

The server had grown to 21 tools in a single 589-line `build_server()`. The
audit found four distinct problems, only one of which is about size.

**Duplication that was not visible from any one call site.** `get_state` and
`handoff` were both `build_handoff(store)`, separated by one default argument.
`check_symbol_exists` was a strict subset of `impact_of_changing` — the same
`query.define(name)` opening, with less of the answer. The four `save_*` tools
differed only in which class from `core/models.py` they instantiated.

**A precondition delegated to the model.** Five tools carried a copy of
`if not graph.stats().get("nodes.total")` and told the agent *"call
`build_code_graph` first"*. A mandatory setup step, duplicated five times, whose
enforcement was the model's judgement.

**No naming rule.** Verbs (`save_decision`), questions (`should_recall_memory`,
`risk_of_changing`) and nouns (`handoff`, `snapshot`) coexisted. A client selects
a tool by matching a request against descriptions; three naming schemes are noise
in exactly that channel.

**Documentation that had already drifted.** `README.md` said "Nineteen tools" and
its list omitted the two added by ADR-0025/0026. The only correct inventory was a
set-equality assertion in a test.

## The decision

Five tools, each taking an `op` (or, for `remember`, a `kind`):

| tool | ops |
|---|---|
| `context` | `optimize` · `health` · `recall` |
| `remember` | `decision` · `constraint` · `discovery` · `failure` |
| `session` | `task` · `state` · `handoff` · `snapshot` · `restore` · `list` |
| `code` | `index` · `find` · `define` · `impact` |
| `verify` | `claims` · `security` · `architecture` · `risk` |

The cuts follow the seams already in `CLAUDE.md`: `remember` is the only write
path to `.verity/`; `session` holds everything that mutates task state,
`restore` included; `code` and `verify` separate *what is true of the repo*
(`graph/`) from *what contradicts your claim* (`consistency/` + `reliability/`).

The reason is not that 21 is untidy. A client spends context on every tool
description it holds, and it selects by matching against them — so a long
surface spends the budget that should have gone to *when to call this*. Five
descriptions with a per-op block carry **more** guidance in **less** text than
21 descriptions that each had to re-establish their own context.

### Why `Literal[...]`

Pydantic renders `Literal["find", "define", ...]` as a JSON-Schema `enum` — what
the model actually reads — and rejects an illegal value before the body runs,
with an error naming the legal set. An `Enum` class or a `str` plus hand-written
validation both cost code to produce a worse schema.

### Why flat optional parameters, not `args: dict`

A `dict` parameter collapses the schema to `{"type": "object"}` and erases every
parameter name. That destroys the discoverability this change exists to protect.
A missing required argument is answered with a *sentence* naming it
(`code(op="define") needs \`name\`.`), not raised as a transport error — an agent
can act on the first and mostly cannot act on the second, and the house already
answers this way (`"No paths given. Pass the files you are about to change."`).

### Why `code` still refuses instead of auto-indexing

The tempting fix for five copies of "call `build_code_graph` first" is to just
run the ingest. It is wrong, and the reason is the shape of the case that
triggers it: an empty graph means a **cold, full** index of a repo of unknown
size, inside an MCP call whose client-side timeout the server cannot see. A
`code(op="find")` that hangs for 40s and then dies without explanation is
strictly worse than one that returns an instruction in 5ms. It would also break
the module's own constraint — `verity graph build` does not silently ingest
either. What the change does fix is the duplication: one shared `EMPTY_GRAPH`
message, so the wording cannot drift between the five call sites.

`verify(op="risk")` keeps its own, longer refusal, because it has to explain
something the others do not: with no graph every file tiers `low`, which reads as
"nothing needs scrutiny" when the truth is "nothing was measured" (ADR-0026).

### Why no deprecation aliases

MCP clients re-read `list_tools` on every connect and no external integration is
pinned to the old names. Registering 21 aliases would hand back precisely the
description budget the change was made to recover. This ships as a breaking
change; the mapping is below.

## Also fixed here

`_store()` resolved the project root from the server process's working directory
and created `.verity/` there when discovery failed. Its `root` parameter existed
but no tool ever passed it. If a client launched `verity-mcp` from a home
directory, state was written to the wrong place, silently. `verity-mcp --root`
(falling back to `$VERITY_ROOT`, then the cwd) now threads a root through
`build_server` to `handlers.store_at`. The auto-`init` divergence from the CLI
stays — it is deliberate, and an agent cannot usefully react to "run init first"
— but it now happens somewhere chosen rather than somewhere inherited.

## Deliberately not done

Unifying the MCP and CLI renderers. The prune ledger differs in format between
the two (`cli/main.py` is columnar), so unifying them changes user-visible CLI
output — a separate decision. The `define` / `impact` / `risk` / `recall`
renderers are MCP-only and have no CLI counterpart to share with, so extracting
them would move code sideways. Exactly one extraction was in scope, because this
change created it: the shared empty-graph guard.

## Migration

| old | new |
|---|---|
| `optimize_context(transcript, task, budget)` | `context(op="optimize", …)` |
| `context_health(transcript)` | `context(op="health", …)` |
| `should_recall_memory(task, context_sample)` | `context(op="recall", …)` |
| `save_decision(statement, why)` | `remember(kind="decision", …)` |
| `save_constraint(statement, hard)` | `remember(kind="constraint", …)` |
| `save_discovery(statement)` | `remember(kind="discovery", …)` |
| `save_failure(attempted, error)` | `remember(kind="failure", statement=…, why=…)` |
| `set_task(title, description, next_action)` | `session(op="task", …)` |
| `get_state()` | `session(op="state")` |
| `handoff(budget)` | `session(op="handoff", …)` |
| `snapshot(label)` | `session(op="snapshot", …)` |
| `restore(number)` | `session(op="restore", …)` |
| `list_snapshots()` | `session(op="list")` |
| `build_code_graph(force)` | `code(op="index", …)` |
| `find_relevant_code(task, limit)` | `code(op="find", …)` |
| `check_symbol_exists(name)` | `code(op="define", …)` |
| `impact_of_changing(name)` | `code(op="impact", …)` |
| `check_claims(text)` | `verify(op="claims", …)` |
| `check_security()` | `verify(op="security")` |
| `check_architecture()` | `verify(op="architecture")` |
| `risk_of_changing(paths)` | `verify(op="risk", …)` |

Only `save_failure` changes parameter names: `attempted` → `statement`,
`error` → `why`, so that all four kinds of record share one signature.

## Consequences

The risk this surface introduces is new and specific: twenty-one descriptions
could not hide a tool, but five can hide an enum member added without its
guidance. `test_every_op_is_documented` reads each tool's `op`/`kind` enum out of
the generated `inputSchema` and asserts every value appears in the description —
so an undocumented op fails the suite rather than shipping silently.

`server.py` is now the surface and nothing else (~300 lines of mostly
description); `handlers.py` holds one function per operation. Changing what a
tool *says* and changing what it *does* are now edits to different files.
