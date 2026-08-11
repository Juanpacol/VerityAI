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

**Removed from `README.md` (2026-08-11).** The README's Family B summary
previously stated the cost saving as a standing result ("the most reproduced
result here"). That framing did not distinguish a claim with retained
evidence from one without, so it has been rewritten to state only the
ceilinged success result and to point here for the cost claim, with the
explicit caveat that the cost claim is history, not a current, checkable
number. `docs/MEASUREMENTS.md` already carried the correct per-pilot caveats
before this change and was not restated.

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
| [Pilot 7 — domain ambiguity](family_b_pilot_7_domain_ambiguity/) | `evidence/`, retrofitted 2026-08-11 ([ADR-0031](../docs/adr/0031-pilot-7-retrofit.md)) from the code each trial left behind. `verity verify experiments/family_b_pilot_7_domain_ambiguity/evidence` re-derives all 10 trials to the originally published 5/5-and-5/5. `verity eval` still reports the run NOT PUBLISHABLE — see the ADR for why that is structural to retrofitting a ceiling, not a discrepancy in the number. |

**Retrofit candidates, not yet attempted**, in order of how directly their
existing materials support a scorer:

- **Pilot 2 (numeric recall)** — no fixture directory, but a regenerable one
  (`generate_fixture.py`, committed) and a committed `ground_truth.json` an
  exact-match scorer could check. Closest in shape to pilot 8.
- **Pilot 1 and the consistency pilot** — fixtures exist, but the original
  scoring was live-agent behaviour judged against prose
  (`ground_truth.md`), not an exact check; retrofitting either needs a new
  machine-checkable scorer, not just a reconstruction of left-behind code.
- **Pilot 3 (agent memory)** — not retrofittable by this technique for the
  same reason as pilots 4-6: its trials retained only `.verity/` state, not
  code, and its metric is a property of live multi-turn agent behaviour.

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
