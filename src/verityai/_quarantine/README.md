# Quarantine

Code rescued from the pre-pivot codebase that is **worth keeping but is not
wired up yet**. Every file here has imports pointing at modules that no longer
exist, so nothing in `verityai/` may import from this package — it exists so
the work is visible in the tree rather than only in git history, where it
would be forgotten.

Each file leaves quarantine when the phase that needs it arrives, and leaving
means: fix the imports, add tests, move it to its real home, delete it here.

| File | Origin | Waiting on | Needs |
|---|---|---|---|
| `rule_engine.py` | `symbolic/rule_engine.py` | Phase 4 — Reliability Engine | Depends on `ontology.models.Rule` / `VerificationStatus`, both deleted. Needs a rule model in `core/models.py` first. |
| `repetition.py` | `evaluation/repetition.py` | Phase 1 benchmark | Depends on `evaluation.metrics.BenchmarkOutcome`, deleted. Needs generalizing from a classification-metrics shape to arbitrary metric dicts. |

## Why these two specifically

**`rule_engine.py`** is the deterministic half of T6, the one research result
that survived the pivot intact. It forward-chains over a set of fact strings
to a fixed point, and its `check_for_violation` is the corrected inversion of
a real bug: the original `apply_rule_to_code` was structurally incapable of
returning `FAIL`, so it reported `PASS` on genuinely vulnerable code. That fix
is worth more than the code around it and must not be lost.
Its companion, `analysis/facts.py`, has no broken imports and is already in
place.

**`repetition.py`** implements the standing rule from `docs/RESEARCH_FINDINGS.md`:
never attribute a metric difference to a mechanism without a same-configuration
repeat. It is what turned the retry-loop claim from a result into a retraction.
The benchmark work in Phase 1 must not reimplement this from scratch — the
whole point of the rule is that it is applied consistently.
