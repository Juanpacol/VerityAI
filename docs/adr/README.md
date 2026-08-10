# Architecture decision records

Each ADR records one decision, the constraint that forced it, and what it
cost. They are append-only: a decision that stopped being right gets a
successor and a `Superseded by` pointer, never a silent edit. Several of
these exist because a measurement contradicted an assumption — those are
the ones worth reading first.

**Start here if you are new:** [0005](0005-agentic-harness-pivot.md) (why
this project changed shape), then [0009](0009-family-a-real-measurement.md)
(the first honest measurement, and the bug it found).

## The harness (current)

| # | Decision | Status |
|---|---|---|
| [0005](0005-agentic-harness-pivot.md) | Reposition from Z3 code verification to a model-agnostic agentic harness | Accepted |
| [0006](0006-code-graph-storage.md) | SQLite for the code graph; ingestion scope declared rather than implied | Accepted |
| [0007](0007-consistency-engine.md) | Backtick-only claim extraction; heuristics that report themselves as heuristics | Accepted — measured in [0018](0018-consistency-engine-first-measurement.md) |
| [0008](0008-reliability-engine.md) | Rescue T6's rule engine; check the architecture against its own graph | Accepted |
| [0010](0010-repetition-rescued.md) | Generalize `repetition.py` from classification verdicts to arbitrary metrics | Accepted |
| [0012](0012-financial-figure-protection.md) | Protect financial figures automatically; a real false positive found on real data | Accepted |

## Measurements and pilots

Each of these is an experiment with a verdict, including the ones whose
verdict was "this pilot could not have detected an effect."

| # | Question | Verdict |
|---|---|---|
| [0009](0009-family-a-real-measurement.md) | What does the context pipeline actually save on real transcripts? | 1.1% unbudgeted, 55.2% budgeted — and a methodology bug in the benchmark itself |
| [0011](0011-family-b-pilot-ceiling-effect.md) | Does the harness change a task's outcome? | `indistinguishable_from_noise` — 20/20 both conditions. A ceiling, not a null result. Design superseded by [0013](0013-numeric-recall-pilot.md) |
| [0013](0013-numeric-recall-pilot.md) | Does figure protection survive a real token budget? | `likely_real_difference`, 0/5 vs 5/5 |
| [0014](0014-agent-driven-memory-pilot.md) | Does an agent use a memory tool unprompted across turns? | `likely_real_difference`, 0/5 vs 5/5 — every trial chose to persist and recall |
| [0015](0015-recovery-after-reset-pilot.md) | Does recovering a handoff after a reset change the outcome? | Success ceilinged; cost fell below the noise floor |
| [0016](0016-harder-recovery-pilot.md) | Does a harder bug break that ceiling? | No — third ceiling; cost effect reproduced and grew |
| [0017](0017-runtime-bug-pilot.md) | Does changing the bug's *shape* break it? | No — fourth ceiling; cost effect reproduced a third time |
| [0018](0018-consistency-engine-first-measurement.md) | Does the Consistency Engine catch real hallucinations? | 100% recall on invented symbols; three real bugs found and fixed |
| [0019](0019-domain-ambiguity-pilot.md) | Does an ambiguity not derivable from code at all break the ceiling? | No — the model's naming convention matched the fabricated policy 10/10 regardless of condition |

## Pre-pivot (superseded)

Kept as history. All four describe the Z3 verification system that
[ADR-0005](0005-agentic-harness-pivot.md) replaced; the documents and
evidence they link to live on the `legacy/pre-pivot-research` branch and at
the `pre-harness-pivot` tag.

| # | Decision |
|---|---|
| [0001](0001-verifiable-python-subset.md) | Define the verifiable Python subset for the AST→Z3 converter |
| [0002](0002-parameterized-verification.md) | Bind function parameters to Z3 variables |
| [0003](0003-hybrid-retrieval.md) | Hybrid lexical + semantic retrieval over the rule KG |
| [0004](0004-live-run-streaming.md) | Stream pipeline runs over SSE |

## Format

```markdown
# ADR-NNNN: Title

- **Status**: Accepted | Superseded by [ADR-NNNN](...)
- **Date**: YYYY-MM-DD
- **Context**: one or two sentences on what forced the decision

## Context / Decision / Result / Consequences
```

State consequences honestly, including the ones that argue against the
decision. An ADR that only lists benefits is a press release.
