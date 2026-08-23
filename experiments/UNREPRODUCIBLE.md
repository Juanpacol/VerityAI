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

---

## Recovered, not permanent: the long-log recovery pilot

A third violation, distinct in kind from the two above. A self-run pilot
(2026-08-17 through 08-19, no external developers) built long synthetic
session logs from three external private repos, found that `verity context`
recovered a buried fact naive tail-truncation lost — 3/3 — then lost that
evidence itself when the working session's scratchpad was destroyed by
`/compact`, before it was ever committed anywhere. No `trials/` directory,
no manifest, nothing tracked.

**Why this one is not permanent, unlike pilots 4-6.** Both arms of this
comparison are deterministic functions of a fixture — a token-budget prune
and a tail-truncation, no agent behavior, no live tool calls in the
measurement loop. Nothing about the finding depended on the destroyed
session; it only needed to be rebuilt. It now is:
[Pilot 9 — long-log recovery](family_b_pilot_9_long_log_recovery/), with
synthetic self-contained fixtures (not the original external repos, so the
evidence needs no outside dependency to re-derive) and the same 3/3 result.
`evidence/manifest.jsonl` and `evidence/report.json` were confirmed
byte-identical across two independent runs of `generate_fixture.py` +
`run_pilot9.py` before being retained.

**The lesson, same as pilots 4-6's:** evidence that lives only in a session
scratchpad is not retained, regardless of how it was produced. The
difference here is that "no agent behavior in the loop" is exactly the
condition that makes a lost result recoverable rather than permanently gone
— worth checking for, specifically, before writing a lost result off.

---

## The same violation happened twice in one session, on the opposite kind of pilot

The very next pilot in this session (`family_b_pilot_10_fresh_agent_speed`,
6 bugs × 3 reps in an earlier attempt, testing whether a fresh agent with an
MCP handoff solves a *known* bug faster than one without) lost its evidence
to the identical failure — a session `/compact` before commit — but this
one genuinely **cannot** be rebuilt the way pilot 9 was. Both arms are
live-agent trials, not a deterministic function of a fixture; `tool_uses`
and turn counts are a property of agent behavior, exactly the reason pilots
4-6 above are permanently unverifiable. Those specific numbers (+36%/+13%/
+9% turns, verity worse in all three) are gone and join pilots 4-6 in "may
be cited as history, never as a current result."

**What was done instead of re-describing the lost numbers:** a fresh run of
the same question, retained as `family_b_pilot_10_fresh_agent_speed` —
`evidence/` is written to this tracked directory after every individual
trial completes, not batched at the end, specifically so a mid-run
`/compact` can cost at most the trials not yet written, never the whole
pilot. That run's result differs from the lost one (direction flips between
the two bugs tested, rather than verity losing in all cases) — which is
itself informative: two independent attempts at the same question, at n=3,
disagreeing on direction is exactly what "no real effect, mostly noise"
looks like, and is a stronger version of the conclusion than either run
alone would support.

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
