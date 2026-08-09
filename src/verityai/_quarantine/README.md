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
| `repetition.py` | `evaluation/repetition.py` | Phase 1 benchmark | Depends on `evaluation.metrics.BenchmarkOutcome`, deleted. Needs generalizing from a classification-metrics shape to arbitrary metric dicts. |

`rule_engine.py` left quarantine in Phase 4 — see `reliability/rule_engine.py`.
`Rule` and `VerificationStatus` now live in `core/models.py`.

## Why it's kept

**`repetition.py`** implements the standing rule from `docs/RESEARCH_FINDINGS.md`:
never attribute a metric difference to a mechanism without a same-configuration
repeat. It is what turned the retry-loop claim from a result into a retraction.
The benchmark work in Phase 1 must not reimplement this from scratch — the
whole point of the rule is that it is applied consistently.
