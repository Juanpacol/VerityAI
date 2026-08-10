# Runtime-bug pilot: a call-sequence bug instead of a wrong constant

> **Evidence caveat added 2026-08-10 (Phase 0 truth repair):** as with
> pilots 4 and 5, the `trials/` directories were later wiped by a re-run of
> `experiments/lib/setup_phase_a.sh` (now guarded, see the script). Nothing
> under `trials/` was git-tracked, so the numbers below are **not
> independently re-derivable** — they rest on the hand-recorded results
> JSON alone, and the tool-call counts were self-reported, not captured by
> any instrumented harness. Treat as unverified history until re-run
> through `verity eval`.

Per `docs/BENCHMARK_PROTOCOL.md`'s Family B procedure. Pilots 4 and 5
(ADR-0015, ADR-0016) both used a "wrong-but-plausible constant" bug shape
and both ceilinged on success (10/10, then 5/5+5/5) — ADR-0016 concluded
this whole *class* of bug (a config swap one or two hops from the call
site) may simply be within reach of current models regardless of recovery
aid. This pilot changes the bug's shape entirely: instead of a wrong
constant visible by reading one function, the bug only shows up by tracing
an actual **sequence of calls**.

**Status: complete, and a fourth ceiling.** 10 real trials (5 per
condition). Task success: 10/10 both conditions, `indistinguishable_from_
noise`. Tool-call cost: `likely_real_difference`, consistent with pilots 4
and 5 — naive floor `[7, 9]` vs verity mean 4.2.

## The bug: a cache keyed on the wrong thing

`fixture_repo/catalog/cache.py`:

```python
_price_cache: dict[str, float] = {}

def get_price(item: str, tier: str) -> float:
    if item not in _price_cache:
        _price_cache[item] = _compute_price(item, tier)
    return _price_cache[item]
```

Read in isolation, this looks like ordinary memoization — there is no
wrong constant, no misnamed variable, nothing that pattern-matches "this
line is broken." The bug only appears when you trace the actual call
sequence `catalog/quote.py::build_quote()` makes: requesting the same
`item` under two different `tier` values in one quote (`"widget"` at
`"standard"`, then at `"premium"`) causes the second call to silently
return the *first* call's cached price, because the cache key never
included `tier`. `tests/test_quote.py` fails on a plain numeric mismatch
(`[40.0, 40.0] != [40.0, 50.0]`) that doesn't name a culprit file, same as
every prior pilot's fixture.

Learning directly from ADR-0016's finding, the fixture was grepped for the
literal string `bug`/`BUG` before spending any trial budget, and confirmed
to fail cold and pass with the real fix (keying the cache by `(item,
tier)`) before running any trials.

## Phase A, conditions, scoring: same as pilots 4 and 5

- Phase A fabricated via `verity task` / `verity remember
  decision/discovery` (`setup_phase_a.sh`), naming the exact root cause —
  including that it was "found by tracing the actual sequence of
  `get_price()` calls," not by reading the function in isolation.
- `naive`: fresh agent, bare "there's a failing test, fix it," no
  `.verity/`.
- `verity`: same fresh agent, `.verity/` pre-loaded, told to run `verity
  handoff` first.
- 5 live-agent trials per condition, Bash access, constrained not to touch
  `tests/`.
- Every trial verified independently: `pytest` run by the harness, plus a
  diff of `tests/test_quote.py` against the original.

## Result

**Primary metric — task success:**

| Condition | 5 trials | Noise floor | Conclusion |
|---|---|---|---|
| `naive` | 5/5 passed | `[1.0, 1.0]` | `indistinguishable_from_noise` |
| `verity` | 5/5 passed | (between mean 1.0) | (within floor) |

Every trial, cold or not, correctly diagnosed the cache-key bug and fixed
it the same way (keying `_price_cache` by `(item, tier)`). Changing the
bug's *shape* from a wrong constant to a call-sequence-dependent one did
not break the ceiling — the fourth ceiling in this series (after ADR-0011,
0015, 0016).

**Secondary metric — cost (tool calls per trial):**

| Condition | Tool calls (5 trials) | Noise floor | Conclusion |
|---|---|---|---|
| `naive` | 7, 8, 9, 8, 8 (mean 8.0) | `[7, 9]` | — |
| `verity` | 4, 4, 4, 5, 4 (mean 4.2) | (between mean 4.2) | `likely_real_difference` |

Consistent with pilots 4 and 5: the cost effect reproduces a third time.
Reproduce:

```bash
./setup_phase_a.sh
# run each of the 10 trials as a live agent per the design above
verity noise-floor naive_results.json verity_results.json --metric success
verity noise-floor naive_results.json verity_results.json --metric tool_uses
```

## Known limitations of this pilot, stated up front

- **Success is a ceiling for the fourth time.** This time the bug's shape
  changed (call-sequence-dependent, not a wrong constant) and it still
  didn't matter. This narrows what's left to try: a bug that's hard not
  because of *where* the fix is, but because *diagnosing it requires
  information a static reading plus one pytest run can't fully supply* —
  e.g., a bug that only reproduces under specific data values not shown by
  the first failing test, or genuinely non-deterministic behavior (which
  this project's own principles rule out testing via real race conditions,
  since that would put noise into the scoring itself).
- **This is still a deterministic bug** — the "runtime" framing means
  "requires tracing execution," not "non-deterministic." A true race
  condition was deliberately avoided per `docs/BENCHMARK_PROTOCOL.md`'s
  own insistence on a clean noise floor uncontaminated by unrelated
  sources of randomness.
- **Tool-call count remains a coarse cost proxy**, same caveat as pilots 4
  and 5.
- **One fixture, one bug, 5 trials per condition.** Same caveat as every
  prior pilot — see `docs/BENCHMARK_PROTOCOL.md` before generalizing.
