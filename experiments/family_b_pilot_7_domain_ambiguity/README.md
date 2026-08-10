# Domain-ambiguity pilot: a fifth ceiling, for a genuinely new reason

Per `docs/BENCHMARK_PROTOCOL.md`'s Family B procedure. Pilots 4-6 (ADR-0015,
0016, 0017) all used bugs with **one answer derivable from the code alone**,
and all three ceilinged on success — a capable model, given enough time,
always found it. This pilot changes the shape of the difficulty entirely: a
bug whose correct fix depends on a **business-rule ambiguity the code
cannot resolve at all**, deliberately designed so the one visible test
cannot distinguish the two plausible answers.

**Status: complete, and it is a fifth ceiling — but for a new reason.**
10/10 trials, both conditions, independently converged on the same
(correct) interpretation of an ambiguity that genuinely wasn't in the code.

## The design

`fixture_repo/billing/late_fee.py`'s `apply_late_fee()` is a one-line
no-op — trivial to spot, not the point. The real question: does the exact
day a grace period ends (`days_overdue == grace_days`) still count as
within grace, or is it already overdue? That's a policy choice
(`days_overdue > grace_days` vs `days_overdue >= grace_days`), not
something the surrounding code states anywhere.

The one visible test (`days_overdue=20, grace_days=10`) is unambiguously
overdue under either interpretation — it was deliberately chosen so both
fixes pass it identically. A second, **hidden** test
(`days_overdue == grace_days`, never shown to any trial) is scored
independently by the harness afterward: the fabricated phase-A decision
states the real policy is inclusive (fee only applies when `days_overdue >
grace_days`, strictly), so the correct answer at the boundary is "no fee."

This is structurally different from pilots 4-6: there, a careful-enough
reading of the code always found the one right answer. Here, **no amount
of reading the code finds it** — only the decision (recoverable via
`verity handoff`) or an outside guess does.

## Phase A, conditions, scoring

Same shape as pilots 4-6 (`../lib/setup_phase_a.sh`): `naive` gets the bare
task and no `.verity/`; `verity` gets a `.verity/` pre-loaded with the
fabricated decision stating the inclusive-grace-period policy explicitly.
5 live-agent trials per condition, Bash access, constrained not to touch
`tests/`.

Two metrics, both scored independently by the harness, never from the
agent's own report:
- `visible_pass`: does the shown test pass? (sanity check, expected ~10/10)
- `boundary_correct`: does `calculate_invoice({"subtotal": 1000.0,
  "days_overdue": 10, "grace_days": 10})` return `1000.0` (no fee, the
  correct policy) rather than `1020.0`?

## Result

| Metric | naive | verity | Verdict |
|---|---|---|---|
| `visible_pass` (5 trials) | 5/5 | 5/5 | ceiling, as designed |
| `boundary_correct` (5 trials) | 5/5 | 5/5 | `indistinguishable_from_noise` |

Every one of the 5 `naive` trials independently wrote `days_overdue >
grace_days` (strict) with no prompting toward the boundary question and no
access to the decision — the same choice the `verity` trials made after
reading it explicitly. None discussed the boundary case in their reasoning;
`>` appears to be a strong default convention for "grace period" semantics
regardless of whether the model was told the policy.

**This is a genuinely different finding from pilots 4-6's ceilings.** There,
success ceilinged because the answer was in the code and a careful agent
always found it. Here, the answer was *not* in the code — and the ceiling
happened anyway, because the model's default convention for this
particular kind of naming (`grace_days`, "grace period") happens to align
with the fabricated policy. That is a fact about this specific ambiguity
and this model, not evidence the design failed: a different, less
linguistically-loaded ambiguity (e.g., an arbitrary tie-breaking rule with
no common convention) might show a real split. Reproduce:

```bash
./setup_phase_a.sh
# run each of the 10 trials as a live agent per the design above
verity noise-floor naive_results.json verity_results.json --metric boundary_correct
```

## Known limitations of this pilot, stated up front

- **The chosen ambiguity had a dominant linguistic prior.** "Grace period"
  strongly suggests an inclusive boundary in common usage and probably in
  training data (real billing systems, contract language). A genuinely
  unresolvable ambiguity needs a convention with no dominant default — e.g.
  a tie-breaking rule between two equally common but incompatible
  approaches, or a numeric edge case with no natural-language framing at
  all.
- **This was the cheapest possible test of the design**, not proof the
  mechanism can never show an effect. A harder version of this same idea
  (an ambiguity without a linguistic tell) is the natural next pilot.
- **One fixture, one ambiguity, 5 trials per condition.** Same caveat as
  every prior pilot — see `docs/BENCHMARK_PROTOCOL.md` before generalizing.
