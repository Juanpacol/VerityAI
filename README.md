# VerityAI

**Give AI agents the right context. Verify what they do.**

VerityAI is a model-agnostic agentic harness for AI-assisted software
engineering. It provides context management, persistent structured memory,
consistency checking, and engineering verification around AI coding agents.

Verity does not replace Claude, Codex, Gemini or Cursor. It controls the
environment those agents work in.

```
                    USER
                     │
                     ▼
                AI AGENT
          Claude / Codex / Gemini
                     │
                     ▼
          ┌─────────────────────┐
          │       VERITY        │
          │   AGENTIC HARNESS   │
          └──────────┬──────────┘
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
       CONTEXT    MEMORY   CONSISTENCY
          │          │          │
          └──────────┼──────────┘
                     ▼
                 CODEBASE
```

---

## Status

**Phase 4 of 5.** All four engines -- Context, Memory, Knowledge Graph,
Consistency and Reliability -- work, are covered by 506 tests, and are
reachable from both a CLI and an MCP server. Phase 5 (deeper agent
integrations and a UI) has not started.

| Engine | State |
|---|---|
| Context — token accounting, classification, pruning, health | working |
| Memory / Handoff — persistent state, snapshots, handoff docs | working |
| Knowledge Graph — real code structure | working |
| Consistency — hallucinated and contradictory claims | working |
| Reliability — architecture, tests, security | working |

**A Family A measurement and six Family B pilots exist now** (below). The
first Family B pilot's result was `indistinguishable_from_noise` — its
tasks were too easy to tell the two conditions apart. The second, testing
automatic financial-figure protection under a real budget constraint,
found `likely_real_difference`: 0/5 vs 5/5 exact recall. The third, testing
whether an agent *managing its own context across turns* uses a memory tool
unprompted, found the same verdict: 0/5 vs 5/5. The fourth, fifth, and
sixth, testing recovery after a context reset across three different bug
designs (a config swap, a two-subsystem decoy, a call-sequence-dependent
cache bug), all hit a success-rate ceiling (5/5 or 10/10 both conditions)
but found `likely_real_difference` on cost every time: recovering a handoff
took fewer tool calls than reconstructing the same investigation from
scratch, consistently, across all three designs.

---

## First real measurement (2026-08-09, updated same day)

Measured on 3 real Claude Code development sessions of this same project
(session transcripts read directly from `~/.claude/projects/`, never
committed — see [ADR-0009](docs/adr/0009-family-a-real-measurement.md)),
~3.48M tokens combined, `tiktoken:cl100k_base`. Every prior figure in this
project's history was synthetic; this is the first that isn't. (The
session count grew slightly and a new protection rule was added the same
day — see [ADR-0012](docs/adr/0012-financial-figure-protection.md) — so
these numbers are re-measured, not the original ADR-0009 figures.)

| Configuration | Tokens before | Tokens after | Reduction | Critical retained | Digit retained |
|---|---:|---:|---:|---:|---:|
| No budget (dedup + noise filter + compression only) | 3,482,198 | 3,442,382 | **1.1%** | 100% | 100% |
| 30,000-token budget, ranked against the task | 3,483,993 | 1,508,198 | **56.7%** | 100% | 100% |

Two numbers, not one, because they answer different questions. The first is
what pruning removes for free, with nothing forced out — real sessions
turned out to carry far less exact-duplicate/dead-noise content than the
92.4% figure from the synthetic fixture that this project's own
`docs/BENCHMARK_PROTOCOL.md` already flags as what not to publish. The
second is what happens once a real budget forces a choice, and it is the
number that matters for "can this fit in a smaller context" — with the two
invariants that have to hold under that pressure (nothing marked critical
is dropped; no financial figure — amount, account number — is dropped)
holding exactly at 100% in both real runs. 21 distinct financial figures
were present in this corpus; none were vacuous zero-figure runs.

Stated honestly: this corpus is developer conversation, not a financial
domain, and most of those 21 figures are example amounts inside this
project's own docstrings and tests, not genuine user data. A 100% result
here says the mechanism works on what this corpus contains — it is not yet
evidence about a real adversarial case (one figure, once, amid heavy noise,
with decoy numbers nearby). The numeric-recall pilot below tests exactly
that instead of assuming it.

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
[ADR-0010](docs/adr/0010-repetition-rescued.md)) implements the protocol's
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
(`experiments/family_b_pilot_2_numeric_recall/`, [ADR-0013](docs/adr/0013-numeric-recall-pilot.md)):
a support-conversation log states a customer's account number and amount
owed once, early, with no explicit marker; a decoy figure (a different,
similarly-formatted account/amount) sits past the midpoint, attributed to
a closed, unrelated case. The harness — not a live agent — prepares each
condition's context to the *same* 800-token budget two ways: `naive`
keeps the most recent messages (what an unmanaged context window actually
does on overflow), `verity` runs `verity context --budget 800` (with the
ADR-0012 rule active). 5 single-turn recall trials per condition, scored
by exact match against ground truth:

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
(`experiments/family_b_pilot_3_agent_memory/`, [ADR-0014](docs/adr/0014-agent-driven-memory-pilot.md)),
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
(`experiments/family_b_pilot_4_recovery_after_reset/`, [ADR-0015](docs/adr/0015-recovery-after-reset-pilot.md)),
testing `docs/BENCHMARK_PROTOCOL.md`'s still-unmeasured "recovery after
reset" claim without a subjective judge: a two-hop bug (harder than the
first pilot's single-line one) in a small repo, a fabricated prior
investigation persisted via `verity task`/`remember`, and a fresh agent
that either has nothing to recover from (`naive`) or runs `verity handoff`
first (`verity`). Both scored by an independent `pytest` run, never the
agent's own report:

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
(`experiments/family_b_pilot_5_harder_recovery/`, [ADR-0016](docs/adr/0016-harder-recovery-pilot.md))
to test whether success itself, not just cost, would ever move: two
structurally identical subsystems, one healthy (a decoy) and one broken,
so a cold agent has to rule out a plausible dead end, not just trace one
chain. Result: **still a ceiling**, the third in this series (after
ADR-0011 and ADR-0015) — 10/10 both conditions found the real bug, none
touched the decoy or the test. But the cost effect held and grew:

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
(`experiments/family_b_pilot_6_runtime_bug/`, [ADR-0017](docs/adr/0017-runtime-bug-pilot.md)):
instead of a wrong-but-plausible constant, a cache keyed only by `item`
that silently returns a stale price when the same item is requested under
a different `tier` — invisible from reading the function in isolation,
only found by tracing the actual sequence of calls. Result: **a fourth
ceiling**, 5/5 both conditions, but the cost effect reproduced a third
time:

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

**The Consistency Engine got its own first real measurement**
(`experiments/consistency_pilot_1_hallucination_detection/`, [ADR-0018](docs/adr/0018-consistency-engine-first-measurement.md)),
closing a stale claim in `docs/BENCHMARK_PROTOCOL.md` that this engine was
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
it, were both closed the same day.** The relation target pattern now
accepts file paths, and a new check (`check_symbol_calls_file`) verifies
the claim against the file-level `IMPORTS` graph. That surfaced a second,
independent bug: the ingester recorded `from billing import tax_rates` as
importing only the bare package, never the real submodule file —
`billing/tax.py` genuinely does import `billing/tax_rates.py`, but the
graph didn't know it. Fixed in `graph/ingest.py` by trying the more
specific candidate first. The exact probe this measurement used to
demonstrate the original gap — `` `apply_tax` calls `billing/tax_rates.py` ``
— now correctly reports **`SUPPORTED`** (the import genuinely exists);
a second probe against a file `apply_tax`'s module never imports still
correctly reports `CONTRADICTED`, confirming the mechanism works once the
underlying import graph is accurate. See ADR-0018 for the full sequence,
including the one relation-extraction gap that remains open (loose
phrasing like "likely calls a helper in").

---

## Why this project changed shape

VerityAI used to generate code with an LLM and prove it correct with Z3. A
research programme (T1–T6, recorded in
[`docs/RESEARCH_FINDINGS_LEGACY.md`](docs/RESEARCH_FINDINGS_LEGACY.md)) was run
to find out whether that worked. Mostly it did not:

- **T3** measured the verifiable Python subset across all of HumanEval and
  MBPP with the real converter: **6.1% and 9.4% coverage.** Formal proof over
  arbitrary generated code does not reach most real programs.
- **T2** *retracted* a previously written-up improvement. Same-configuration
  runs on different days disagreed 50% of the time — indistinguishable from
  sampling noise.
- **T1** found the confidence score uncalibrated, and inverted in one
  configuration: its least-confident verdicts were its most accurate.
- **T6** found what does work. Deterministic AST fact extraction plus a rule
  engine caught vulnerabilities Z3 structurally cannot, and exposed a real bug
  in the process — a function that could never return `FAIL`, and had been
  reporting `PASS` on genuinely vulnerable code.

T3 and T6 together say the differentiator was never the theorem prover. It is
deterministic analysis over project structure, with the model confined to
questions that are genuinely semantic.

The full reasoning is in
[ADR-0005](docs/adr/0005-agentic-harness-pivot.md). The negative results are
the most valuable thing this project has produced, and they are why the bar
for publishing a number here is set where it is.

---

## Install

```bash
git clone https://github.com/yourname/VerityAI
cd VerityAI
pip install -e ".[dev,tokenizers]"
```

The core depends on `pydantic`, `typer`, `rich` and `python-dotenv` — nothing
else. A harness that manages someone else's context has no business dragging in
an LLM SDK, a graph database or a solver.

`tokenizers` adds `tiktoken` for exact counts. Without it everything still
works on a chars/4 estimate, and every report says so.

---

## Use

```bash
verity init                    # create .verity/ in your repo

# Measure a context without changing it
verity ingest transcript.json

# Prune toward a budget, with a full stage-by-stage ledger
verity context transcript.json --budget 20000 --task "add rate limiting"

# Multi-dimensional health, not just "73% full"
verity health transcript.json

# Record state that must survive a context reset
verity task "add rate limiting" --next "write the burst test"
verity remember decision "token bucket per key" --why "fixed window rejected bursts"
verity remember constraint "no new Redis dependency"
verity remember failure "fixed window counter" --error "rejected legitimate bursts"

# Generate a handoff for a cold session
verity handoff --budget 2000

verity snapshot "before the refactor"
verity restore 1
```

### What `verity health` shows

Not a fullness percentage — that measures the container, not the contents:

```
VERITY CONTEXT HEALTH

  Window usage          41.1%
  Relevant context       9.2%
  Critical retained    100.0%
  Redundancy            90.8%
  Tool noise            92.0%
  Stale facts               0
  Contradictions            0

  Total tokens         52,648  [tiktoken:cl100k_base]

  Health                45.8%
```

### The code graph

```bash
verity graph build                          # index the repo (incremental)
verity graph context "rate limiting"        # relevant code, by relationship
verity graph find ContextPipeline           # where is this defined
verity graph deps src/verityai/graph/query.py
verity graph cycles                         # circular imports; exits 1 if any
verity graph untested                       # symbols with no direct test edge
```

`graph context` is the part worth explaining. It seeds on text, then walks
call, containment, inheritance and test edges. Asked about "rate limiting" in
this repository it returns `ContextPipeline.run` — which does not contain the
phrase — because `run` calls `_enforce_budget`, which does. It returns
`critical_retention` because tests reach it. Those are edges, and edges are
what text similarity structurally cannot see.

Every result says why it was included:

```
  method    ContextPipeline.run
            src/verityai/context/prune.py:62
            run(self, items: list[ContextItem], task: str, budget: int | None)
            why: calls _enforce_budget; called by test_critical_items_survive...
```

Scope is declared rather than implied. Python only, for now; every other file
is reported as not read, and vendored subprojects (a directory with its own
`pyproject.toml`) are excluded and counted separately:

```
50/50 Python files in the graph (100%); 9 in nested projects (vendored, not
yours); 1,266 non-Python files not read (Phase 2 is Python-only)
```

### Checking claims

```bash
verity check response.md      # or pipe text on stdin with -
```

Extracts checkable assertions from backtick-quoted spans and a closed set of
relation phrases (`` `A` calls `B` ``), then checks each against the code
graph and against decisions already rejected or superseded in `.verity/`. No
model is involved — a claim it cannot extract this way is not checked, never
guessed at, the same discipline ADR-0001 applied to formal verification.

```
  [OK  ] ContextPipeline.run calls _enforce_budget
         a resolved calls edge connects them (confidence 100%)
  [FAIL] AuthService.refresh_token
         no definition of 'AuthService.refresh_token' found anywhere in the graph
  [FAIL] use a global mutable cache for session state
         resembles a rejected decision: 'use a global mutable cache for session
         state' (caused race conditions under concurrent requests) (confidence 85%)

  2 contradiction(s) of 3 checked claim(s)
```

Exits non-zero on any contradiction, so it is usable as a pre-merge or CI
check. Decision-resurfacing confidence is capped below 1.0 always — it is a
lexical-overlap heuristic, not a graph lookup, and must never read as more
certain than one.

### Reliability: security and architecture

```bash
verity reliability security                 # SQL injection, check-then-act races
verity reliability architecture             # import policy vs. the real graph
```

Both are pattern-matching and graph algorithms — no model, no solver. A
security finding means "worth a human look," never a proof; a clean scan
means "not this exact shape," never "no vulnerabilities." Every rule that
fires prints its own documented blind spot alongside the result:

```
SECURITY

  [MEDIUM] No Check-Then-Act Race  (src/verityai/graph/query.py)
           Rule No Check-Then-Act Race violated: precondition present, ...

  1 violation(s) across 61 files scanned

  note: This rule matches a syntactic shape (check membership, then mutate
  the same container, unguarded) -- it cannot tell whether the container is
  actually shared across threads/processes.
```

`architecture` checks every import against a declared policy — which
top-level package may depend on which — against the real graph, not a
snapshot in someone's head. Running it against this repository for the first
time found real drift: `memory/handoff.py` imports `context.tokenizer` for
its token budget, which the architecture diagram in `CLAUDE.md` didn't list.
The import was legitimate; the diagram was stale. See
[ADR-0008](docs/adr/0008-reliability-engine.md).

---

## Use it from an agent (MCP)

```bash
pip install -e ".[mcp]"
claude mcp add verity -- verity-mcp
```

Nineteen tools, each a thin wrapper over the same functions the CLI calls:

- **context** — `optimize_context`, `context_health`
- **memory** — `set_task`, `save_decision`, `save_constraint`, `save_discovery`,
  `save_failure`, `get_state`, `handoff`
- **graph** — `build_code_graph`, `find_relevant_code`, `check_symbol_exists`,
  `impact_of_changing`
- **consistency** — `check_claims`
- **reliability** — `check_security`, `check_architecture`
- **snapshots** — `snapshot`, `restore`, `list_snapshots`

`check_symbol_exists` is the one to reach for before asserting an API is
available. It answers `NOT FOUND: no definition of 'refresh_token' in this
repository. Do not assume it exists.` — far cheaper than opening files, and the
difference between believing and knowing.

`check_claims` is the same idea applied to a whole draft response: call it on
your own output before sending it, and it flags every backtick-quoted
assertion the graph or memory actually contradicts, plus anything that
resembles a decision already ruled out.

**What this can and cannot do**, stated plainly because it bounds every claim
this project makes:

- Verity **cannot see the agent's real context window.** MCP is cooperative —
  the agent calls a tool and gets an answer, and there is no interception
  point. "We pruned your context" is only true of context the agent chose to
  hand over.
- Verity **can** hold state the agent would otherwise lose and hand back a
  reconstruction after a reset. That does not require seeing the window, and
  it is the part that is genuinely hard to do without a harness.

---

## Design rules

These are not style preferences. Each one is a lesson with a research result
behind it.

**Deterministic first, the model only when necessary.** Nothing in the Phase 1
pipeline calls an LLM. Partly principle, mostly measurement: if pruning spent
tokens to decide what to prune, the savings figure would be fiction.

**Every count carries its method.** An exact tiktoken count and a chars/4
estimate are different kinds of number. They never appear as the same number.

**No composite score without its components.** `ContextHealth.score` exists
because people ask for one number, and it is never printed alone. T1 is what a
lone authoritative-looking number does when nobody can audit it.

**Every degraded path says why.** When semantic ranking is unavailable, the
result carries `degraded_reason` rather than quietly returning worse output.

**Critical context is never dropped.** Not by any budget. If the protected set
exceeds the budget, the pipeline goes over and reports it. A harness that
quietly discards a hard constraint to hit a number is worse than no harness.

**No claim without a noise floor.** No A/B number is published until the same
configuration has been repeated enough times to know what its own variance
looks like. This is the rule that turned T2 from a result into a retraction.

---

## Development

```bash
pytest tests/          # 506 tests, no network, no services
ruff check src/ tests/
ruff format src/ tests/
```

Python 3.10+. Raised from 3.9 on 2026-08-09: 3.9 has been end-of-life since
October 2025 and the MCP SDK requires 3.10.

---

## Contact

Juan Pablo Botero Espinosa · juanpabloboteroespinosa@gmail.com
