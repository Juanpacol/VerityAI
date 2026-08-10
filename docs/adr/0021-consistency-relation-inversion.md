# ADR-0021: `check_symbol_calls_file` asserted a hallucination it was built to catch

- **Status**: Accepted
- **Date**: 2026-08-10
- **Context**: An external audit of this project's published claims, run
  before planning any new work on top of the Consistency Engine, re-checked
  ADR-0018's own probe against the current code rather than trusting the
  ADR's prose. It reproduced a different result than the one ADR-0018
  reports as final.

## What the audit found

ADR-0018's own fixture and ground truth (`experiments/consistency_pilot_1_hallucination_detection/`)
state plainly: *"Any claim that `apply_tax` calls anything ... — FALSE"*
(`ground_truth.md`). `billing/tax.py`'s `apply_tax` does
`tax_rates.REGION_RATES.get(region, 0.0)` — a dict read, not a call.

Re-running the exact probe ADR-0018 used to demonstrate its fix:

```
$ verity check probe.txt   # `apply_tax` calls `billing/tax_rates.py`
  [OK  ] apply_tax calls billing/tax_rates.py
         'billing/tax.py' imports 'billing/tax_rates.py' (confidence 100%)
```

`SUPPORTED`, at full confidence, for a claim the pilot's own ground truth
calls false. `check_symbol_calls_file` (`consistency/check.py`) treats a
resolved `IMPORTS` edge between the subject's file and the target file as
sufficient evidence for a `calls` relation. It is not: a file can import a
module and never call anything in it — read a constant, read a dict, import
it only for a type annotation. `apply_tax` is exactly that case.

ADR-0018 narrates this as the blind spot being *closed* — the probe
"went from vanishing entirely to reporting `CONTRADICTED` ... to
`SUPPORTED`" once an unrelated ingester bug was also fixed. Nobody re-checked
the final verdict against the ground truth once both fixes landed. The
sequence of individually-reasonable patches (extend the target pattern to
files, add a file-level fallback check, fix the ingester's submodule
resolution) composed into a checker that now *asserts* the exact
hallucination it exists to catch, at the same confidence it uses for a real
edge.

## Why this matters more than an ordinary bug

CLAUDE.md's invariants are built on the T6 finding: "be suspicious of a
checker that has never failed anything." The pre-ADR-0018 behavior — the
claim silently decomposing into two independent, both-true existence checks
and vanishing — was a **silent false negative**: wrong, but at least inert.
The fix converted it into an **asserted false positive**: a verification
tool now actively vouches for a fabricated call relationship. For a
consistency engine whose entire purpose is to contradict false claims, this
is the worst direction a bug can point — worse than the gap it replaced.

## Decision

`check_symbol_calls_file` returns `UNVERIFIABLE`, not `SUPPORTED`, when the
only evidence for a `calls` claim on a file target is a resolved `IMPORTS`
edge:

```python
if any(node.id == target_file.id for node in imports):
    return ClaimCheck(
        claim=claim,
        status=CheckStatus.UNVERIFIABLE,
        confidence=0.0,
        explanation=(
            f"{subject_node.path!r} imports {target_path!r}, but an import does not "
            "confirm a call -- the graph has no line-level evidence that anything in "
            f"{claim.subject!r} actually calls a function from that file, only that "
            "the file is imported"
        ),
    )
```

The absence of any import is left unchanged and stays `CONTRADICTED`: if the
subject's file doesn't import the target file at all, it cannot be calling
something inside it — that direction of evidence is sound. Only the
positive direction (import found → confident `SUPPORTED`) was wrong, because
"imports" and "calls" are different claims and the graph has no edge for the
second one at file granularity.

Regression test added in `test_consistency_check.py`
(`test_an_import_with_no_call_evidence_is_unverifiable`), replacing the test
that had asserted the wrong verdict as correct
(`test_an_actual_import_is_supported`) — the same fixture, a corrected
expectation.

## Consequences

- **The relation blind spot ADR-0018 reported as closed is narrowed, not
  closed.** A `calls`-claim on a file target can now only be confidently
  *denied* (no import at all) or marked `UNVERIFIABLE` (import exists, call
  unconfirmed) — never confidently confirmed. Confirming a real call into a
  specific file would need line-level call-site evidence the graph does not
  carry today (`analysis/facts.py` and the ingester record no line numbers
  for this purpose); that is real future work, not simulated here.
- `docs/MEASUREMENTS.md`'s consistency-pilot section is corrected to match:
  the probe now reports `UNVERIFIABLE`, and the "closed" framing is removed.
- **The lesson generalizes beyond this one function.** Composing several
  individually-reasonable fixes (extend a pattern, add a fallback path, fix
  an unrelated ingester bug) can silently flip a checker's net effect from
  under-approximating to over-approximating, and nothing catches that unless
  the original probe is re-run against its own ground truth after each
  change lands — not just once, at the end of the ADR that introduced it.
  `verity eval` (planned, see the project's implementation plan) is intended
  to make this the kind of thing a re-run catches automatically rather than
  an external audit finding it after the fact.
- No other check function in `consistency/check.py` makes an equivalent
  import-implies-call substitution — `check_symbol_relation`'s direct
  `CALLS`/`INHERITS` edge lookups are unaffected, since those edges are the
  actual claim, not a proxy for it.
