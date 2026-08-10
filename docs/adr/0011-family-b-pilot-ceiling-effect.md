# ADR-0011: Family B pilot — a ceiling effect, and what it actually tells us

- **Status**: Accepted. Its *pilot design* is superseded by
  [ADR-0013](0013-numeric-recall-pilot.md), which corrects the ceiling
  this one hit; the finding recorded here stands.
- **Date**: 2026-08-09
- **Context**: closing the last gap in `docs/BENCHMARK_PROTOCOL.md` — does
  Verity change a task's *outcome*, as opposed to how many tokens a
  transform removes (ADR-0009) or whether a metric difference is real
  (ADR-0010, the tool this pilot is built on).

## Context

Family A answers "how many tokens does pruning remove." It says nothing
about whether an agent with Verity available actually does a task
differently or better. Answering that needs real trials, a noise floor
established before any comparison, and — because a pilot this size can
only detect a large effect or its absence — tasks hard enough that a large
effect has room to show up at all.

## Decision 1: an isolated, single-bug fixture per task, found necessary by
a real design failure

The first fixture design shared one repository with two seeded bugs live
simultaneously, one prompt per task pointing at the same directory. Four
mechanism-check trials (not counted as data) immediately showed why that
was wrong: the prompt said "make `pytest tests/` pass," and every
agent — correctly, given that instruction — ran the whole suite and fixed
*both* bugs regardless of which task it was assigned. That collapsed the
two intended tasks into one and guaranteed every future trial would succeed
by construction, before a single real data point was collected. Fixed by
splitting into `fixture_repo_task1/` (only the pricing bug live) and
`fixture_repo_task2/` (only the inventory bug live), each independently
verified beforehand to fail exactly its own target test.

This is the same category of finding as ADR-0009's dedup/classify ordering
bug: a flaw in the measurement apparatus, caught by running it before
trusting its output, not by inspection.

## Decision 2: score by running `pytest` directly, never by trusting the agent

Every trial's `{"success": 1.0 | 0.0}` came from the harness running
`pytest tests/` itself against the trial's working directory after the
agent stopped, and cross-checking that no file under `tests/` had been
touched. All 20 agent self-reports happened to be honest and consistent
with the independent check in this run, but the design does not depend on
that: an agent's own claim of success is exactly the kind of thing Phase 3's
Consistency Engine exists to be suspicious of, and this pilot holds itself
to the same standard rather than exempting its own benchmark from it.

## Decision 3: report the ceiling effect as the finding, not as a null result

Result, both tasks: 5/5 `alone`, 5/5 `verity`, noise floor `[1.0, 1.0]`,
between-config mean `1.0`, `compare_to_noise_floor` conclusion
`indistinguishable_from_noise`.

`indistinguishable_from_noise` is the mechanically correct label — a
between-config value equal to the within-config floor cannot be
distinguished from it — but reporting only that label without its cause
would be exactly the kind of context-free number this project's own rules
exist to prevent (the same reasoning behind never publishing a bare
`ContextHealth.score` or a bare `critical_retention` percentage). The cause
here is identifiable and specific: both seeded bugs are single-line, and
`pytest`'s own failure output names the wrong value directly (`assert
118.8 == 97.2`; `DID NOT RAISE ValueError`). Locating the fix — the
capability `verity graph context`/`find`/`deps` targets — was never the
hard part of either task, for either condition, so a tool aimed at that
capability had nothing to differentiate the conditions on. A pilot that
scores 5/5 in both arms cannot report anything *but*
`indistinguishable_from_noise`, regardless of whether a real effect exists
elsewhere; the honest conclusion is about this pilot's task design, not
about Verity.

## Consequences

- **No claim that Verity changes task outcomes exists anywhere in this
  project**, and none should be inferred from this result in either
  direction. That absence is itself the accurate current state, not a gap
  to paper over.
- A follow-up Family B pilot needs tasks sized past this ceiling: either a
  larger, unfamiliar codebase where locating the right code is genuinely
  the hard part, or a bug whose failing test does not already name the
  fix. Both conditions here failed that bar and should not be reused
  unmodified.
- The pilot's infrastructure — isolated per-task fixtures, `pytest`-based
  independent scoring, `bench/repetition.py`'s noise-floor procedure,
  `verity noise-floor` for the final comparison — all worked correctly end
  to end and needs no rework for the next attempt. Only the task design
  needs to change.
- `experiments/family_b_pilot/` (fixtures, prompts, `README.md`, and the
  raw `results/*.json`) is committed in full, so this result — and the next
  pilot's comparison to it — is reproducible by construction, per
  `docs/BENCHMARK_PROTOCOL.md`'s publication rule.
