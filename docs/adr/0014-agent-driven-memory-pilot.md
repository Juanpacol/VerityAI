# ADR-0014: Does an agent use its own memory tool without being told to?

- **Status**: Accepted
- **Date**: 2026-08-09
- **Context**: closing the gap named in ADR-0013's own limitations — a
  harness-prepared context proved easier to read from, but nothing had
  tested whether an agent *actively managing its own context across turns*
  gets the same benefit.

## Context

ADR-0013's pilot answered a single question from a context the harness had
already prepared. The agent made no decisions about memory — it just read
what it was given. The stronger, more product-relevant claim is that an
agent *equipped* with a memory tool will choose to use it, unprompted, when
something worth keeping appears. That claim had never been tested.

## Decision: turns are fresh, memoryless `Agent` invocations

There is no way to truncate a real model's context mid-conversation from
outside. Instead, each turn of each trial is a **separate call to the
`Agent` tool** — a fresh subagent with no memory of any previous call. This
is not a simulated sliding window; it is a genuine one, for free: nothing
persists between two `Agent` calls except what the orchestrator explicitly
re-supplies, plus whatever a prior turn wrote to disk. The `naive` condition
gets nothing to write with; the `verity` condition gets a `.verity/`
directory and knowledge that `verity remember`/`verity handoff` exist, and
decides for itself, every turn, whether to use them.

## Two bugs found and fixed before the result could be trusted

Both were caught by inspecting intermediate state (`.verity/state/*.jsonl`)
rather than trusting a trial's own report — the same discipline ADR-0009 and
ADR-0011 established.

1. **IBAN false-positive from an unrelated safety layer.** The first fixture
   draft included an IBAN-shaped account number (matching pilot 2's shape).
   3 of 5 `verity` turn-1 calls had their `verity remember` command blocked
   outright by this environment's own permission classifier, which
   pattern-matches IBAN-shaped strings as credentials; the other 2 persisted
   a self-redacted version ("account number on file") that would have failed
   turn 4 regardless. This is not a VerityAI defect — it is a real
   constraint on operating a memory CLI under an external safety layer, and
   it made the original fixture unusable. Fixed by dropping the account
   number and using a plain currency amount as the only target figure —
   still enough to trigger ADR-0012's classification rule, not shaped like a
   secret.
2. **Shell quoting silently corrupted 3 of 5 persisted statements.** The
   working instruction told agents to run
   `verity remember discovery "Case #7788: ... $4,231.50"` with double
   quotes. Bash expands `$4` inside double quotes as an unset positional
   parameter, silently turning `$4,231.50` into `,231.50` in three trials.
   Caught by reading `.verity/state/discoveries.jsonl` after turn 1, before
   running any further turns — fixed by instructing single quotes, and only
   the 3 corrupted trials were re-run.

Neither bug is a finding about the mechanism being measured. Both are
recorded because a pilot that trusted trial output without checking
intermediate state would have reported `verity` losing 3-5 of 5 trials for
reasons that had nothing to do with whether agents use memory tools.

## Result

5 trials per condition, 4 turns each, scored by exact match on the final
turn against `ground_truth.json`:

| Condition | Exact matches | Noise floor |
|---|---|---|
| `naive` | 0/5 | `[0.0, 0.0]` |
| `verity` | 5/5 | `[1.0, 1.0]` |

`compare_to_noise_floor` reports `likely_real_difference` in both
directions. Every `verity` trial, unprompted, ran `verity remember
discovery` in turn 1 and `verity handoff` in turn 4, and every one correctly
distinguished the persisted figure from the turn-3 decoy. Every `naive`
trial correctly declined ("INSUFFICIENT INFORMATION") rather than guessing.

## Consequences

- This is the first pilot in the project testing agent-*initiated* memory
  use rather than harness-prepared context or code-fix competence. It
  directly supports the product's central claim — an agent managing its own
  context does better than one that isn't — rather than the narrower claim
  that a well-prepared context is easier to read.
- It is still narrow: one fixture, one figure, 5 trials, and every `verity`
  trial was told the memory tool existed. Whether agents use it without
  being told, or under a harder/noisier session, is unmeasured — see the
  pilot's own README for the full limitations list.
- The two bugs found here are a reminder, independent of the actual result,
  that a CLI-based memory tool operated via an agent's own shell commands
  inherits every failure mode of shell scripting (quoting) and of whatever
  external safety layer wraps the agent's tool use (credential-shaped string
  detection) — neither of which `verity remember`'s own code controls, and
  both of which a production integration would need to account for.
- Next planned pillar (per the user's stated sequence): *recovery after
  reset* — `docs/BENCHMARK_PROTOCOL.md`'s still-unmeasured, and by its own
  account most valuable, Family B row.
