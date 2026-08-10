# Arbitrary-tiebreak pilot: the ceiling finally breaks

Per `docs/BENCHMARK_PROTOCOL.md`'s Family B procedure. Pilots 4-7 (ADR-0015,
0016, 0017, 0019) all ceilinged on success — even pilot 7's genuinely
code-unresolvable ambiguity produced 10/10 identical answers, because the
specific ambiguity chosen ("grace period") carried a strong linguistic
convention. This pilot removes that convention entirely.

**Status: complete, and this is the first pilot in the series where
success itself splits between conditions.** 5/5 `naive` trials get the
answer wrong; 5/5 `verity` trials get it right. `likely_real_difference`.

## The design

`fixture_repo/allocation/pick_winner.py`'s bug is a one-line no-op (always
returns the first candidate) — trivial to spot, not the point. The real
question: when two candidates tie on `score`, which one wins? Nothing in
the domain suggests an answer — candidates are identified by opaque
numeric `id`s, with no suggestive naming anywhere (unlike pilot 7's "grace
period," which reads as inclusive by convention).

The visible test uses candidates with clearly different scores, so any
correct-looking comparison passes it regardless of tie-break behavior. The
hidden test (never shown to any trial) uses two candidates **tied on
score**, with the lower-`id` candidate listed **second** in the input —
deliberately, because `max(candidates, key=lambda c: c["score"])`, the
laziest fix that still looks entirely correct, returns Python's
first-seen maximum on a tie: the higher-`id` candidate here, which is
wrong per the fabricated policy ("on a tie, the lower id wins"). Getting
this right requires having actually considered that ties exist, not just
writing a plausible one-liner.

## Phase A, conditions, scoring

Same shape as pilots 4-7 (`../lib/setup_phase_a.sh`): `naive` gets the bare
task and no `.verity/`; `verity` gets a `.verity/` pre-loaded with the
fabricated decision, which explicitly states the tie-break rule and warns
that `max()`'s default behavior does not reliably match it. 5 live-agent
trials per condition, Bash access, constrained not to touch `tests/`.

Two metrics, both scored independently by the harness:
- `visible_pass`: does the shown test pass? (expected ~10/10, a sanity
  check, not the finding)
- `tie_correct`: does `pick_winner` return the lower-`id` candidate on the
  hidden tie case? This is the metric that matters.

## Result

| Metric | naive | verity | Verdict |
|---|---|---|---|
| `visible_pass` (5 trials) | 5/5 | 5/5 | ceiling, as designed |
| `tie_correct` (5 trials) | 0/5 | 5/5 | `likely_real_difference` |

All 5 `naive` trials wrote `max(candidates, key=lambda c: c["score"])` —
a genuinely reasonable, idiomatic fix that passes the visible test
perfectly and that none of them had any way of knowing was wrong on a tie.
All 5 `verity` trials read the handoff, saw the explicit warning about
`max()`'s tie behavior, and wrote a tie-break comparison instead. Noise
floor `[0.0, 0.0]` for `naive`, between-config mean `1.0` for `verity` —
zero overlap. Reproduce:

```bash
./setup_phase_a.sh
# run each of the 10 trials as a live agent per the design above
verity noise-floor naive_results.json verity_results.json --metric tie_correct
```

## Why this pilot succeeded where pilot 7 didn't

Pilot 7's ambiguity (grace-period boundary inclusivity) had a dominant
natural-language convention strong enough that agents guessed the correct
policy without ever being told it. This pilot's ambiguity has no such
convention: nothing about numeric `id`s or a "highest score wins" rule
suggests which one wins a tie. The result is the first genuine
success-rate split in eight Family B pilots — not because the task got
harder in a way that a longer investigation would close, but because the
correct answer was never inferable from the code or its naming at all,
and here that was actually true rather than only apparently true.

## Known limitations of this pilot, stated up front

- **One fixture, one arbitrary rule, 5 trials per condition.** Same
  caveat as every prior pilot — see `docs/BENCHMARK_PROTOCOL.md` before
  generalizing to "recovery always changes outcome on domain ambiguities."
  It took two attempts (pilot 7, then this one) to find an ambiguity that
  actually worked; a third ambiguity might again have a hidden convention.
- **The `verity` decision was quite explicit** (it names the exact tie-break
  rule and warns against the specific wrong default). This is consistent
  with every prior pilot's phase-A fabrication, but it means this result
  shows recovery helps when the recovered decision directly answers the
  ambiguity — not that recovery helps with vaguer or less complete notes.
- **`visible_pass` and `tie_correct` are perfectly correlated with
  condition here** (ceiling on one, clean split on the other) — a larger N
  would be needed to characterize how consistently this replicates, not
  just whether it can happen at all.
