# T5 — What Actually Builds Developer Trust in AI-Generated Code

## Status: protocol designed and materials generated; NOT yet executed

This is the one item in the T1-T6 research roadmap that fundamentally
requires real human participants — something no amount of automated
analysis (T1-T4, T6) or evidence scraping (`docs/EVIDENCE_COVERAGE.md`)
can substitute for. **No interviews have been conducted.** Everything
below is a ready-to-run protocol plus real (not simulated) materials to
show participants — fabricating "example" human responses to fill this
gap would violate the exact honesty standard the rest of this project
holds itself to (see `docs/CASE_STUDY.md`). This document exists so that
running the actual study is a scheduling problem, not a design problem,
whenever real participants are available.

## Research question

VerityAI's core pitch is a decomposed confidence score (verification 50%
/ pattern_similarity 25% / complexity 15% / test_coverage 10%) plus a
visual reasoning trace (`/runs/{request_id}/view`: pipeline stepper, KG
retrieval provenance, Z3 counterexample panel, confidence breakdown bar).
T1 already found the confidence *number* doesn't calibrate well against
ground truth on its own (see `docs/PHASE_3_METHODOLOGY.md`'s Analysis
section). T5 asks a different, human question: **when a developer looks
at this system's output, what actually makes them trust it or not** — the
number itself, the Z3 counterexample, knowing which KG rules were
retrieved, or something else entirely? If the number doesn't calibrate
but the *proof panel* still builds justified trust, that's a very
different product story than if neither does.

## Materials (real, not simulated)

`scripts/generate_human_eval_materials.py` ran 6 real prompts through the
live `Orchestrator` (llama3.2, hybrid KG retrieval, real Z3 verification
— the same infrastructure as every other real run in this project, not a
scripted demo) and rendered each with the actual `render_run_view` used
in production. Output: `docs/human_eval/materials/sample_01.html`
through `sample_06.html`, plus `manifest.json` listing each sample's
prompt, final status, confidence, and attempt count. The prompts were
chosen for likely variety (a simple case, a subtle edge case, something
outside the verifiable subset) — not cherry-picked for a "nice" story
after seeing the results.

**Before running the study**: open `docs/human_eval/materials/manifest.json`
and confirm the 6 samples actually span a mix of verdicts (some
`pass`/high confidence, ideally at least one `fail` or `not_verified`). If
by chance all 6 landed on the same verdict, re-run the script (prompts can
be edited in the script directly) before recruiting — participants need to
see real variety, not six near-identical "everything's fine" screens.

## Recruitment

- **N = 5-10 developers.** Professional software engineers, any
  experience level with AI coding tools (that's itself worth recording as
  a covariate, not a screening filter).
- Recruit via personal network / relevant online communities (e.g. a
  developer Discord/Slack, r/programming-adjacent spaces) — no payment
  budget assumed; a genuine "I'm building a code-verification tool and
  want 15 minutes of honest feedback" ask is the expected framing.
- **Session length**: ~20-25 minutes per participant (6 samples × ~2-3
  min each + wrap-up questions).
- **Format**: screen-share or in-person, participant reads the HTML file
  directly in a browser (no live server needed — the materials are
  self-contained static files, consistent with `render_run_view`'s
  self-containment guarantee).

## Interview script

For each of the 6 samples, in order (same order for every participant —
don't randomize order across participants for this small an N; do
randomize *which* sample is shown under which panel condition, see
below):

1. Show the **full view** (pipeline stepper + retrieval provenance + Z3
   panel + confidence breakdown, everything `render_run_view` renders).
2. Ask, verbatim: **"Do you trust this code? Why or why not?"**
3. Follow-up (only if not already answered): **"What specifically made
   you say that?"**
4. Record: trust/no-trust (binary), the verbatim reason, and which
   element of the page they pointed to or mentioned (score number / Z3
   panel / retrieval provenance / code itself / something else).

After all 6 samples shown in full, do a **second pass** with 3 of the 6
samples (participant's choice of which 3, or assign round-robin across
participants so each sample gets covered under each condition roughly
equally across the full N) shown with panels **hidden** one at a time
using browser dev tools or a pre-prepared stripped-down HTML variant:

- **Condition A — score only**: hide the Z3 panel and retrieval
  provenance table, leave the confidence number and code visible.
- **Condition B — Z3 only**: hide the confidence breakdown bar and
  retrieval provenance, leave the Z3 counterexample/pass panel and code
  visible.
- **Condition C — everything** (repeat, as a within-subject control):
  same as the first pass, to check if simply seeing the sample twice
  changes the answer independent of what's hidden.

Ask the same "do you trust this code, why?" question again under each
condition. This is the manipulation that answers the actual research
question — comparing trust/no-trust and the *stated reason* across
conditions A/B/C for the same underlying code tells you which panel is
actually load-bearing for trust, versus decorative.

**Closing questions** (once, after all samples):

- "If you had to remove everything on this page except one element to
  still trust or distrust code, what would you keep?"
- "Was there anything on this page that made you trust the code *less*,
  or that you didn't understand?"
- "How does this compare to how you currently decide whether to trust
  AI-generated code (Copilot, ChatGPT, etc.) today?"

## What to measure / how to analyze

Given N=5-10, this is qualitative, not a powered quantitative study —
don't compute a confidence interval on 8 people. What's actually
analyzable:

1. **Per-sample trust rate** under the full view: does trust track the
   system's own confidence score at all (even loosely), or is it
   uncorrelated? This is the closest human analog to T1's calibration
   question, and a useful cross-check against it.
2. **Reason coding**: tag each verbatim reason into a small set of
   categories (e.g. "trusted the number," "trusted the Z3 proof,"
   "trusted seeing which rules were checked," "didn't really trust it,
   just didn't look closely," "distrusted despite high confidence
   because X"). Count frequency per category — this is the primary
   answer to the research question.
3. **Condition A vs. B vs. C stated reasons**: did removing the Z3 panel
   change anyone's stated trust, or did removing the score? A reason
   that only appears in the "everything" condition and never survives
   into A or B is decorative for that participant; one that persists
   across conditions is load-bearing.
4. **The "remove everything but one" closing question** is the most
   direct signal — tabulate what people kept.

## Honest constraints on this design, stated up front

- N=5-10 with no random sampling of participants is not statistically
  generalizable — treat results as hypothesis-generating, consistent
  with how every other real run in this project (n=28, single runs) has
  been treated per the Fase 1 noise-floor standing rule.
- Same-order sample presentation (not randomized) risks an ordering
  effect (fatigue, anchoring on the first sample's format) — acceptable
  for this N and purpose, but worth randomizing order if this study is
  ever repeated at larger scale.
- The interviewer is also the system's builder, which is a real bias
  risk (participants may soften criticism to a builder present in the
  room/call). Where possible, have someone else run the sessions, or at
  minimum disclose this limitation alongside any results reported later.

## Next step

Recruit real participants and run this. Do not write findings into
`docs/RESEARCH_FINDINGS.md`'s T5 section until real sessions have
happened — until then, that section should say exactly what this
document's Status line says: protocol ready, not yet executed.
