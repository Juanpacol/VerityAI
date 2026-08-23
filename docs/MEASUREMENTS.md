# Measurements

Every number this project publishes, with the method that produced it and
the limits it carries. The rule these follow is in
[`BENCHMARK_PROTOCOL.md`](BENCHMARK_PROTOCOL.md): metrics split by whether
a model is in the loop, and no cross-configuration claim without a noise
floor established first.

Two things to know before reading:

- **Family A** is deterministic — no model call anywhere in the measured
  path, so `n=1` is sufficient and a repeat is only a smoke test.
- **Family B** has a model deciding something, so it needs N≥5 repeats per
  configuration and a noise floor before any comparison. Several pilots
  below returned `indistinguishable_from_noise`; those are reported in the
  same detail as the ones that didn't.

## First real measurement (2026-08-09, updated same day)

Measured on real Claude Code development sessions of this same project
(session transcripts read directly from `~/.claude/projects/`, never
committed — see [ADR-0009](adr/0009-family-a-real-measurement.md)),
`tiktoken:cl100k_base`. Every prior figure in this project's history was
synthetic; this is the first that isn't. (The session count and total
tokens have grown between measurements as this project's own development
continued — this corpus is a moving target by nature, not a fixed
benchmark set, and re-running the commands below today will not reproduce
these exact totals. See [ADR-0012](adr/0012-financial-figure-protection.md)
for the same reason the first re-measurement happened.)

| Configuration | Tokens before | Tokens after | Reduction | Critical retained | Digit retained |
|---|---:|---:|---:|---:|---:|
| No budget (dedup + noise filter + compression only) | 3,482,198 | 3,442,382 | **1.1%** | 100% | 100% |
| 30,000-token budget, ranked against the task | 3,483,993 | 1,508,198 | **56.7%** | 100% | 100% |

Two numbers, not one, because they answer different questions. The first is
what pruning removes for free, with nothing forced out — real sessions
turned out to carry far less exact-duplicate/dead-noise content than the
92.4% figure from the synthetic fixture that this project's own
`BENCHMARK_PROTOCOL.md` already flags as what not to publish. The
second is what happens once a real budget forces a choice, and it is the
number that matters for "can this fit in a smaller context" — with the two
invariants that have to hold under that pressure (nothing marked critical
is dropped; no financial figure — amount, account number — is dropped)
holding exactly at 100% in both real runs. 21 distinct financial figures
were present in this corpus; none were vacuous zero-figure runs.

**The caveat that belongs on the 56.7% row and did not appear here until an
external audit found it:** under a 30,000-token budget, every session in
this corpus large enough to matter reports `budget_met: False` — the
budget could not actually be met, so the pipeline kept the entire protected
(critical) set rather than cutting into it. Under invariant 1, that is
correct behavior — but it also means **100% critical retention in this row
is guaranteed by construction, not evidence that ranking-under-pressure
chose well.** `render_report`/`to_json` (`bench/deterministic.py`) now print
`budget_met` per case so this cannot go unnoticed again; running
`verity bench ... --budget 30000` on this project's own sessions currently
reports 2 of 6 cases meeting budget, and the tool itself prints
`NOT PUBLISHABLE` on that exact run. A number this project quotes externally
needs either a budget that is actually met, or the caveat stated alongside
it — this section had neither before this note.

Stated honestly: this corpus is developer conversation, not a financial
domain, and most of those 21 figures are example amounts inside this
project's own docstrings and tests, not genuine user data. A 100% result
here says the mechanism works on what this corpus contains — it is not yet
evidence about a real adversarial case (one figure, once, amid heavy noise,
with decoy numbers nearby). The numeric-recall pilot below tests exactly
that instead of assuming it.

Reproducibility is also more limited than the "reproduce it yourself"
framing below implies: the session transcripts are private and were never
committed, so a third party can run the same *mechanism* on their own
sessions but can never reproduce, check, or falsify these specific published
numbers.

Reproduce it yourself (only the aggregate counts leave your machine — see
`tests/unit/test_bench_privacy.py` for what's enforced never to appear in
the output):

```bash
verity bench ~/.claude/projects/-Your-Project-Path/*.jsonl
verity bench ~/.claude/projects/-Your-Project-Path/*.jsonl --budget 30000 --task "..."
```

### Family B: is a difference real, or noise?

Family A never answers "does Verity change a task's outcome" — only "how
many tokens did this transform." For that, `bench/repetition.py` (rescued
and generalized from the pre-pivot research programme, see
[ADR-0010](adr/0010-repetition-rescued.md)) implements the protocol's
non-negotiable procedure: establish the noise floor by repeating ONE
configuration N times before ever comparing it to another.

```bash
verity noise-floor within.json between.json --metric success
```

Each file is a JSON array of `{"metric": value}` objects, one per repeat.
`within.json` is N repeats of a single configuration (the floor);
`between.json` is the configuration being compared against it. Fewer than 2
within-repeats is `insufficient_data`, reported as such and never silently
upgraded to a verdict.

**A 20-trial pilot has been run** (`experiments/family_b_pilot/`): two
seeded bugs in a small order-processing service, 5 real agent trials per
(task, condition), each scored by running `pytest` directly — never by
trusting the agent's own report. Result on both tasks:
`indistinguishable_from_noise`, floor `[1.0, 1.0]`. Both conditions hit a
**100% success ceiling** — 20/20 trials fixed the bug — because both seeded
bugs are single-line and `pytest`'s own failure output names the exact
wrong value, so locating the fix (what `verity graph context`/`find`/`deps`
help with) was never the hard part. That is a finding about the pilot's
task design, not a verdict that Verity has no effect — see the pilot's own
README for what a task that could actually detect a difference needs to
look like.

**A second, corrected pilot has been run**
(`experiments/family_b_pilot_2_numeric_recall/`, [ADR-0013](adr/0013-numeric-recall-pilot.md)):
a support-conversation log states a customer's account number and amount
owed once, early, with no explicit marker; a decoy figure (a different,
similarly-formatted account/amount) sits past the midpoint, attributed to
a closed, unrelated case. The harness — not a live agent — prepares each
condition's context against an 800-token *target* two ways: `naive`
keeps the most recent messages (what an unmanaged context window actually
does on overflow), `verity` runs `verity context --budget 800` (with the
ADR-0012 rule active). **Corrected 2026-08-10: the two conditions do not
land on the same actual token count** — re-running
`prepare_contexts.py --budget 800` today produces `naive_context.txt` at
672 tokens and `verity_context.txt` at 899 tokens, 34% more content in the
`verity` arm. `--budget` is the input each side is asked to respect, not a
guarantee both outputs land at the same size, and the ranked/protected
selection that `verity context` performs can legitimately keep more than a
raw tail-truncation would. Since the metric is recall of a figure the
`naive` arm provably never receives, this is a real confound worth stating
plainly: some of the recall gap could reflect `verity` simply carrying more
content, not only better selection of the same amount. 5 single-turn recall
trials per condition, scored by exact match against ground truth:

| Condition | Exact matches | Noise floor |
|---|---|---|
| `naive` | 0/5 | `[0.0, 0.0]` |
| `verity` | 5/5 | `[1.0, 1.0]` |

`likely_real_difference` in both directions — zero overlap between the
floors. Every `naive` trial correctly said "insufficient information"
rather than reporting the decoy; every `verity` trial reported the exact
account number and amount. Stated limits (the pilot's own README has the
full list): one fixture, one figure, one budget, one truncation strategy,
and a single-turn task close enough to deterministic that neither
condition's noise floor shows real spread.

**A third pilot has been run**
(`experiments/family_b_pilot_3_agent_memory/`, [ADR-0014](adr/0014-agent-driven-memory-pilot.md)),
closing the gap both prior pilots left open: whether an agent *managing its
own context across turns* gets any benefit, rather than a harness handing it
one already-prepared. Each turn of each trial is a fresh, memoryless agent
call — a genuine sliding window, not a simulated one. `naive` agents get
nothing to persist with; `verity` agents get a `.verity/` directory and the
knowledge that `verity remember`/`verity handoff` exist, and decide for
themselves, unprompted, whether to use them. Result, 5 trials per condition
across 4 turns each:

| Condition | Exact matches | Noise floor |
|---|---|---|
| `naive` | 0/5 | `[0.0, 0.0]` |
| `verity` | 5/5 | `[1.0, 1.0]` |

`likely_real_difference` in both directions. Every `verity` trial chose, on
its own, to persist the figure in turn 1 and recall it in turn 4, correctly
ignoring the turn-3 decoy every time. Two real bugs were caught before the
result could be trusted — an external safety classifier blocking an
IBAN-shaped string as a credential, and a shell-quoting bug that silently
corrupted a `$`-prefixed amount — both documented in the pilot's own README
and ADR-0014, neither a finding about the underlying mechanism.

**A fourth pilot has been run**
(`experiments/family_b_pilot_4_recovery_after_reset/`, [ADR-0015](adr/0015-recovery-after-reset-pilot.md)),
testing `BENCHMARK_PROTOCOL.md`'s still-unmeasured "recovery after
reset" claim without a subjective judge: a two-hop bug (harder than the
first pilot's single-line one) in a small repo, a fabricated prior
investigation persisted via `verity task`/`remember`, and a fresh agent
that either has nothing to recover from (`naive`) or runs `verity handoff`
first (`verity`). Both scored by an independent `pytest` run, never the
agent's own report:

> **Evidence caveat (added 2026-08-10, Phase 0 truth repair):** the
> per-trial `trials/` directories that would let these numbers be
> independently re-derived were later destroyed by a re-run of the pilot's
> own setup script (now guarded — see `experiments/lib/setup_phase_a.sh`);
> nothing under `trials/` was ever git-tracked. The table below rests on
> the hand-recorded results JSON alone; the tool-call figures were
> self-reported at the time, not captured by an instrumented harness. Not
> independently verifiable, **permanently**: `tool_uses` counts an agent's
> tool calls -- a property of behaviour, not of the code left behind -- so no
> fixture-and-scorer harness can regenerate it, `verity eval` included.
> See `experiments/UNREPRODUCIBLE.md`.

| Metric | `naive` | `verity` | Verdict |
|---|---|---|---|
| Task success (5 trials) | 5/5 | 5/5 | `indistinguishable_from_noise` (ceiling) |
| Tool calls per trial | mean 6.6, floor `[5, 8]` | mean 4.8 | `likely_real_difference` |

Another ceiling on raw success — the bug, while harder than the first
pilot's, still wasn't hard enough for a capable agent to fail cold. But
recovery made the *same* successful outcome cheaper: every `verity` trial
read the handoff and went nearly straight to the fix; every `naive` trial
spent extra tool calls re-deriving the call chain the handoff had already
named. See the pilot's own README and ADR-0015 for the full caveats.

**A fifth pilot raised the difficulty further**
(`experiments/family_b_pilot_5_harder_recovery/`, [ADR-0016](adr/0016-harder-recovery-pilot.md))
to test whether success itself, not just cost, would ever move: two
structurally identical subsystems, one healthy (a decoy) and one broken,
so a cold agent has to rule out a plausible dead end, not just trace one
chain. Result: **still a ceiling**, the third in this series (after
ADR-0011 and ADR-0015) — 10/10 both conditions found the real bug, none
touched the decoy or the test. But the cost effect held and grew:

> **Evidence caveat (added 2026-08-10, Phase 0 truth repair):** same
> issue as pilot 4 — the `trials/` directories were destroyed by a later
> re-run of the setup script (now guarded) and were never git-tracked;
> the tool-call figures below are self-reported, not harness-captured. Not
> independently verifiable, **permanently**: `tool_uses` counts an agent's
> tool calls -- a property of behaviour, not of the code left behind -- so no
> fixture-and-scorer harness can regenerate it, `verity eval` included.
> See `experiments/UNREPRODUCIBLE.md`.

| Metric | `naive` | `verity` | Verdict |
|---|---|---|---|
| Task success (5 trials) | 5/5 | 5/5 | `indistinguishable_from_noise` (ceiling) |
| Tool calls per trial | mean 8.0, floor `[7, 10]` | mean 5.2 | `likely_real_difference` |

A fixture bug was caught before this result could be trusted: the first
draft's source had an explicit `# BUG: ...` comment left over from writing
it, and all 10 trials of that run "found" it by reading the comment. Caught
because several trial reports cited "a comment in the source," removed, and
every trial re-run from a verified-clean fixture. Across pilots 4 and 5,
recovery now has two reproductions of the same pattern: it makes an
already-achievable outcome cheaper, and the saving grows with how much
investigation a reset would have thrown away — see ADR-0016 for the full
discussion of why success itself may need a qualitatively different kind of
bug (runtime reasoning, not static tracing) to move.

**A sixth pilot changed the bug's shape entirely**
(`experiments/family_b_pilot_6_runtime_bug/`, [ADR-0017](adr/0017-runtime-bug-pilot.md)):
instead of a wrong-but-plausible constant, a cache keyed only by `item`
that silently returns a stale price when the same item is requested under
a different `tier` — invisible from reading the function in isolation,
only found by tracing the actual sequence of calls. Result: **a fourth
ceiling**, 5/5 both conditions, but the cost effect reproduced a third
time:

> **Evidence caveat (added 2026-08-10, Phase 0 truth repair):** same
> issue as pilots 4 and 5 — the `trials/` directories were destroyed by a
> later re-run of the setup script (now guarded) and were never
> git-tracked; the tool-call figures below are self-reported, not
> harness-captured. Not independently verifiable, **permanently**:
> `tool_uses` counts an agent's tool calls -- a property of behaviour, not of
> the code left behind -- so no fixture-and-scorer harness can regenerate it,
> `verity eval` included. See `experiments/UNREPRODUCIBLE.md`.

| Metric | `naive` | `verity` | Verdict |
|---|---|---|---|
| Task success (5 trials) | 5/5 | 5/5 | `indistinguishable_from_noise` (ceiling) |
| Tool calls per trial | mean 8.0, floor `[7, 9]` | mean 4.2 | `likely_real_difference` |

Four ceilings across four different bug designs is now a pattern, not a
fluke: current models rarely fail cold on a single-agent-turn, statically
traceable bug in a small repo, regardless of whether it's a config swap, a
two-subsystem decoy, or a call-sequence dependency. ADR-0017 argues success
itself may need a fundamentally different kind of difficulty (or a much
larger trial budget) to move — while the cost effect is now this project's
most consistently reproduced Family B result, across three separate
pilots and bug designs.

**A seventh pilot tried a fundamentally different kind of difficulty**
(`experiments/family_b_pilot_7_domain_ambiguity/`, [ADR-0019](adr/0019-domain-ambiguity-pilot.md)):
instead of a bug with one answer derivable from the code, a business-rule
ambiguity the code cannot resolve at all — does the exact day a grace
period ends still count as within grace? The one visible test was
deliberately built so both interpretations (`>` and `>=`) pass it
identically; a second, hidden test (never shown to any trial) checks which
one the fix actually implements, scored independently by the harness:

| Metric | `naive` | `verity` | Verdict |
|---|---|---|---|
| Visible test passes (5 trials) | 5/5 | 5/5 | ceiling, as designed |
| Correct at the hidden boundary (5 trials) | 5/5 | 5/5 | `indistinguishable_from_noise` |

A fifth ceiling — but for a new reason. Every `naive` trial independently
chose the strict `>` comparison with no access to the fabricated decision
and no prompt toward the boundary case at all. The ambiguity was real (no
code states the policy), but "grace period" carries a strong enough
linguistic convention that the model's default matched the fabricated
policy regardless of condition. ADR-0019 keeps the hidden-test design —
a real technique for scoring "correct for the right reason" versus
"happened to pass" — and proposes the next pilot use an ambiguity with no
dominant convention (a coin-flip-shaped choice, not a language-shaped one).

> **Evidence:** retrofitted onto a retained artifact
> ([ADR-0031](adr/0031-pilot-7-retrofit.md)) —
> `experiments/family_b_pilot_7_domain_ambiguity/evidence/` holds a diff,
> scorer output and a fixture-pinned manifest for all 10 trials, reconstructed
> from the code each trial actually left behind. `verity verify
> experiments/family_b_pilot_7_domain_ambiguity/evidence` re-derives the table
> below exactly. The gate still reports this run NOT PUBLISHABLE (a
> reconstruction that replays one fixed script per condition has no trial-to-
> trial variance for the noise-floor check to observe) — see the ADR for why
> that is a structural property of retrofitting a ceiling, not a defect in
> the number.

**An eighth pilot found that ambiguity, and the ceiling broke.**
(`experiments/family_b_pilot_8_arbitrary_tiebreak/`, [ADR-0020](adr/0020-arbitrary-tiebreak-pilot.md)):
same hidden-test design, but the ambiguity is a tie-break rule between two
opaque numeric IDs — nothing in the naming suggests an answer. The hidden
test lists the correct (per the fabricated policy) tied candidate second
in the input, so the most natural-looking fix
(`max(candidates, key=lambda c: c["score"])`) returns the *wrong* one on a
tie, because Python's `max` keeps the first-seen maximum:

| Metric | `naive` | `verity` | Verdict |
|---|---|---|---|
| Visible test passes (5 trials) | 5/5 | 5/5 | ceiling, as designed |
| Correct on the hidden tie (5 trials) | 0/5 | 5/5 | `likely_real_difference` |

The first genuine success-rate split in eight Family B pilots. Every
`naive` trial wrote the idiomatic `max()` one-liner — reasonable, passes
the visible test, and wrong on a tie for a reason nothing visible reveals.
Every `verity` trial read the handoff's explicit warning about `max()`'s
tie behavior and wrote a tie-break comparison instead. The mechanism
wasn't difficulty — pilot 7's task was arguably harder to reason about —
it was the *absence of any inferable signal* in the ambiguity itself. See
ADR-0020 for what made this design succeed where pilot 7's didn't, and its
own stated limits (N=5, one fixture, a decision that named the rule
explicitly).

> **Evidence:** the only Family B pilot with a retained artifact —
> `experiments/family_b_pilot_8_arbitrary_tiebreak/evidence/` holds a
> per-trial diff, scorer output and a manifest pinned to the fixture's
> hash, produced by the committed `eval_spec.json`. Re-derive the table
> above with `verity verify experiments/family_b_pilot_8_arbitrary_tiebreak/evidence`.

**Pilot 9 (long-log context recovery,
`experiments/family_b_pilot_9_long_log_recovery/`) measures the claim this
project's `context/` engine actually exists to support: recovering a signal
that got buried in a long conversation, not making a fresh agent faster.**
Three synthetic ~12,000-character session logs, each with one real fact
stated once mid-log and a decoy near the tail engineered to read as "the
headline finding." At a 25%-of-total token budget, naive tail-truncation
lost the real fact and kept the decoy in all three; `verity context`
recovered the real fact in all three. `naive: 0/3  verity: 3/3`. No agent in
the loop — both arms are deterministic, so n=1 per fixture is the correct
Family A protocol here despite the pilot's Family-B-shaped design, and the
evidence is re-derivable by re-running `generate_fixture.py` +
`run_pilot9.py` (confirmed byte-identical across two runs before being
retained). This result only worked after
[ADR-0033](adr/0033-user-messages-are-not-unconditionally-critical.md) — the
first attempt at this pilot lost the signal identically to naive, because
every generic user turn was unconditionally `CRITICAL` and starved the
budget before ranking ever ran. See
[`experiments/UNREPRODUCIBLE.md`](../experiments/UNREPRODUCIBLE.md) for why
this pilot exists as a rebuild rather than an original result.

**A companion pilot on the opposite question,
`experiments/family_b_pilot_10_fresh_agent_speed/`, found no consistent
turns/cost advantage for a fresh agent with an MCP memory handoff solving a
*known* bug, in either direction.** Two bugs this project found in itself
(pinned to their pre-fix commits, `b320239` and `cb64bd0`, as the fixture —
no external repo needed), three repetitions per condition, all 12 trials
independently verified by re-running the real test suite against each
trial's own checkout. Result: verity was 18.3% *faster* on one bug and
22.0% *slower* on the other. Direction flipping between bugs at this sample
size is consistent with noise, not a real effect — the honest reading,
together with an earlier same-session attempt at this exact question that
found verity worse in all three bugs tested but lost its raw evidence to a
mid-session `/compact` (see `experiments/UNREPRODUCIBLE.md`), is that this
tool's value is recovering context that was already lost (pilot 9, above),
not accelerating an agent that still has full access to the repo.

**A preliminary n=1 check against a real (not synthetic) session,
`experiments/family_b_pilot_11_real_compaction/`, points the same direction
as pilot 9.** A real Claude Code session grown past the `--autocompact`
floor (100k tokens, the only scriptable trigger — `/compact` itself is
REPL-only) lost a specific detail under `naive` ("not stored in memory —
need re-read") that `verity handoff` recovered near-verbatim under
`verity`. Not a Family B result — n=1, no noise floor, stopped after one
pair because a second condition's run hit the account's Claude Pro
rate-limit window mid-session. Retained as a real-session data point and as
the cost/design baseline (~$1/session) for a proper N≥5 run later.

**The Consistency Engine got its own first real measurement**
(`experiments/consistency_pilot_1_hallucination_detection/`, [ADR-0018](adr/0018-consistency-engine-first-measurement.md)),
closing a stale claim in `BENCHMARK_PROTOCOL.md` that this engine was
still "blocked on existing at all" — it already existed (ADR-0007) and just
lacked a measurement against real, not hand-authored, claims. Real agents,
shown only one file of a small codebase, were asked to describe it anyway,
producing a genuine mix of true and hallucinated claims:

| Claim class | Result |
|---|---|
| Invented function names (14 across 5 trials) | **100% caught** |
| Function-to-file relation hallucinations (~6) | **0% caught at the time** — structurally invisible to the relation extractor |
| Backtick-quoted local variable names (8) | False positives — real names, just not graph-indexed |

A real bug was also found and fixed in decision resurfacing: with only one
or two rejected decisions on record, the closest-of-the-available matches
always normalized to 100% confidence regardless of actual relevance — a
genuinely unrelated proposal "resembled" an unrelated rejected decision just
as strongly as an actual paraphrase of it. Confirmed with an isolated probe,
fixed by normalizing against the checked text's own best-possible score
instead of the in-corpus max, and locked in with a regression test.

**The function-to-file relation blind spot, and a real ingester bug behind
it, were addressed the same day — but the fix initially overcorrected, and
was itself corrected the next day ([ADR-0021](adr/0021-consistency-relation-inversion.md)).**
The relation target pattern now accepts file paths, and a new check
(`check_symbol_calls_file`) checks the claim against the file-level
`IMPORTS` graph. That surfaced a second, independent bug: the ingester
recorded `from billing import tax_rates` as importing only the bare
package, never the real submodule file — `billing/tax.py` genuinely does
import `billing/tax_rates.py`, but the graph didn't know it. Fixed in
`graph/ingest.py` by trying the more specific candidate first.

At that point the exact probe this measurement used to demonstrate the
original gap — `` `apply_tax` calls `billing/tax_rates.py` `` — reported
`SUPPORTED`, on the reasoning that the import genuinely exists. That
verdict was wrong: the pilot's own ground truth labels this claim FALSE —
`apply_tax` only reads `tax_rates.REGION_RATES`, a dict, and never calls
anything. An `IMPORTS` edge is necessary but not sufficient evidence for a
`calls` claim, and treating it as sufficient reintroduced the exact failure
this fix was meant to close, just inverted: a silent false negative became
an asserted false positive, at 100% confidence. The blind spot is now
**narrowed, not closed**: `check_symbol_calls_file` reports `UNVERIFIABLE`
when only an import is found (correct — the graph has no line-level call
evidence either way), and still correctly reports `CONTRADICTED` when the
target file isn't imported at all. See ADR-0018 for the original sequence
and ADR-0021 for the correction, including the relation-extraction gap that
remains open in both (loose phrasing like "likely calls a helper in").

---
