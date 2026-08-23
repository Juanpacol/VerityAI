# Long-log context recovery: the tool's actual value proposition

Per `docs/BENCHMARK_PROTOCOL.md`'s Family A rule (deterministic, no model in
the loop, n=1 suffices per fixture — the comparison itself is what varies,
not repeated sampling of a stochastic process).

**Status: complete, 3/3.** Across three independent fixtures, naive
tail-truncation loses the one fact that answers the task every time; `verity
context` recovers it every time, at the same token budget.

## Why this pilot exists

This retroactively rebuilds a result first produced 2026-08-17 through
2026-08-19 against three external private repos. That evidence was destroyed
by a session `/compact` before being retained — a violation of invariant 7
recorded in `experiments/UNREPRODUCIBLE.md`. Unlike the two prior violations
there, this result has **no agent behavior in the measurement loop** — both
arms are pure functions of a fixture — so it is fully re-derivable and did
not need to be written off, only rebuilt. See
[[project_pilot_findings_context_vs_speed]] for how this result sits
alongside the session's other finding (no speed advantage for a fresh agent
working a known bug — that one is `family_b_pilot_10_fresh_agent_speed`, a
genuine negative result kept for the same reason).

The three fixtures here are synthetic, not the original external repos —
deliberately, so a third party can re-derive this evidence without needing
three other private GitHub repositories checked out. `generate_fixture.py`
is fully self-contained.

## Design

`generate_fixture.py` builds three ~12,000-character session logs (auth
service, billing service, search service), each with the same shape:

1. An intro and a round of filler review notes ("also check X, nothing
   here") — realistic noise, no signal.
2. **One real fact, stated exactly once**, mid-log: the actual bug, its root
   cause, and its fix — everything a task ("find the exact bug in X and its
   fix") needs.
3. More filler.
4. **A decoy near the tail**: a plausible-sounding "actually, I think THIS
   is the most important finding" tangent, explicitly framed as "the
   headline of our writeup" — engineered to be exactly what naive
   tail-truncation keeps.
5. A wrapup.

Two arms, both deterministic, both run by `run_pilot9.py`:
- **naive**: fills the token budget from the end of the conversation
  backward — the obvious baseline anyone reaches for without a pruning
  pipeline.
- **verity**: `ContextPipeline.run(items, task=..., budget=...)` — the same
  code path `verity context` runs.

Budget: 25% of the fixture's total token count (`TokenCounter`, same counter
both arms use). Scored by whether the fact's distinctive marker string ("This
is the actual bug") survives, case-insensitively, in the arm's output.

## Result

| Fixture | naive | verity |
|---|---|---|
| auth_service | ✗ lost | ✓ recovered |
| billing_service | ✗ lost | ✓ recovered |
| search_service | ✗ lost | ✓ recovered |

`naive: 0/3  verity: 3/3` — see `evidence/report.json` and
`evidence/manifest.jsonl` for the per-trial detail, including each output's
sha256 and the exact budget used.

This is the same fix path this project's own [[0033-user-messages-are-not-unconditionally-critical|ADR-0033]]
made possible: before that fix, `verity context` lost the signal identically
to naive, because every generic user turn was marked unconditionally
`CRITICAL` and starved the budget before ranking ever got a chance to
protect the informative reply.

## Reproduce

```bash
cd experiments/family_b_pilot_9_long_log_recovery
python3 generate_fixture.py   # writes logs/*.json, deterministic
python3 run_pilot9.py         # runs both arms, writes evidence/
```

Re-running both scripts reproduces `evidence/manifest.jsonl` and
`evidence/report.json` byte-for-byte (verified via sha256 before this pilot
was retained).
