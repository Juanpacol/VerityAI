# Fresh-agent speed on a known bug: no consistent advantage

Per `docs/BENCHMARK_PROTOCOL.md`'s Family B procedure — real agent trials,
scored independently, never trusting the agent's own report.

**Status: complete, n=3 per condition per bug, direction flips between
bugs.** Neither reading supports a turns/cost advantage for `verity` at this
scale.

## Why this pilot exists

Tests the other half of the question
[`family_b_pilot_9_long_log_recovery`](../family_b_pilot_9_long_log_recovery/)
answers: does giving a *fresh* agent an MCP memory handoff (root cause
already investigated) make it solve a *known* bug faster than starting
from nothing? Pilot 9 shows VerityAI recovers context that was lost. This
pilot asks the opposite question — does it also make an agent that still
has full access to the repo faster — and the honest answer, at this sample
size, is no.

This is a rebuild. An earlier same-session run of this exact pilot (6 bugs
across a different VerityAI-copy fixture) reported turns +36%/+13%/+9%
worse for `verity` in every case — but that evidence lived only in a
session scratchpad and was destroyed by `/compact` before being committed,
same failure as `family_b_pilot_9`'s first attempt
(see `experiments/UNREPRODUCIBLE.md`). Unlike pilot 9, **this result cannot
be rebuilt deterministically** — both arms are live-agent trials, not a
pure function of a fixture — so rather than re-describe the lost numbers,
this is a fresh run with evidence written into this tracked directory after
every single trial completes, not batched at the end. If this session gets
compacted mid-run, only the trials already written are lost, not all of
them.

## Design

Fixture: this repository itself, pinned to the commit **before** each of
two bugs this project found in itself was fixed —
[[0034-dropped-critical-was-dead-code|ADR-0034]]/[[0035-enforce-budget-tiebreak-was-inverted|ADR-0035]]
(commit `b320239`, both bugs live in `context/prune.py`) and
[[0036-supersede-never-deactivated-the-original|ADR-0036]] (commit
`cb64bd0`). Using this project's own git history as the fixture, instead of
an external repo, means the exact buggy state is permanently reproducible
by anyone with this repository — `git checkout b320239` or `git checkout
cb64bd0` — with no external dependency at all.

Two conditions, three repetitions each, per bug (12 trials total):
- **naive**: a fresh Claude Code session, the bug description, nothing else.
- **verity**: the same session, plus a fabricated `.verity/` containing the
  root cause and fix already worked out (the same content used to seed the
  MCP handoff in the earlier run) — the agent is instructed to call
  `mcp__verityai__session(op="handoff")` first and trust it.

Both conditions use a `.claude/settings.local.json` allow-listing
`pytest`/`python3.11` so the trial can actually run its own verification
instead of stalling on a permission prompt (the failure mode that made
several earlier pilots in this project unusable — see this repo's own
session history).

Scored independently after every trial: `git diff` retained as
`changes.diff`, and the real fix verified by running
`tests/unit/test_prune.py` (bug1) or `tests/unit/test_memory.py
tests/unit/test_consistency_check.py` (bug3) against the trial's own
checkout — not by reading the agent's summary.

## Result

| Bug | naive turns (avg) | verity turns (avg) | Δ |
|---|---|---|---|
| bug1 (`dropped_critical` + tiebreak) | 20.0 | 16.3 | **-18.3%** (verity faster) |
| bug3 (`supersede()`) | 13.7 | 16.7 | **+22.0%** (verity slower) |

12/12 trials fixed the bug correctly (verified by re-running the real test
suite against each trial's own checkout, independent of what the agent
reported). Direction flips between the two bugs at n=3 — the same shape an
earlier same-session run showed differently (verity worse in both bugs
there). Read together, both runs support the same conclusion: **no
consistent turns/cost advantage in either direction**, most consistent with
sampling noise at this scale (3 repetitions, a single-session task under 25
turns) rather than a real effect `verity`'s MCP handoff either grants or
costs.

## Reproduce

```bash
git clone <this-repo> /tmp/bug1_trial && git -C /tmp/bug1_trial checkout b320239
git clone <this-repo> /tmp/bug3_trial && git -C /tmp/bug3_trial checkout cb64bd0
# seed .verity/ per evidence/trials/*_verity/ (fabricated decision content
# matches ADR-0034/0035/0036's own root-cause writeups), run the trial,
# score with tests/unit/test_prune.py or test_memory.py+test_consistency_check.py
```

`evidence/manifest.jsonl` has one line per trial (turns, cost, condition);
`evidence/trials/<name>/changes.diff` is each trial's actual code change,
retained so the "12/12 correct" claim above is independently checkable
without re-running anything.
