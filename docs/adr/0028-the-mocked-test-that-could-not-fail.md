# ADR-0028: The mocked test that could not fail

- **Status**: Accepted
- **Date**: 2026-08-10
- **Context**: `reliability/risk.py` shipped in [ADR-0026](0026-risk-adaptive-verification.md)
  with 21 passing tests. A verification pass ran it against this repository's
  own graph and found it returns a clean-looking `low` verdict for every file
  whenever the caller's path is in any form but the exact one the ingester
  stored. The tests could not have caught it, because they were the only ones
  in the suite built on `MagicMock`.

## The defect

Executed against this repo, ingested:

```
src/verityai/context/prune.py                     -> medium  (35 callers, fan-in 7)
/Users/…/src/verityai/context/prune.py            -> low     ("no graph node found")
./src/verityai/context/prune.py                   -> low     ("no graph node found")
```

`graph/ingest.py:414` stores `str(path.relative_to(root))`. `graph/store.py`'s
`nodes_in_file` matches `WHERE path = ?` — exact string equality. So an
absolute path, or merely a `./` prefix, resolves to zero nodes. Every graph
signal is then unavailable and `classify_file_risk` returns `low`.

Two things make this worse than a missing feature:

1. **The failure is indistinguishable from a clean result.** `low` means "this
   file does not need deep verification." Feed `classify_paths` a list of
   absolute paths — the form `Path.resolve()`, `find`, and most shell
   pipelines produce — and it reports that nothing in the repository warrants
   scrutiny. This is the exact shape of T6's finding, which CLAUDE.md records
   as *be suspicious of a checker that has never failed anything*.
2. **`file_dependencies` shares the trap.** Its FILE node id is derived from
   the path string, so a mismatched form silently returns `{"imports": [],
   "imported_by": []}` — fan-in zero, no error. Fixing only `nodes_in_file`'s
   call site would have left a second silent zero on the same axis.

## Why the tests could not see it

`tests/unit/test_reliability_risk.py` was the only file in 600+ tests to
import `unittest.mock`:

```python
mock_query.store.nodes_in_file.return_value = [mock_node]
```

`MagicMock` returns the configured node for *any* argument. `nodes_in_file("src/x.py")`
and `nodes_in_file("/abs/src/x.py")` are the same call to a mock and different
calls to a database. The 21 tests verified the tiering *arithmetic* — which was
correct — and could express nothing about the lookup, which was not.

This is why CLAUDE.md:201's claim is load-bearing rather than stylistic:
*"Tests use plain objects and `tmp_path`. There is nothing to mock — that is a
property worth protecting when adding engines."* The property was true when
written; the first test file to break it hid a total failure on its first
outing.

## Decision

**State the contract; do not silently normalize.** `classify_file_risk` and
`classify_paths` take an optional `repo_root`:

- relative path → `./` stripped and normalized, then used;
- absolute + `repo_root` → relativized against it;
- absolute, no `repo_root` → returned with a note saying *"the graph stores
  repo-relative paths, so no node can match. The tier below is a non-result,
  not a low-risk verdict."*;
- outside `repo_root` → a note naming the root.

Defaulting to `Path.cwd()` was rejected. It would work from the repository
root and silently produce the original failure from anywhere else, which
trades a visible gap for an invisible one.

**The unresolved note is appended unconditionally**, not only when no other
reason fired. A high-tier file whose graph signals were never available has to
say so, or the reasons list claims more was measured than was. That is the one
behavioural change to an existing path, and it has its own test.

**The tests are rewritten against a real ingested graph**, following
`tests/unit/test_graph_query.py`'s convention. The fixture deliberately
separates signals that are naturally coupled — three callers normally implies
fan-in three — so each branch can be asserted alone:

| file | callers | fan-in | untested | isolates |
|---|---|---|---|---|
| `src/hub.py` | 3 | 1 | 1 | blast radius, not fan-in |
| `src/edges.py` | 2 | 2 | 1 | fan-in, and the `<3` boundary |
| `src/lonely.py` | 0 | 0 | 1 | untested alone |
| `src/plain.py` | 1 | 1 | 0 | the genuine `low` (a real TESTS edge) |
| `src/auth/tokens.py` | 0 | 0 | 1 | a path marker over a real node |

21 tests became 31, and `unittest.mock` is no longer imported anywhere in the
suite.

## Consequences

- Two `GraphQuery`/`GraphStore` docstrings now state the required path form,
  since `risk.py` was the second consumer to hit it and will not be the last.
- Tests that assert an *absence* now assert on the reasons list rather than
  the tier. `src/edges.py` is medium from fan-in, so "two callers do not
  trigger blast radius" cannot be shown by the tier alone — a mock could zero
  the other signals to make the tier prove the point; a real graph cannot.
  This is more honest and slightly less convenient, which is the trade.
- `tests/unit/test_reliability_risk.py` now runs `ingest_repo` per test. It
  costs milliseconds on an 8-file tree.
- **Not fixed here, and now visible:** `_HIGH_RISK_PATH_MARKERS` substring-matches
  the whole path rather than its segments, so `"api"` fires on any path
  containing those three letters — `src/rapid/…`, `src/therapist/…`,
  `src/scrapility/…` all tier *high*. Verified by running `_path_signal`
  over each; no file in this repository happens to trip it, so the defect is
  latent here rather than absent. It is surfaced instead of quietly narrowed
  because choosing between substring and path-segment matching deserves its
  own decision and its own test, and `verity reliability risk` printing the
  matched marker is how a human finds out — the same discipline
  `security.py`'s `RULE_CAVEATS` already applies to its own rules.

## The lesson worth keeping

A mock is a claim that the collaborator's behaviour is uninteresting. Here the
collaborator's behaviour — *exact string equality on a path column* — was the
entire defect. Combined with [ADR-0027](0027-retained-trial-evidence.md),
found in the same pass, the pattern is consistent enough to state plainly:
**a test that cannot fail for the reason the code is wrong is not evidence
that the code is right**, whether what it elides is a hash's meaning or a
database's lookup.
