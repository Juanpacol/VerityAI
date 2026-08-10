# ADR-0026: Risk-Adaptive Verification — tier files and gate rule depth

- **Status**: Accepted
- **Date**: 2026-08-10
- **Context**: a change to an authentication module and a trivial docstring edit do
  not deserve the same verification depth, yet `Rule.severity` is a free-form string
  nothing branches on, and `get_applicable_rules` filters on exactly one axis
  (language). This decision adds a second axis, `risk_tier`, using only signals
  already available in the code graph — blast radius, fan-in, untested symbols, and
  path conventions — requiring no new AST facts and no change to `RuleEngine`.

## Decision

Add `risk_tier: str = "low"` field to `Rule` (gating metadata, never core logic).
Implement `src/verityai/reliability/risk.py` with three functions:

1. **`classify_file_risk(path, query) -> (tier, reasons)`** — tiers one changed
   file as `"low"`, `"medium"`, or `"high"` with the reasons that produced the
   tier, following invariant 5's spirit: a tiered path always says why.

   Signals, all available from `graph` with no new AST facts:
   - **Path convention** (`"auth"`, `"migrations"`, `"api"`, `"security"`,
     `"payment"`, `"billing"` in the path) → `high` unconditionally. A change
     to authentication code warrants deep scrutiny regardless of graph metrics.
   - **Blast radius** (any symbol in the file has 3+ callers) → at least
     `"medium"`.
   - **Fan-in** (2+ other files import this one) → at least `"medium"`.
   - **Untested public symbols** (any public symbol with no test edge) → at least
     `"medium"`. `GraphQuery.untested` over-reports by construction, but that
     signal never downgrades to `"low"` alone; it only upgrades.
   - Nothing matched → `"low"`.

2. **`rules_for_tier(tier, rules) -> list[Rule]`** — every rule whose `risk_tier`
   is at or below `tier`. The filter-then-fire shape `get_applicable_rules`
   already demonstrates for language, now applied to a second axis: a `"high"`
   tier runs all rules; a `"low"` tier runs only rules worth checking on a
   trivial change (most defaults to `"low"`).

3. **`classify_paths(paths, query) -> dict[str, (tier, reasons)]`** — batch entry
   point. CLI passes changed files here, receives a tier-per-path dict.

The two builtin rules in `security.py` are backfilled with tier annotations:
- `sql-injection` → `risk_tier="high"` (database injection warrants deep
  scrutiny on any file)
- `check-then-act-race` → `risk_tier="medium"` (concurrency is worth checking
  on elevated-risk files, but not mandatory on trivial ones)

**Backfilled caveats:** `sql-injection` previously had no caveat in `RULE_CAVEATS`,
now receives one explaining the shape limitations — the rule detects syntactic
patterns (string concatenation with `+` in SQL context) and cannot distinguish
intended vs accidental use or guarantee the data reaches a query executor.

## Consequences

- `core/models.py` gains `Rule.risk_tier: str = "low"` field and the two builtin
  rules are tagged with tier values.
- New module `reliability/risk.py` (~110 lines) — pure functions, no side effects,
  injectable `GraphQuery` for testability.
- 21 new unit tests in `test_reliability_risk.py` covering path signals, blast
  radius, fan-in, untested symbols, tier ordering, rule filtering, and batch
  classification.
- CLI integration (wiring changed paths to `classify_file_risk` → `rules_for_tier`
  for actual rule selection) is stated as **future work**, not done here. Same
  for a measured pilot comparing adaptive-depth verification vs flat-depth.

  **Update, 2026-08-10:** `verity reliability risk` now exists and reports
  tiers with their reasons; `--show-rules` prints what each tier admits.
  Gating a scan by tier is deliberately **still not shipped**, for a reason
  this ADR did not anticipate: both built-in rules are medium/high tier, so
  `rules_for_tier("low")` returns an empty list. A risk-gated scan would run
  **zero** rules on every low-tier file — most of a repository — while
  reporting no violations. That is the T6 failure CLAUDE.md warns about (a
  checker that can never fail), and shipping it would be doing it on purpose.
  The command prints the `low  0/2  -- nothing` line so the coverage hole is
  visible rather than hidden behind a gate; a test pins it, so the day a
  low-tier rule exists, the assertion says so.

  ADR-0028 records a second correction: this tiering silently returned `low`
  for every file whenever the caller's path was not byte-identical to the
  form the ingester stored, and the mock-based tests here could not detect it.
- The blind spots are stated plainly in caveats:
  - Path convention heuristics are lexical patterns, not proof of risk (a file
    named `auth_utils.py` in `src/billing/` triggers high risk by path, even if
    it's truly low-risk).
  - Untested symbol detection over-reports: a symbol with no direct `test` edge
    may still be covered by integration tests outside the graph's scope.
  - Blast radius does not account for indirect call chains (a file with 5 callers
    via a single intermediary is flagged identically to 5 direct callers).
- `analysis/facts.py` carries no line numbers, so this module works at file
  granularity, the same as existing `reliability/` findings. Hunk-level precision
  (only this function is risky, not the whole file) is real future work, not
  simulated here.

## What this does NOT do

- Hunk-level tiering ("this function is high-risk, that one is low") — facts emit
  no line numbers, and correlating diff hunks to AST nodes is deferred.
- Automatic rule consequence tuning ("run only fast rules on trivial files") —
  tuning is orthogonal and belongs in a cost-aware scheduler, not here.
- Measuring the effect of adaptive depth on verification quality — a pilot is
  needed (via `verity eval`) before any claim about this mechanism's impact can
  be published, per invariant 7 (Phase 0).

## Rationale

Risk tiers are already observable in the codebase (high-risk vs low-risk files are
intuitively clear), and the signals that identify them are already in the graph
with no new extraction work needed. Gating rule depth by these signals is
conservative (high-risk files get all checks; low-risk files get a safe subset)
and measurable: a pilot can compare adaptive-depth against flat-depth and quantify
the savings (faster verification on trivial files) vs cost (thoroughness on
high-risk files). The backfilled `sql-injection` caveat closes a documentation
gap that Phase 0 (Truth Repair) identified but did not address.
