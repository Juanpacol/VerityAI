# Recovery-after-reset pilot: does a handoff document actually save work?

> **Evidence caveat added 2026-08-10 (Phase 0 truth repair, ADR-0021's sibling
> finding):** the `trials/` directories that held each trial's post-fix code
> were later wiped by a re-run of `experiments/lib/setup_phase_a.sh`'s
> `rm -rf trials` (only the setup script's *`--force`-style guard* now
> prevents this — see the script). Nothing under `trials/` was git-tracked.
> The success and tool-call numbers below are therefore **not independently
> re-derivable**: they rest on the hand-recorded `naive_results.json` /
> `verity_results.json` alone, and the per-trial tool-call counts in
> particular were self-reported at the time, not captured by any
> instrumented harness. Treat this pilot's numbers as unverified history,
> not as a reproducible result — **permanently**. `tool_uses` counts an
> agent's tool calls, which is a property of behaviour rather than of the
> code left behind, so no fixture-and-scorer harness can regenerate it,
> `verity eval` included. An earlier version of this caveat said the numbers
> were unverifiable "until re-run through `verity eval`"; that promised a
> repair which does not exist. See `experiments/UNREPRODUCIBLE.md`.

Per `docs/BENCHMARK_PROTOCOL.md`'s Family B procedure. "Recovery quality
after reset" is named there as *"the one thing the harness does that an
agent cannot do for itself"* and the most valuable unmeasured row in the
table — but the protocol also warns that this metric normally requires
being "judged by a model or a human", the exact kind of uncalibrated
subjective score T1 (`docs/RESEARCH_FINDINGS_LEGACY.md`) forbids publishing.
This pilot avoids a subjective judge entirely: the task is designed so the
project's usual objective criterion (does `pytest` pass, on a real fix, not
a patched test) is also the recovery signal.

**Status: complete.** 10 real trials (5 per condition), each a single-turn
live agent with Bash access, fixing a real seeded bug. Every trial's final
state was verified independently: `pytest` run by the harness (never
trusted from the agent's own report), and the test file diffed against the
original to rule out patching the test instead of the bug.

## The fixture

`fixture_repo/` is a small `pricing` package with a two-hop bug, deliberately
harder than the first Family B pilot's (ADR-0011) single-line, pytest-names-
the-culprit bug:

- `pricing/config.py`: a correct per-tier `THRESHOLDS` dict, plus a stale
  `DEFAULT_THRESHOLD` fallback from an older, non-tiered pricing scheme.
- `pricing/discount.py`: `get_bulk_discount_rate()` gates the discount on
  `DEFAULT_THRESHOLD` instead of `THRESHOLDS[tier]["quantity"]` — the real
  bug, two function calls away from the failing test.
- `tests/test_pricing.py`: fails with a plain numeric mismatch
  (`250.0 == 225.0`) that doesn't name the culprit file.

## Phase A is fabricated, not another live agent

A two-live-agent design (agent A investigates, agent B recovers) would
confound two questions: "did agent A investigate well?" and "does recovering
its work help?". To isolate the second question, the "prior investigation"
each `verity` trial recovers is written directly via the same CLI a real
agent would use — `verity task`, `verity remember decision`, `verity
remember discovery` (see `setup_phase_a.sh`) — with fixed content naming the
exact root cause, the exact file, and the exact next action. This is
deterministic and reproducible, and it means the pilot measures whether
recovering a handoff helps, not how good some other agent's investigation
happened to be.

## The two conditions

- **`naive`**: a fresh agent gets only "there's a failing test, fix it" and
  Bash access to the repo. No `.verity/` state exists — as if a reset wiped
  everything, with no recovery mechanism available at all.
- **`verity`**: the same fresh agent, same bare instruction, plus a
  `.verity/` directory pre-loaded with the fabricated phase-A investigation,
  and told to run `verity handoff` before doing anything else.

Both conditions may freely explore, edit, and run `pytest` themselves — this
pilot is closer to pilot 1's shape (a real coding task) than pilots 2-3's
(single-turn text recall), since "recovery after reset" is fundamentally
about resuming a piece of *work*, not recalling a fact.

## Method

5 trials per condition. Constraint given to every trial: don't touch
`tests/`. Two things verified independently after each trial, never taken
from the agent's own report: `pytest tests/` exit status, and a diff of
`tests/test_pricing.py` against the original (to catch a trial passing the
test by weakening it rather than fixing the bug).

## Result

**Primary metric — task success:**

| Condition | 5 trials | Noise floor | Conclusion |
|---|---|---|---|
| `naive` | 5/5 passed | `[1.0, 1.0]` | `indistinguishable_from_noise` |
| `verity` | 5/5 passed | (between mean 1.0) | (within `naive`'s floor) |

All 10 trials fixed the real bug correctly, with no test-file tampering —
another ceiling, in the same shape as ADR-0011's first pilot: the task,
while harder than that one, still wasn't hard enough for a capable agent to
fail cold. This says nothing against the recovery mechanism; it says this
fixture's difficulty ceiling needs to be higher to see task-success vary.

**Secondary metric — cost of getting there (tool calls per trial):**

| Condition | Tool calls (5 trials) | Noise floor | Conclusion |
|---|---|---|---|
| `naive` | 6, 8, 5, 7, 7 (mean 6.6) | `[5, 8]` | — |
| `verity` | 5, 5, 5, 4, 5 (mean 4.8) | (between mean 4.8) | `likely_real_difference` |

`verity`'s mean falls below `naive`'s own within-condition floor. Every
`verity` trial ran `verity handoff` first and then went essentially straight
to the fix; every `naive` trial spent extra tool calls re-deriving the same
call chain (`calculate_total` → `apply_discounts` →
`get_bulk_discount_rate`) that the fabricated handoff had already named.
Reproduce:

```bash
./setup_phase_a.sh
# run each of the 10 trials as a live agent per the design above
verity noise-floor naive_results.json verity_results.json --metric success
verity noise-floor naive_results.json verity_results.json --metric tool_uses
```

## Known limitations of this pilot, stated up front

- **The primary metric (success) is a ceiling, like ADR-0011's first
  pilot.** This fixture's difficulty was calibrated to be harder than that
  pilot's, but not hard enough to make cold recovery fail outright — a
  harder or more ambiguous bug might show a real success-rate gap instead
  of (or in addition to) a cost gap.
- **Tool-call count is a rough, coarse proxy for "cost of recovery".** It
  is not tokens, not wall-clock time, and not weighted by how expensive
  each tool call was — it is reported here because it is objective and
  requires no subjective judge, in the same spirit as the rest of this
  project's metrics, not because it is the most precise possible cost
  measure.
- **The phase-A investigation was fabricated by the harness, not produced
  by a real prior agent.** This isolates the question this pilot means to
  answer, but it also means the pilot says nothing about how good agents
  actually are at writing a handoff-worthy decision/discovery in the first
  place — only that recovering a *correct* one helps.
- **One fixture, one bug, 5 trials per condition.** Same caveat as every
  prior pilot in this series — see `docs/BENCHMARK_PROTOCOL.md` before
  generalizing.
