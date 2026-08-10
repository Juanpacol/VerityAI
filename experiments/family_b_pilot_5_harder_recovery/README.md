# Harder-recovery pilot: does a harder bug finally break the success ceiling?

> **Evidence caveat added 2026-08-10 (Phase 0 truth repair):** as with pilot
> 4, the `trials/` directories were later wiped by a re-run of
> `experiments/lib/setup_phase_a.sh` (now guarded, see the script).
> Nothing under `trials/` was git-tracked, so the numbers below are
> **not independently re-derivable** — they rest on the hand-recorded
> results JSON alone, and the tool-call counts were self-reported, not
> captured by any instrumented harness. Treat as unverified history --
> **permanently**. `tool_uses` counts an agent's tool calls, a property of
> behaviour rather than of the code left behind, so no fixture-and-scorer
> harness can regenerate it, `verity eval` included. See
> `experiments/UNREPRODUCIBLE.md`.

Per `docs/BENCHMARK_PROTOCOL.md`'s Family B procedure. Pilot 4
(`experiments/family_b_pilot_4_recovery_after_reset/`, ADR-0015) measured
"recovery after reset" and found a real cost effect but a success-rate
ceiling (10/10 both conditions) — the bug, while harder than the first
Family B pilot's (ADR-0011), still wasn't hard enough to make cold recovery
fail outright. This pilot deliberately raises the difficulty to test
whether success itself, not just cost, ever moves.

**Status: complete, and still a ceiling.** 10 real trials (5 per condition)
on a harder, two-subsystem fixture. Task success: 10/10 both conditions,
`indistinguishable_from_noise` — a third ceiling in this series (after
ADR-0011 and ADR-0015). Tool-call cost: `likely_real_difference`, and a
*larger* gap than pilot 4's (naive floor `[7, 10]` vs verity mean 5.2,
compared to pilot 4's `[5, 8]` vs 4.8).

## The fixture: two subsystems, one healthy, one broken

`fixture_repo/` is a `billing` package with two structurally identical
subsystems feeding `calculate_invoice()`:

- `billing/tax.py` + `billing/tax_rates.py`: **healthy**. `apply_tax()`
  correctly reads `REGION_RATES`. A decoy `LEGACY_REGION_RATES` dict sits
  right next to it, unused — a plausible-looking dead end meant to cost
  real exploration time, not an actual bug.
- `billing/late_fee.py` + `billing/policy.py`: **broken**. `apply_late_fee()`
  reads `policy.DEPRECATED_POLICY` (a superseded, 30-day-grace policy)
  instead of `policy.ACTIVE_POLICY` (10-14 days, current) — the real bug,
  in a file with an exact structural twin (`tax.py`) that is *not* buggy.
- `tests/test_billing.py`: fails on a plain numeric mismatch that names
  neither subsystem.

An earlier draft of `late_fee.py` had an explicit `# BUG: this should read
policy.ACTIVE_POLICY...` comment (left over from writing the fixture) —
found and removed before trusting the first run of all 10 trials, once it
became clear every trial's report referenced "a comment in the source"
pointing straight at the fix. See "A methodological detour" below.

Validated cold before spending trial budget the second time: the corrected
fixture fails without the comment, the real fix passes, and editing the
decoy `tax.py` instead does not make it pass.

## Phase A, conditions, scoring: same as pilot 4

- Phase A is fabricated via `verity task` / `verity remember
  decision/discovery` (`setup_phase_a.sh`), naming the exact root cause and
  file, and explicitly noting that the tax subsystem was checked and is
  healthy — same shape of investigation a careful human would leave behind.
- `naive`: fresh agent, bare "there's a failing test, fix it," no `.verity/`.
- `verity`: same fresh agent, same bare instruction, `.verity/` pre-loaded,
  told to run `verity handoff` first.
- 5 live-agent trials per condition, Bash access, free to explore/edit/run
  pytest, constrained not to touch `tests/`.
- Every trial verified independently: `pytest` run by the harness, plus a
  diff of `tests/test_billing.py` (no tampering) and `billing/tax.py` (no
  agent "fixed" the healthy decoy) against the originals.

## Result

**Primary metric — task success:**

| Condition | 5 trials | Noise floor | Conclusion |
|---|---|---|---|
| `naive` | 5/5 passed | `[1.0, 1.0]` | `indistinguishable_from_noise` |
| `verity` | 5/5 passed | (between mean 1.0) | (within floor) |

No trial touched the decoy subsystem or the test file. Every trial — cold
or with a handoff — correctly localized the real bug. The two-subsystem
design raised the cost of finding it (see below) but did not raise the risk
of failing to find it, at least not for the model used here.

**Secondary metric — cost (tool calls per trial):**

| Condition | Tool calls (5 trials) | Noise floor | Conclusion |
|---|---|---|---|
| `naive` | 9, 7, 7, 10, 7 (mean 8.0) | `[7, 10]` | — |
| `verity` | 7, 7, 4, 4, 4 (mean 5.2) | (between mean 5.2) | `likely_real_difference` |

The gap is larger here than pilot 4's (naive mean 8.0 vs. pilot 4's 6.6;
verity mean 5.2 vs. pilot 4's 4.8) — consistent with a harder search space
costing more to redo from scratch, while a correct handoff's cost stays
close to "read the note, apply the fix" regardless of how hard the note was
to produce. Reproduce:

```bash
./setup_phase_a.sh
# run each of the 10 trials as a live agent per the design above
verity noise-floor naive_results.json verity_results.json --metric success
verity noise-floor naive_results.json verity_results.json --metric tool_uses
```

## A methodological detour worth recording

The first version of this fixture's `late_fee.py` contained the line
`# BUG: this should read policy.ACTIVE_POLICY, the current collections
policy -- not policy.DEPRECATED_POLICY...` — an artifact of writing the
fixture that never should have shipped in the "before" state. All 10 trials
of that first run passed, several explicitly citing "the bug was even
flagged in a comment in the source." That result was correctly recognized
as invalid *before* being reported: it measured whether an agent can read
an English-language bug report embedded in a comment, which every model
naturally can, not whether the two-subsystem design achieved anything. The
comment was removed, all 10 trial directories were rebuilt from the
corrected fixture, and every trial was re-run from scratch. The result
above is from that corrected run — verified to contain no `BUG` string
anywhere in the fixture before spending the second trial budget.

## Known limitations of this pilot, stated up front

- **Success is a ceiling for the third time in this series** (after
  ADR-0011 and ADR-0015). Across three different fixtures of increasing
  difficulty, no design so far has made a capable agent fail cold on a
  config-swap-style bug. This may say more about this class of bug (a
  wrong-but-plausible constant, one hop or two from the call site) being
  within reach of current models than about recovery's ceiling in general —
  a bug requiring runtime-only reasoning (a race condition, a bug that only
  reproduces with specific data) might behave differently.
- **The two-subsystem decoy did cost real tool calls** (naive's mean rose
  from pilot 4's 6.6 to 8.0) but never cost a wrong fix. Whether a design
  with three or more decoys, or subtler differences between healthy and
  broken subsystems, would eventually produce a real failure rate is
  untested.
- **Tool-call count remains a coarse cost proxy**, not tokens or wall-clock
  time, for the same reasons stated in pilot 4's README.
- **One fixture, one bug, 5 trials per condition.** Same caveat as every
  prior pilot — see `docs/BENCHMARK_PROTOCOL.md` before generalizing.
