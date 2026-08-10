# ADR-0007: Backtick-only claim extraction, and heuristics that say they are heuristics

- **Status**: Accepted. Measured in
  [ADR-0018](0018-consistency-engine-first-measurement.md), which found
  100% recall on invented symbol names and three real bugs in this design.
- **Date**: 2026-08-09
- **Context**: Phase 3 of the harness (ADR-0005), built directly on the graph
  from ADR-0006.

## Context

The Consistency Engine checks what an agent says against what the graph and
memory actually contain: hallucinated symbols, invented relationships, and
proposals that resemble a decision already rejected. Two design questions
dominate: how to pull a "claim" out of free text without a model, and how to
report a check whose answer is inherently uncertain without it being mistaken
for one that isn't.

## Decision 1: extraction fires only on backtick-quoted spans

Parsing arbitrary prose for assertions needs either a model or a lot of
guessing — exactly the two things Phase 1 through 3 have avoided everywhere
else. The alternative used here leans on a convention that already exists:
Claude Code, Codex and Cursor all format code references in markdown
backticks. `extract_claims` recognizes three shapes inside backtick spans
(a dotted/underscored identifier, a file path, an explicit relation phrase
like `` `A` calls `B` ``) and nothing outside them.

This is a stated under-approximation, the same trade T3 made for formal
verification: a claim the extractor cannot categorize is simply not checked,
rather than forced into the nearest kind and checked wrongly. An eager
extractor that invents claims out of ordinary prose would "catch"
contradictions that were never actually asserted, which is worse than missing
some real ones.

**Bug found by testing this against real, human-shaped sentences**: the first
version of the relation regex made backticks around the *subject* optional, to
allow "the `GraphStore` class inherits from `Base`" to bind `class` — the bare
English word sitting between the backtick span and the verb — as the subject,
instead of `GraphStore`. Requiring backticks on both subject and target (with
a short whitelist of intervening nouns: class/function/method/object) fixed
it, and is now the same discipline the rest of the module already applied to
bare symbol and file claims.

Two relation verbs are recognized: `calls` and `inherits from` / `extends`,
because each maps unambiguously to one graph edge kind. `depends on` and
`uses` are deliberately left unrecognized — they could mean CALLS, IMPORTS, or
nothing checkable at all, and ADR-0006 already established that a wrong edge
is worse than a missing one.

## Decision 2: unresolved graph edges produce UNVERIFIABLE, never a guess

A relation claim whose target matches an edge the ingester deliberately left
unresolved (an ambiguous call — see ADR-0006) is reported `UNVERIFIABLE`, not
`CONTRADICTED`. The graph's own caution about not guessing at ambiguous names
would be undone if the layer built on top of it started guessing on its
behalf.

## Decision 3: decision resurfacing is a heuristic, and is never allowed to look
like a lookup

Checking whether agent text resembles a `REJECTED` or `SUPERSEDED` decision is
lexical overlap (BM25, reused from `context/rank.py`), scored against the
best-matching decision in a small corpus. This can be wrong in both
directions — a coincidental shared phrase, or a genuine resurfacing worded
differently enough to score low.

Two things enforce that this uncertainty is visible rather than laundered:

- Confidence is capped at 0.85, always, regardless of how high the raw score
  is. A symbol-existence check can legitimately report 1.0 because it is a
  lookup; a lexical-overlap heuristic must never claim the same certainty.
- It gets its own `ClaimKind.DECISION_ALIGNMENT` rather than reusing
  `SYMBOL_EXISTS` as a convenient carrier for the flagged decision's
  statement. An early draft did exactly that, and it would have made a report
  mislabel a resurfacing warning as a claim about a symbol.

## Consequences

- Zero new dependencies; `bm25_rank` was already written for Phase 1.
- A claim extraction pass over this repository's own PR descriptions and
  agent transcripts would need to be re-evaluated periodically as the
  backtick convention drifts — if an agent starts formatting code without
  backticks, extraction silently sees nothing, which is the safe failure mode
  but is also invisible unless someone checks `claims_extracted`.
- `check_decision_resurfacing` runs in `O(claims × decisions)` per check —
  fine for the scale `.verity/` operates at (a project accumulates decisions
  in the hundreds, not millions), and the moment that stops being true this
  function is the one to revisit.
- The engine composes cleanly with what exists: `verity check` and
  `check_claims` (MCP) both degrade gracefully with a stated reason when the
  graph has not been built or `.verity/` does not exist, rather than failing
  outright or reporting a false clean bill of health.
