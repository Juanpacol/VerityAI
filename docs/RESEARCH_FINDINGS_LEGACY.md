# Research Findings — T1-T6 Synthesis (Fase 6)

## Why this document exists

After the hybrid-retrieval-trace-view work merged to `main`
(2026-07-14), the explicit decision was made **not** to publish, pitch,
or scale VerityAI externally yet — first find out whether its core
claims (confidence calibration, retrieval helping, rule-corpus scaling,
what fraction of code is even verifiable, what closes the SQLi/race gap,
what actually builds developer trust) hold up under real, adversarial
scrutiny. This document consolidates what six research questions (T1-T6)
found, states a direction, and is the gate before any external framing
of this project changes.

**Read this before citing any specific number from this project in an
interview, a blog post, or a resume.** Several findings below correct or
retract claims made earlier in `docs/PHASE_3_METHODOLOGY.md` in real
time, as evidence accumulated — that correction process is itself part
of the story, not a footnote (see `docs/CASE_STUDY.md` Findings 5-6).

## The six questions and what was actually found

### T1 — Does the confidence score calibrate? **No, and not just imprecisely.**

Reliability diagrams + Expected Calibration Error across every real
baseline/arm run so far: ECE ranges 0.14 (`hybrid_kg`, best) to 0.50
(`raw_llm`, the expected null-model worst case). No configuration is
well-calibrated. Worse: `single_shot_z3` shows an **inverted** signal in
its two lowest confidence bins — near-zero-confidence `FAIL` verdicts
were usually *actually correct* code (75% empirical accuracy), while
`NOT_VERIFIED` abstentions (fixed 0.3 baseline confidence) were usually
*actually buggy* (12.5% empirical accuracy). That's backwards from what
a trust signal should show. Small-n caveat: bins hold 2-6 outcomes each
(n=28 total per config) — this shows enough signal to say "don't trust
the current confidence formula as calibrated," not enough to pin down
the exact degree or confirm the inversion survives repetition.
Full detail: `docs/PHASE_3_METHODOLOGY.md`, "Analysis" section.

### T2/T7 — Does the retry loop trade accuracy for precision, and does KG context help? **The retry-loop claim was retracted. The KG-context effect looks real.**

Checking `verityai_full` (Real run #2) against `no_kg` (Real run #3) —
the *literal same configuration* run on different days — showed
task-level verdict disagreement (50%, ground-truth-controlled)
statistically indistinguishable from single-shot-vs-full-retry
disagreement (55%). **The originally-published "the retry loop shifts
the error profile" claim cannot currently be told apart from ordinary
`temperature=0.7` sampling noise at n=28 with zero repetition.** This is
a retraction of an earlier, over-confident claim, not a new bug — see
`docs/CASE_STUDY.md` Finding 5. The piece that *did* survive the
identical check: pairwise divergence between `no_kg`/`legacy_kg`/
`hybrid_kg` (48-62% ground-truth agreement) sits consistently *below*
the ~69-71% noise floor — the KG-context effect is likely real, unlike
the retry-loop trade-off.

### T3 — What fraction of realistic code is inside the verifiable subset? **6.1% (HumanEval) / 9.4% (MBPP), post-expansion. The most serious limitation found.**

Running the *actual* `ASTtoSMTConverter` (not a reimplementation)
against all 164 HumanEval and all 974 MBPP canonical solutions originally
found 6.1% and 8.8% respectively fall inside the int/bool/if-else/
bounded-for verifiable subset. This is far below the "70-80% fine,
30-40% concerning" range assumed in conversation before the number was
known. A small fraction of exclusions (3.7%/0.8%) are a known Z3
boolean-coercion engine bug, not genuine scope limitations — kept in
their own category rather than conflated.

**Updated same evening**: basic Z3 String theory support (equality,
concatenation, `len()`, annotation-aware parameter typing) was added to
`ast_to_smt.py` and the same evidence re-classified with no new fetching.
Result: HumanEval unchanged (6.1%, gained nothing — its problems mostly
need indexing/slicing/methods/recursion, none of which this touches),
MBPP moved from 8.8% to **9.4%** (+6 problems out of 974). Real, but
modest — direct evidence that closing this gap further needs string
*indexing and method-call* support, a materially larger and riskier
undertaking, not just equality/concatenation. Full detail:
`docs/PHASE_3_METHODOLOGY.md`'s Real run #3 section (original classifier
methodology) and "Follow-up on RESEARCH_FINDINGS.md's direction" section
(the expansion and its measured impact).

### T4 — Does hybrid-retrieval accuracy improve with a bigger rule corpus? **No detectable ROI from 10→48 rules in this run.**

F1 identical (0.500) at corpus sizes 10, 30, and 48 rules; precision
rises as recall falls, canceling out exactly. Ground-truth agreement
across sizes (71-82%) sits *at or above* the T2 noise floor — unlike the
KG-context comparison, which sat below it. Growing the corpus past 10
rules gave no evidence of helping, in this run. Only covers 10-48 rules,
not the order-of-magnitude jump to the "low hundreds" CLAUDE.md
anticipates. Full detail: `docs/PHASE_3_METHODOLOGY.md` Real run #4.

### T5 — What actually builds developer trust in the output? **Protocol designed, real materials generated, NOT yet executed.**

This is the one question requiring real human participants, which
cannot be substituted with more automated analysis or fabricated to fill
the gap. Full protocol, real (not simulated) `/runs/{id}/view` materials
from live generation, recruitment plan, and analysis method:
`docs/T5_HUMAN_EVAL_PROTOCOL.md`. **Status: not started.** Do not treat
any hypothesis about "what builds trust" in this document as answered
until that protocol actually runs.

### T6 — Can pattern-matching close the SQLi/race-condition gap Z3 structurally can't? **Yes, narrowly — and it surfaced a real bug in existing code.**

Two KG rules (`SQL Injection Prevention`, `No Check-Then-Act Race`) had
existed as prompt-only guidance since the project's earliest seed data,
never independently checked. Built two narrow AST fact extractors
(`symbolic/security_facts.py`) and wired them into the pre-existing
`RuleEngine` — which turned out to have a real design gap:
`apply_rule_to_code` can only ever return `PASS` or `UNKNOWN`, never
`FAIL`, so it reported a false `PASS` on genuinely vulnerable code. Fixed
via a new, additive `check_for_violation` method (existing behavior
untouched — nothing else called the old method). Verified end-to-end
against hand-written vulnerable/safe fixtures with zero false positives
on the closest existing security-benchmark shapes. Confirmed via real
evidence (`docs/evidence/z3_docs/`) that this genuinely isn't a
VerityAI-specific gap: Z3's own docs state its string theory is "an
incomplete heuristic solver," not decidable in general. Full detail:
`docs/PHASE_3_METHODOLOGY.md` T6 section, `docs/CASE_STUDY.md` Finding 6.

## Direction: what to scale, what to hold, what to fix first

**Hold — do not scale on current evidence:**
- Hybrid retrieval as the default (`VERITYAI_RETRIEVAL_STRATEGY` stays
  `legacy`) — T4 found no corpus-size ROI, and the original hybrid-vs-
  legacy A/B (before this research phase) showed hybrid losing on
  accuracy/recall.
- Any claim about the retry loop's accuracy/precision trade-off — T2
  retracted the only prior evidence for it. Don't repeat the original
  framing in a pitch or resume bullet.
- The confidence score as a "trust this number" pitch — T1 shows it
  doesn't calibrate and is sometimes inverted. This is the single
  biggest gap between VerityAI's current marketing framing (CLAUDE.md's
  "shows formal proof + explanation") and its actual measured behavior.

**Fix/build first, before publishing anything external:**
1. **Run T5 for real.** Every other finding here is a system-side
   metric; T5 is the only one that tells you whether any of this matters
   to an actual developer. If humans say "I don't even look at the
   score, I look at the Z3 counterexample," that reprioritizes
   everything above it — including whether fixing T1's calibration is
   even the right lever to pull. Still not done — needs real participants.
2. ~~Build repeated-run infrastructure.~~ **Done, same evening.**
   `src/verityai/evaluation/repetition.py` (tested library:
   `pairwise_agreement_summary`, `summarize_metric_variance`) plus
   `scripts/run_repeat_validation.py`, run for real (10 tasks, 2 repeats,
   same day) -- agreement rate 60.0%, close to the prior cross-day
   estimate of 69.2% (diff 0.09), cross-validating the noise floor across
   two different measurement methods. Use this infrastructure for any
   future comparison-driven experiment instead of the ad-hoc after-the-
   fact check T2 originally relied on.
3. **Expand the verifiable subset, if the T3 number is going to be
   defended at all.** Partially attempted, same evening: basic Z3 String
   theory (equality, concatenation, `len()`, annotation-aware parameter
   typing) moved MBPP from 8.8% to 9.4% (+6/974) and HumanEval not at all
   (6.1%, unchanged) — real but modest, and direct evidence that closing
   this gap further needs string indexing/slicing/method-call support, a
   materially larger and riskier investment than what was attempted here.
   The choice is now more informed but still open: (a) invest further in
   Z3 String/Array theory to try to move the number more, or (b)
   explicitly reposition VerityAI as a narrow-scope tool for a specific
   class of logic (arithmetic/control-flow-heavy functions) rather than
   "code verification" broadly, and say so up front rather than let
   single-digit-percent coverage be a surprise in an interview.

**Continue, validated by this research phase:**
- Pattern-matching for the specific gaps Z3 structurally cannot cover
  (T6) — SQLi and check-then-act races are tractable this way, and the
  approach found and fixed a real bug in already-shipped code, which is
  itself evidence the method works, not just a hypothesis.
- The evidence pipeline (`src/verityai/evidence/`) as a mechanism for
  grounding future claims in real external sources rather than assumed
  ranges — used directly to source T6's Z3-limitations evidence and will
  keep being useful for T1/T4-adjacent questions (e.g., what calibration
  quality is normal in comparable published work).

## What to say (and not say) about this project right now

**Say**: "We built a neuro-symbolic verification system, then spent a
research phase checking whether its core claims actually held up — and
found the confidence score doesn't calibrate, a previously-published
retry-loop finding was actually noise, only 6-9% of realistic code falls
in the formally-verifiable subset, and the rule corpus shows no ROI past
10 rules in testing so far. We also found and fixed a real bug in the
rule engine while prototyping SQL-injection detection. The one thing we
haven't checked yet is whether any of this matters to an actual
developer — that's the next thing to run, before deciding what (if
anything) to scale."

**Don't say**: "VerityAI has a well-calibrated confidence score" / "the
retry loop measurably improves precision" / "hybrid retrieval improves
accuracy" / any number from this project without checking this document
first for whether it's since been qualified or retracted.

## Standing rules this research phase established (apply going forward)

1. No future A/B or cross-model comparison in this project attributes an
   accuracy/precision/recall difference to a mechanism without checking
   it against a same-configuration repeat first (from T2's retraction).
2. `EvidenceStore.save()`-style idempotency checks must compare full
   serialized state, not a single content hash, when *any* field
   (not just the fetched content) can change after first save (from the
   evidence-pipeline bug fixes found during T3/T6 work).
3. A finding that "no effect was detected" must be checked against the
   noise floor before being read as "no effect exists" (T4's central
   methodological move, reusable for any future ablation).
4. Rule 1 now has real tooling, not just a reminder:
   `evaluation/repetition.py` is the reusable, tested implementation --
   use it (`pairwise_agreement_summary`, `summarize_metric_variance`)
   rather than re-deriving the check ad hoc in a new script.
