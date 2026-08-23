# Real-session context recovery: preliminary n=1

Per `docs/BENCHMARK_PROTOCOL.md`'s Family B procedure — real agent trials,
scored independently. **Status: preliminary, n=1 per condition, not a
publishable Family B result.** Stopped deliberately after one pair; see
"Why n=1" below.

## Why this pilot exists

`family_b_pilot_9_long_log_recovery` proves context recovery against a
*synthetic* long log. This pilot tests the same claim against a *real*
Claude Code session growing toward genuine compaction, which had never
been done. The result at n=1 is directionally the same as pilot 9's, and
is retained specifically because it is evidence against a *real* context
mechanism, not a fabricated one — even at this sample size it is worth more
than a synthetic result, and worth less than a properly-repeated one.

## Design

`/compact` is a REPL-only slash command, not invocable via `claude -p`
(headless). The only scriptable trigger is `--autocompact <tokens>`, whose
documented floor is 100k tokens — real conversation growth, not a shortcut.

One session per condition, four sequential `claude -p ... --resume <id>
--autocompact 100k` invocations against a fresh clone of this repository:

1. **Plant.** Investigate a real, specific fact:
   `reliability/security.py`'s shell-command-injection rule
   ([[0032-shell-command-security-rule|ADR-0032]]) — its
   `severity`, `risk_tier`, and its stated blind spot (no data-flow
   analysis). The `verity` condition additionally records this via
   `verity remember discovery` into a real `.verity/` store.
2. **Pad (×2).** Real exploration work with no connection to the planted
   fact — summarize every file under `graph/`+`consistency/`, then
   `reliability/`+`bench/`. Purely to grow genuine context past the 100k
   floor; `cache_read_input_tokens` crossed 140k-180k by the third turn in
   both conditions.
3. **Check.** Ask for the fact back, explicitly forbidding a re-read of the
   source file. `verity`'s prompt additionally instructs calling
   `verity handoff` first.

## Result

| | naive | verity |
|---|---|---|
| severity/risk_tier | recovered correctly | recovered correctly |
| blind-spot detail | **lost** — "not stored in memory — need re-read" | **recovered**, near-verbatim to the discovery it recorded at step 1 |
| total cost (4 turns) | $1.25 | $0.90 |

Same shape as pilot 9: the short, simple fact survives either way; the more
specific detail survives only with the handoff. `verity`'s final answer was
confirmed to match `evidence/trials/verity_1/discoveries.jsonl` (the actual
record it wrote), not merely a plausible-sounding guess — and the run's
`permission_denials` was empty for that turn, confirming `verity handoff`
actually executed rather than being blocked and worked around.

## Why n=1

Real cost, not synthetic: each condition took 4 sequential invocations
minutes apart, ~$1-1.30 total, and the *second* condition's run was
interrupted mid-session by hitting the account's Claude Pro rate-limit
window (not API billing — a shared usage cap with the account's normal
Claude Code use). One retry after the window reset completed the pair.
Scaling to `docs/BENCHMARK_PROTOCOL.md`'s N≥5-per-condition minimum for a
real Family B verdict means 10 sessions of this shape, which was assessed
against that same rate-limit ceiling and deferred rather than risk
repeatedly exhausting an account's usage window for a preliminary check.

**This is not evidence of an effect at Family B confidence** — no noise
floor was established, n=1 cannot distinguish a real effect from a lucky
draw. It is retained as a directionally-consistent real-session data point
alongside pilot 9's synthetic one, and as the concrete design + cost
baseline (~$1/session, ~5-7 min, 4 sequential `--resume` calls) for
whoever runs the full N≥5 version later.
