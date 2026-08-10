# Published numbers with no retained artifact

Invariant 7 (CLAUDE.md) says no published metric without a retained,
re-derivable artifact. This file enumerates every number this project has
published that does **not** meet that bar, so the set is countable in one
place rather than scattered across ADRs and pilot READMEs.

A number listed here may well be correct. The claim being made is narrower
and harder: nobody — including its author — can now check it.

---

## Permanently unverifiable

### Pilots 4, 5, 6 — tool-call savings

| Pilot | Published | Metric |
|---|---|---|
| [4 — recovery after reset](family_b_pilot_4_recovery_after_reset/) | 6.6 → 4.8 mean tool calls | `tool_uses` |
| [5 — harder recovery](family_b_pilot_5_harder_recovery/) | 8.0 → 5.2 mean tool calls | `tool_uses` |
| [6 — runtime bug](family_b_pilot_6_runtime_bug/) | 8.0 → 4.2 mean tool calls | `tool_uses` |

**What happened.** Each pilot's `trials/` directories held the only copy of
the post-trial code. A later re-run of `experiments/lib/setup_phase_a.sh`
began with `rm -rf trials` and destroyed all 30. Nothing under `trials/` was
git-tracked, so no copy survived anywhere. Discovered 2026-08-10 (Phase 0
truth repair); the script now refuses to proceed without
`FORCE_TRIALS_RESET=1`, and [ADR-0027](../docs/adr/0027-retained-trial-evidence.md)
made retained evidence structural rather than a matter of remembering.

**Why re-running cannot fix these three.** `tool_uses` counts an agent's tool
calls. It is a property of an agent's *behaviour*, not of the code the agent
left behind. `verity eval` reconstructs a trial from a fixture, a condition
command and a scorer — a scripted condition command performs zero tool calls,
and no scorer can recover a count from a finished directory. A re-run would
re-derive only `success`, which was a 5/5 ceiling in all three conditions
and was never the finding.

So the earlier caveats on these pilots, which said the numbers were
unverifiable *"until re-run through `verity eval`"*, were wrong in a way
worth naming: they promised a repair that is not available. These figures
are **permanently unverifiable** without new live-agent runs, which would be
a new experiment producing new numbers, not a reproduction of these.

**How they may be cited.** As history — "three early pilots reported a
tool-call reduction; their evidence was lost and the figures cannot be
re-checked." Never as a current result, never in external material, and never
aggregated with figures that do have artifacts.

**What a real successor would need.** A live-agent pilot with tool calls
counted from the transcript rather than self-reported, run through
`verity eval` so each trial retains a diff and a manifest line. That is new
research, and it is not scheduled.

---

## Retained and re-derivable

For contrast, and so this file is not read as a general disclaimer:

| Result | Artifact |
|---|---|
| [Pilot 8 — arbitrary tie-break](family_b_pilot_8_arbitrary_tiebreak/) | `evidence/` — a per-trial diff, scorer output, and a manifest pinned to the fixture's hash, plus the `eval_spec.json` that produced them. `tests/unit/test_bench_evidence.py` re-derives every trial's metric on each test run. |

Pilot 7's headline metric is likewise a deterministic property of committed
code, though it has not yet been re-run through the harness to produce an
`evidence/` directory.

---

## Family A

`docs/MEASUREMENTS.md`'s Family A figures are re-derivable in a different
sense: they are computed by `verity bench` from Claude Code session
transcripts under `~/.claude/projects/`, which are not this repository's to
commit. Anyone with their own sessions can reproduce the *method*; nobody can
reproduce the *exact* numbers without those files. The relevant caveat there
is a separate one already stated in `docs/MEASUREMENTS.md`: every real
session hit `budget_met: False` under the 30k budget, so 100% critical
retention is guaranteed by invariant 1 in that regime rather than being
evidence of ranking quality.
