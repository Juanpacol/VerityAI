# ADR-0018: First real measurement of the Consistency Engine -- and a real bug it found

- **Status**: Accepted
- **Date**: 2026-08-09
- **Context**: `docs/BENCHMARK_PROTOCOL.md` describes hallucination and
  contradiction detection as blocked on "the Consistency Engine existing at
  all (Phase 3)." Before planning that as new work, the actual state of
  `src/verityai/consistency/` was checked: the engine already exists,
  already has tests, and was accepted in ADR-0007. The real gap was a
  measurement against genuine (not hand-authored) input — the same gap
  Context Engine had before ADR-0009.

## Part A: inducing real hallucinations

Writing false claims by hand to test the checker would measure the
fixture, not the checker — the synthetic-fixture trap this project has
avoided since ADR-0009. Instead, five independent live-agent trials were
shown only `billing/invoice.py` (from the pilot 5 fixture, with a real
code graph built via `verity graph build`) and asked to describe the full
call chain anyway, including functions they could not see. Ground truth was
written from the real source files before running any checker output.

### Result

98 claims checked, 23 flagged:

| Class | Count | Outcome |
|---|---|---|
| Invented helper-function names | 14 | 100% caught |
| Backtick-quoted local variable names (`with_tax`, `days_overdue`) | 8 | False positives — real names, just not graph-indexed |
| A genuine path inaccuracy | 1 | Correctly caught |
| Fabricated function-to-file relation claims | ~6 | **0% caught — structurally invisible**, not even counted among the 98 |

Confirmed with isolated probes, not just inferred: `` `apply_tax` calls
`billing/tax_rates.py` `` produces zero contradictions (decomposes into two
independent, both-true existence checks), while `` `apply_tax` calls
`get_tax_rate` `` — an equally fabricated relation but with a function-shaped
target — is correctly caught as a relation-level failure. The relation
extractor (`consistency/claims.py`) only recognizes symbol-to-symbol
`calls`/`inherits from`/`extends` phrases; a claim of a function calling
into a *file* — a very natural way to describe a module dependency — never
parses as a relation claim at all.

## Part B: a real bug in decision resurfacing

A `REJECTED` decision was fabricated, then two proposals were checked: a
genuine paraphrase of it (should trigger resurfacing) and a completely
unrelated one (should not). **Both triggered resurfacing.**

Root cause, in `check_decision_resurfacing`
(`src/verityai/consistency/check.py`): each candidate decision's BM25 score
was normalized against `max(scores.values())` — the maximum *among the
stored decisions*, not an absolute scale. With one or two decisions on
record (not an edge case — the common case for an early or solo project),
whichever decision is relatively closest to the checked text always
normalizes to a perfect 1.0, regardless of whether it shares any real
content with it. Confirmed directly: adding a second, obviously unrelated
rejected decision ("store passwords in plaintext") caused *both* to be
flagged against a caching proposal that resembled neither.

### Fix

Normalize against the checked text's own best-possible score (BM25-matched
against itself) instead of the in-corpus max:

```python
_, self_scores = bm25_rank(text, [text])
self_score = self_scores.get(0, 0.0)
if self_score <= 0:
    return []
...
normalized = score / self_score
```

This dropped the unrelated proposal's confidence from 85% (always-maximal,
regardless of content) to 16%, and the genuine paraphrase's from an
identically-maximal 85% to a more proportionate 43% — both directionally
correct. A regression test was added
(`test_a_single_rejected_decision_does_not_swallow_unrelated_text`); the
pre-existing "unrelated text" test never caught this bug because its
fixture text had zero token overlap with the stored decision, never
exercising the near-zero-but-nonzero BM25 score regime where the bug
actually lived — a reminder that a test suite's own fixtures can share the
synthetic-fixture trap's blind spots.

## Consequences

- `docs/BENCHMARK_PROTOCOL.md`'s "blocked on Consistency Engine existing"
  language is now corrected — the engine exists, has a first real
  measurement, and that measurement found and fixed a genuine bug, the same
  pattern ADR-0009 and ADR-0016 established for other engines.
- **Symbol-existence checking has a clean, confirmed 100% recall** on
  invented function names in this pilot — the engine's strongest result so
  far.
- **The function-to-file relation blind spot is fixed** (same-day
  follow-up): `claims.py`'s relation target pattern now accepts file paths
  (`[\w./-]+` instead of `[\w.]+`), and `check.py` gained
  `check_symbol_calls_file`, which checks a file-targeted relation claim
  against the file-level `IMPORTS` graph instead of a symbol-level `CALLS`
  edge. Verified against the exact probe this ADR used to demonstrate the
  gap: `` `apply_tax` calls `billing/tax_rates.py` `` now reports
  `CONTRADICTED` ("the file defining 'apply_tax' does not import
  'billing/tax_rates.py'") instead of silently vanishing into two
  independent existence checks. Regression tests added in both
  `test_claims.py` and `test_consistency_check.py`
  (`TestSymbolCallsFileRelation`). One residual, honestly-stated limitation:
  the fix's accuracy is bounded by how completely the ingester resolves
  imports — `from package import submodule`-style imports were observed, in
  the real `billing/tax.py` fixture, to register only an edge to the
  package's `__init__.py`, not to the submodule file. That is a pre-existing
  ingester limitation, not introduced by this fix, and is not addressed
  here. The extractor's other named gap (tolerating explanatory text
  between the relation verb and its arguments, e.g. "likely calls a helper
  in") remains open, deliberately out of scope for this pass.
- **Backtick-quoted local variable names — addressed, deliberately without
  changing any verdict** (same-day follow-up). Investigating a fix
  surfaced a harder fact: a real local variable name (`with_tax`) and a
  real hallucinated function name (`get_tax_rate`) are lexically
  identical — plain snake_case, no dot, no parens, no capital letter. All
  14 invented names this pilot caught had exactly that shape. Any
  heuristic that softened the verdict or confidence for that shape would
  have silently cost the 14/14 recall this ADR reports. Instead,
  `check_symbol_exists` now adds an honest caveat to the *explanation*
  when a missed symbol has no marker distinguishing it from a plain
  variable name (`looks_like_bare_name` in `claims.py`) — status and
  confidence are unchanged, `CONTRADICTED` at 1.0 either way. The caveat
  necessarily appears on both `with_tax` and `get_tax_rate`, since nothing
  in the text tells them apart; this is stated plainly rather than
  pretended away. Regression tests confirm a clearly code-shaped miss
  (`TotallyMadeUpClass`) gets no caveat, since there the evidence really is
  as strong as the confidence claims.
- **The resurfacing fix is a real improvement, not a complete solution.**
  BM25's IDF is inherently unstable with 1-2 documents; a small residual
  false-positive risk remains for very small decision corpora. Stated
  honestly rather than oversold, per this project's standing practice (T1).
