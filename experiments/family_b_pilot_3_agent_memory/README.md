# Agent-driven memory pilot: does an agent actually use `verity remember` on its own?

Per `docs/BENCHMARK_PROTOCOL.md`'s Family B procedure. Pilot 2
(`experiments/family_b_pilot_2_numeric_recall/`, ADR-0013) proved that a
*harness-prepared* context survives a financial figure better than a naively
truncated one — but its own README named the gap: nothing in it tested
whether an agent *managing its own context*, across real turns, gets the
same benefit. This pilot closes that gap.

**Status: complete.** 10 real trials (5 per condition), each a genuine
4-turn simulated session in which every turn is a **fresh, memoryless agent
invocation** — no shared conversation state between turns, by construction,
not by truncation. Result: `likely_real_difference`, noise floor `[0.0, 0.0]`
for `naive` vs `[1.0, 1.0]` for `verity`, zero overlap.

## The fixture

`raw_log.json` (from `generate_fixture.py`) is a 4-turn support session for
case #7788:

1. **Turn 1** states the amount owed ($4,231.50) once, in plain prose.
2. **Turn 2** is pure filler (a ticket-system sync line).
3. **Turn 3** introduces a decoy — a different amount ($500.00) attributed
   to a closed, unrelated case (#4521).
4. **Turn 4** asks for the exact amount owed on the *current* case, with an
   explicit instruction to say "INSUFFICIENT INFORMATION" rather than guess.

Unlike pilot 2, there is deliberately no IBAN-shaped account number in this
fixture. An earlier draft included one and it was blocked or silently
corrupted by this environment's own safety classifier when an agent tried to
persist it — see "A methodological detour" below. The target figure here is
a plain currency amount, which still triggers the ADR-0012 classification
rule (it requires a currency symbol) without pattern-matching a credential.

## The two conditions and the real mechanism being tested

Each turn of each trial is a **separate call to the `Agent` tool** — a fresh
subagent with no memory of any previous call. This is not a simulated
sliding window; it is a real one, for free: a new agent genuinely does not
know what a previous invocation did or said, unless something was persisted
to disk and handed to it explicitly.

- **`naive`**: each turn's agent receives only that turn's text, no tools
  suggested, nothing to persist with. Whatever isn't in the current turn's
  prompt is, for this agent, gone.
- **`verity`**: each turn's agent receives the same turn text, plus a
  working directory with its own initialized `.verity/` state and the
  knowledge that `verity remember discovery '...'` and `verity handoff`
  exist. Nobody tells the agent to use them for the target figure — that
  decision is the agent's own, every turn, exactly as it would be in real
  use.

This is a stronger test than pilot 2: pilot 2 tested whether a
harness-prepared context is easier to read from. This tests whether an
agent, given a memory tool and no instruction to use it for anything
specific, chooses to use it for the one fact worth keeping.

## Method

5 independent trials per condition, 4 sequential turns each (20 agent calls
per condition, 40 total). Each trial's `verity` condition used its own
isolated directory (`trials/verity_1` .. `trials/verity_5`) so no trial's
memory could leak into another's. Final-turn answers were scored by **exact
string match** against `ground_truth.json`'s `total_amount`, verified
directly against each trial's literal output — never inferred from the
trial's own framing.

## Result

| Condition | 5 trials | Noise floor | Conclusion |
|---|---|---|---|
| `naive` | 0/5 exact matches | `[0.0, 0.0]` | `likely_real_difference` |
| `verity` | 5/5 exact matches | `[1.0, 1.0]` | `likely_real_difference` |

All 5 `naive` trials answered `INSUFFICIENT INFORMATION` at turn 4 — correct
and honest, since the figure genuinely was not in their turn's prompt. All 5
`verity` trials chose, unprompted, to run `verity remember discovery` in
turn 1, and all 5 chose to run `verity handoff` before answering in turn 4,
correctly distinguishing the persisted figure from the turn-3 decoy in every
case. Reproduce:

```bash
python3 generate_fixture.py
# then drive each trial's 4 turns manually per the design above
verity noise-floor naive_results.json verity_results.json --metric success
```

There is no single script that reproduces the trial-running step: each turn
is a live agent decision, not a deterministic transform, so — unlike
pilot 2's `prepare_contexts.py` — there is nothing to script here beyond
fixture generation and scoring.

## A methodological detour worth recording

The first attempt at this fixture used an IBAN-shaped account number
alongside the amount, matching pilot 2's design. Of the first 5 `verity`
turn-1 calls, 3 had their `verity remember` command **blocked outright** by
this environment's own safety/permission classifier (unrelated to
VerityAI), which pattern-matches IBAN-shaped strings as credentials. The
other 2 that got through **self-redacted** the account number in the
persisted statement ("account number on file, redacted") — meaning even
successful persistence would have failed turn 4's exact-match scoring. This
was caught and fixed before spending any trial budget on the flawed design,
by dropping the account number from the fixture entirely (see
`generate_fixture.py`'s docstring) — the target figure is a plain amount,
which still exercises ADR-0012's rule but isn't secret-shaped.

A second, smaller bug was caught the same way: the first working instruction
told agents to run `verity remember discovery "..."` with **double** quotes
in bash. Since the statement contains a literal `$4,231.50`, bash expanded
`$4` as an unset positional parameter inside double quotes, silently
corrupting 3 of 5 persisted statements to `,231.50` (missing `$4`). Caught
by inspecting `.verity/state/discoveries.jsonl` directly after turn 1,
before running any further turns on the corrupted trials — fixed by
instructing single quotes, and only the 3 affected trials were re-run.
Neither bug says anything about VerityAI's mechanism; both are about
operating a CLI memory tool safely from an agent's Bash calls, and are
recorded here because a pilot that didn't check its own intermediate state
would have reported a false negative for `verity` caused by its own harness.

## Known limitations of this pilot, stated up front

- **One fixture, one figure, 4 turns, 5 trials per condition.** This tests
  whether an agent *can* and *does* use the memory tool unprompted in one
  narrow scenario — not a general claim about how often agents do so, or
  how the behavior holds up over longer or noisier sessions.
- **The prompt for `verity` trials does mention the tool exists.** No trial
  used the memory tool without being told the tool was available — this
  pilot tests "does the agent use an available tool when it decides to",
  not "does the agent know to ask for one that was never mentioned."
- **All 5 `verity` trials used the tool; there is no partial-adoption data
  point.** A pilot with more trials or a less obviously important figure
  might find agents skipping the tool sometimes — this run doesn't have the
  statistical power to say how often that would happen.
- **Both noise floors are degenerate** (`[0,0]` and `[1,1]`), same caveat as
  pilot 2: a ceiling in the informative direction, not evidence of real
  within-condition variance on a harder or more ambiguous task.
