# First real measurement of the Consistency Engine

`docs/BENCHMARK_PROTOCOL.md` said hallucination/contradiction detection was
"additionally blocked on the Consistency Engine existing at all (Phase 3)."
That sentence was stale: `src/verityai/consistency/` already exists, is
tested (`tests/unit/test_consistency_check.py`), and was accepted in
ADR-0007. What was actually missing was the same thing Context Engine
lacked before ADR-0009: a measurement against real, not hand-authored,
input. This experiment is that measurement.

**Status: complete, and it found a real bug**, fixed in
`src/verityai/consistency/check.py` (see `docs/adr/0018-consistency-engine-
first-measurement.md`).

## Part A — inducing real hallucinations, not writing them by hand

Writing false claims by hand would measure the fixture, not the checker —
the same synthetic-fixture trap `BENCHMARK_PROTOCOL.md` warns about
elsewhere. The honest way to get genuine hallucinations: show a real agent
only `billing/invoice.py` (from the pilot 5 fixture, reused here with a
real code graph built via `verity graph build`) and ask it to describe the
full call chain anyway, including functions it cannot see. This reliably
produces a mix of true claims (what it could actually see) and invented
ones (what it guessed about `apply_tax`/`apply_late_fee`'s internals) —
five independent trials, five independently-generated transcripts.

Ground truth (`ground_truth.md`) was written from the real source files
*before* running `verity check` on any transcript, to avoid biasing the
labels: `apply_tax` and `apply_late_fee` call **nothing** internally — they
only read module-level dicts directly. Any claim that either function
"calls" a helper or a file is false, whatever name is used.

### Result

98 claims checked across 5 trials, 23 flagged as contradictions:

| Class | Count | Checker behavior |
|---|---|---|
| Invented helper-function names (`get_tax_rate`, `compute_overdue_penalty`, etc.) | 14 | **100% caught** — every single one flagged `FAIL` |
| Backtick-quoted local variable names (`with_tax`, `days_overdue`) | 8 | Flagged `FAIL` as "no definition found" — **false positives**: these are real names in the real code, just not graph-indexed (the graph tracks functions/classes/files, not local variables) |
| A genuine path inaccuracy (`invoice.py` instead of the real `billing/invoice.py`) | 1 | Correctly caught — a real, if minor, inaccuracy |
| Fabricated relation claims ("`apply_tax` calls into `billing/tax_rates.py`", "`apply_late_fee` calls into `billing/policy.py`") | ~6 across trials | **0% caught — invisible to the checker entirely**, not even counted among the 98 |

The last row is the important finding. Confirmed with isolated probes
(not just inferred from the trial transcripts):

```
`apply_tax` calls `billing/tax_rates.py` to resolve the rate.
```
→ 0 contradictions. Decomposes into two independent, both-true existence
checks (`apply_tax` exists, `billing/tax_rates.py` exists) — the relation
extractor never fires when the "calls" target is a file rather than a
function, regardless of how tightly the claim is phrased.

```
`apply_tax` calls `get_tax_rate` to resolve the rate.
```
→ 1 contradiction, correctly reported as a relation-level failure
(`'apply_tax' exists, but 'get_tax_rate' was not found either`). So a
symbol-to-symbol relation claim, phrased tightly, *is* checked correctly —
the blind spot is specific to symbol-to-file relations and to relation
claims phrased with explanatory text between the verb and its targets.

## Part B — a real bug found in decision resurfacing

A `REJECTED` decision was fabricated (`Apply the late fee using
DEPRECATED_POLICY directly...`), then two independent proposals were
checked: one a genuine paraphrase of the rejected idea (should trigger
resurfacing), one a completely unrelated caching proposal (should not).

**Both triggered resurfacing, at first.** The unrelated proposal "resembled"
the on-file rejected decision because `check_decision_resurfacing`
normalized each BM25 score against the *maximum score among the stored
decisions*, not against an absolute scale. With only one or two decisions
on record — a very common case, not an edge case — the single closest-of-
the-available-candidates decision always normalizes to a perfect 1.0,
regardless of whether it shares any real content with the checked text.
Confirmed directly by adding a second, obviously unrelated rejected
decision ("store passwords in plaintext") and observing *both* decisions
get flagged against a caching proposal that resembles neither.

**Fixed** in `src/verityai/consistency/check.py`: normalize against the
checked text's own best-possible score (itself matched against itself),
not against the in-corpus max. This dropped the unrelated proposal's
confidence from 85% to 16% and the genuine paraphrase's from 85% (an
overstated, always-maximal confidence) to a more proportionate 43% — both
directionally correct, though a small residual false-positive risk remains
for very small decision corpora due to BM25's own IDF instability at that
scale (see the ADR's limitations section). A regression test
(`tests/unit/test_consistency_check.py::test_a_single_rejected_decision_
does_not_swallow_unrelated_text`) locks in the fix; the pre-existing
"unrelated text" test never caught this because its fixture text had zero
token overlap with the stored decision, never exercising the
near-zero-but-nonzero score regime where the bug actually lived.

## Files

- `fixture_repo/` — the pilot 5 `billing/` fixture, reused as-is, with a
  real code graph built on top (`.verity/graph.db`, gitignored).
- `trial_1.txt` .. `trial_5.txt` — the five real, independently-generated
  transcripts from Part A.
- `ground_truth.md` — written before running any checker output.
- `resurfacing_trial/` — the fabricated rejected decision and the two Part
  B proposals.

## Known limitations of this pilot, stated up front

- **One fixture, one function, 5+2 trials.** Same caveat as every prior
  pilot in this project — see `docs/BENCHMARK_PROTOCOL.md`.
- **The relation-extraction blind spot (function-to-file relations) was
  not fixed here.** It's a real, confirmed gap, but fixing the extractor's
  regex to recognize file targets — and to tolerate explanatory text
  between the relation verb and its arguments — is a larger change than
  this pilot's scope; it's documented in ADR-0018 as a finding, not
  patched.
- **The resurfacing fix reduces but does not eliminate the small-corpus
  false-positive risk.** BM25's IDF calculation is inherently unstable with
  1-2 documents; a genuinely bulletproof fix would need a different
  similarity approach for very small decision corpora, which is out of
  scope here.
