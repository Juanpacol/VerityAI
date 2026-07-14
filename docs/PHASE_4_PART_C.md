# Phase 4 Part C — Web Dashboard

## What shipped

- **`api/dashboard.py`**: self-contained HTML/CSS/vanilla-JS page (no
  build step, no CDN), served at `GET /dashboard`. Three pieces:
  - **Trace viewer** — paste a trace ID, fetches `GET /trace/{id}`, shows
    the generated code, LLM reasoning, and verification status.
  - **Confidence meter** — a CSS meter (fill + lighter track), colored by
    the project's validated status palette (good/warning/serious/critical
    from the dataviz skill's `references/palette.md`), thresholded on the
    confidence value itself (≥0.8 good, ≥0.5 warning, ≥0.2 serious, else
    critical).
  - **KG explorer** — lists algorithms and rules via two new endpoints,
    filterable by language.
- **`kg/client.py`**: new `get_all_rules(language)` method (mirrors
  `get_all_algorithms`), plus `GET /kg/algorithms` and `GET /kg/rules` in
  `api/rest.py`, both backed by a `get_kg_client()` dependency
  (constructed from `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`, overridable
  in tests with a fake Neo4j driver).

## Real bugs found and fixed while wiring this up

Building the KG explorer meant actually *calling* `KGClient`'s read
methods for the first time in this project's history — there was no prior
test coverage for `kg/client.py` at all. That surfaced two real,
previously-undetected bugs, both the same shape:

1. **`Algorithm.description` never set.** `get_algorithm_by_id` and
   `get_all_algorithms` fetched `a.description` from Neo4j but never
   passed it to the `Algorithm(...)` constructor — a required field with
   no default, so both methods would have raised `ValidationError` against
   any real KG data. Fixed by passing it through.
2. **`Rule.condition` never set.** `get_rule_by_id`, `get_rules_by_category`,
   and `get_rules_for_algorithm` all had the identical gap for
   `condition` (also required, no default). Same fix pattern in all three,
   applied consistently in the new `get_all_rules` too.

Also added `Algorithm.verified: bool = True` to the ontology model — it
was already being written by `kg/ingestion.py` and read from Neo4j by
`kg/client.py` (as a dead/silently-ignored kwarg under Pydantic's default
"ignore extra fields" behavior), just never declared on the model itself.
Mirrors `Pattern.verified`, which already existed.

These bugs are exactly why `agent/orchestrator.py`'s KG-context fetch
(`_fetch_kg_context`) is wrapped in a broad `try/except` that degrades to
"no rules injected" on any failure (Phase 2) — that safety net is what
kept this from crashing every KG-backed generation request; it just also
meant KG context was silently never actually working, which is worse in a
quieter way. `tests/unit/test_kg_client.py` now pins all five methods down
directly.

## Design choice: confidence thresholds, not pass/fail status

The meter is driven by the confidence *number*, not directly by
PASS/FAIL — a `NOT_VERIFIED` result with 0.3 confidence and a `FAIL`
result with 0.0 confidence both read as "serious"/"critical" on the same
scale, rather than needing a separate encoding per status enum value.

## Test coverage

`test_kg_client.py` (new, 8 tests — the first ever for this module),
`test_api.py` additions for `/kg/algorithms`, `/kg/rules`, `/dashboard`
(self-contained check: no external URLs/`<link>` tags). Full suite: 397
tests passing.

## Deferred (Phase 4 Part D — not started)

- Docker execution sandbox for running generated code's tests.
- Prompt-injection pentest cases (check overlap with Phase 2's existing
  `neural/prompt_builder.py` injection-mitigation tests first).
- API rate limiting.
- `verityai:latest` Docker image.

With Part C done, Phase 4's A/B/C are complete; only Part D remains for
the phase to close out.
