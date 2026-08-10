"""Risk-Adaptive Verification: tier a changed file, then gate which rules
run against it.

A trivial change and a change to an authentication module do not deserve
the same depth of scrutiny -- but nothing in `reliability/` had any notion
of that before this module. `Rule.severity` is a free-form string nothing
branches on; `get_applicable_rules` (`rule_engine.py`) filters on exactly
one axis, language. This module adds the second axis, `risk_tier`, and
does it entirely from signals already available in `graph` -- blast
radius, test coverage, fan-in, and path conventions -- so it needs no new
AST facts and no change to `RuleEngine` itself (ADR-0026).

What this module does NOT attempt: hunk-level precision ("only this
function in this file is risky"). `analysis/facts.py`'s extractors carry
no line numbers, and `GraphNode.line`/`end_line` exist but nothing here
correlates a diff hunk to them -- that is real future work, not faked here
to look more precise than it is. Tiering is per-file, the same granularity
`reliability/security.py`'s findings already use.
"""

from verityai.core.models import Rule
from verityai.graph.query import GraphQuery

# Path fragments that, on their own, justify treating a file as high risk
# regardless of what the graph says about it -- a deliberately small,
# explicit list rather than a heuristic guess, the same discipline
# `_RELATIONS` in `consistency/claims.py` uses for relation phrases.
_HIGH_RISK_PATH_MARKERS = ("auth", "migrations", "api", "security", "payment", "billing")

_TIER_ORDER = {"low": 0, "medium": 1, "high": 2}


def _path_signal(path: str) -> str | None:
    lowered = path.lower()
    return next((marker for marker in _HIGH_RISK_PATH_MARKERS if marker in lowered), None)


def classify_file_risk(path: str, query: GraphQuery) -> tuple[str, list[str]]:
    """Tier one changed file as "low", "medium", or "high", with the
    reasons that produced the tier -- never a bare label (invariant 5's
    spirit: a degraded/triggered/tiered path always says why).

    Signals, all already available from `graph` with no new AST facts:
    - **Path convention**: a fragment like `auth/`, `migrations/`, `api/`
      matches -> high, unconditionally. A change to authentication code
      warrants deep verification regardless of what the graph measures.
    - **Blast radius**: any symbol defined in this file has 3+ callers
      across the codebase -> at least medium.
    - **Fan-in**: 2+ other files import this one -> at least medium.
    - **Untested public symbols**: any public symbol in this file has no
      test edge (`GraphQuery.untested`, which over-reports by construction
      -- see its own docstring) -> at least medium, never downgraded to
      low on this signal alone since the over-reporting cuts only one way
      (toward flagging more, not fewer, files).
    - Nothing matched -> low.
    """
    reasons: list[str] = []
    tier = "low"

    def _raise(new_tier: str, reason: str) -> None:
        nonlocal tier
        reasons.append(reason)
        if _TIER_ORDER[new_tier] > _TIER_ORDER[tier]:
            tier = new_tier

    marker = _path_signal(path)
    if marker is not None:
        _raise("high", f"path contains {marker!r}, a high-risk convention")

    nodes = query.store.nodes_in_file(path)
    if not nodes:
        if not reasons:
            reasons.append("no graph node found for this file (not ingested, or not Python)")
        return tier, reasons

    max_callers = max((len(query.callers(node.id)) for node in nodes), default=0)
    if max_callers >= 3:
        _raise("medium", f"at least one symbol here has {max_callers} callers (blast radius)")

    fan_in = len(query.file_dependencies(path)["imported_by"])
    if fan_in >= 2:
        _raise("medium", f"imported by {fan_in} other files (fan-in)")

    untested_in_file = {n.id for n in query.untested()} & {n.id for n in nodes}
    if untested_in_file:
        _raise(
            "medium",
            f"{len(untested_in_file)} public symbol(s) here have no direct test edge "
            "(over-reports by construction -- see GraphQuery.untested_caveat())",
        )

    if not reasons:
        reasons.append("no elevating signal found")

    return tier, reasons


def rules_for_tier(tier: str, rules: list[Rule]) -> list[Rule]:
    """Every rule whose `risk_tier` is at or below `tier` -- the filter-
    then-fire shape `get_applicable_rules` already demonstrates for
    language, applied to a second axis. A "high" tier gets every rule;
    a "low" tier gets only rules that are worth running on a trivial
    change.
    """
    ceiling = _TIER_ORDER.get(tier, 0)
    return [r for r in rules if _TIER_ORDER.get(r.risk_tier, 0) <= ceiling]


def classify_paths(paths: list[str], query: GraphQuery) -> dict[str, tuple[str, list[str]]]:
    """Tier every path in `paths` -- the entry point a CLI command uses to
    turn a list of changed files into a per-file tier."""
    return {path: classify_file_risk(path, query) for path in paths}
