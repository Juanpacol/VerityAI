"""Security checks: T6's deterministic pattern-matching, finally wired up.

The two fact extractors in `analysis/facts.py` have existed since Phase 1's
predecessor research but were never connected to anything — the KG rules they
targeted (`SQL Injection Prevention`, `No Check-Then-Act Race`) were prompt
guidance only. This module is the connection: extract facts from real code,
run them through the rescued `RuleEngine.check_for_violation`, report
violations as `Finding`s.

Two rules, both narrow by explicit design (see each extractor's own
docstring for exactly what it catches and doesn't): syntactic, single-file,
no real data-flow analysis. A hit means "worth a human look," a miss means
"not this exact shape" — never a soundness or completeness guarantee. That
framing came from real evidence: Z3's own documentation calls its string
theory "an incomplete heuristic solver," which is why this is a pattern
matcher and not a proof.

Findings are file-granular, not line-granular. The extractors analyze a
whole parsed module and return a flat set of fact strings; recovering which
specific call site triggered a fact would need the extractors themselves to
carry line numbers, which is future work, not something faked here to look
more precise than it is.
"""

from pathlib import Path

from verityai.analysis.facts import extract_race_condition_facts, extract_sql_injection_facts
from verityai.core.models import Finding, ReliabilityReport, Rule, VerificationStatus
from verityai.graph.ingest import find_nested_projects, walk_repo
from verityai.reliability.rule_engine import RuleEngine

BUILTIN_SECURITY_RULES: list[Rule] = [
    Rule(
        id="sql-injection",
        name="SQL Injection Prevention",
        category="security",
        severity="high",
        risk_tier="high",
        formal_spec="PRE: sql_query_built_dynamically; POST: uses_parameterized_query",
        description=(
            "A query string built by concatenation, f-string, %-format or .format() "
            "was passed to execute()/executemany()/executescript() with no accompanying "
            "parameterized (safe) query found in the same function."
        ),
    ),
    Rule(
        id="check-then-act-race",
        name="No Check-Then-Act Race",
        category="security",
        severity="medium",
        risk_tier="medium",
        formal_spec="PRE: check_then_act_on_shared_resource; POST: check_and_act_combined_atomically",
        description=(
            "A containment check followed by a mutation of the same container, with "
            "neither guarded by a lock -- the textbook check-then-act race shape."
        ),
    ),
]


# Per-rule caveats about false-positive shape, surfaced whenever a finding
# for that rule appears. Found necessary by dogfooding: scanning this
# repository flagged `GraphQuery.context_for` for check-then-act-race. Reading
# it shows an ordinary local dict being built up (`if seed_id in found: ...
# else: found[seed_id] = ...`) with no concurrent access anywhere near it --
# the syntactic shape the extractor looks for, with none of the concurrency
# that would make it a real race. `analysis/facts.py`'s own docstring says
# this plainly ("treat a hit as worth a human look, never as proof"), but that
# caveat lived only in a docstring nobody reading a scan's output would see.
RULE_CAVEATS: dict[str, str] = {
    "sql-injection": (
        "This rule matches a syntactic shape (a dynamically built query string "
        "reaching execute()/executemany()/executescript() with no parameterized "
        "query alongside it in the same function) -- it has no data-flow analysis, "
        "so it cannot tell whether the dynamic portion actually originates from "
        "untrusted input or is built entirely from hardcoded, trusted values. A "
        "hit on a query built from constants is a false positive by this "
        "definition, not evidence of an injectable query."
    ),
    "check-then-act-race": (
        "This rule matches a syntactic shape (check membership, then mutate the "
        "same container, unguarded) -- it cannot tell whether the container is "
        "actually shared across threads/processes. A hit on an ordinary local "
        "dict being built up is expected and is not itself a bug."
    ),
}


def caveats_for(findings: list[Finding]) -> list[str]:
    """Caveats for every rule that produced at least one finding."""
    seen_rules = {finding.rule_id for finding in findings}
    return [RULE_CAVEATS[rule_id] for rule_id in seen_rules if rule_id in RULE_CAVEATS]


def extract_all_facts(code: str) -> set[str]:
    """Every fact this module's rules can consume, from one module's source."""
    return extract_sql_injection_facts(code) | extract_race_condition_facts(code)


def scan_code(code: str, path: str = "", rules: list[Rule] | None = None) -> list[Finding]:
    """Check one module's source against the built-in security rules.

    `rules` is injectable so a caller can run a subset or add project-specific
    rules without this module knowing about them — the same pattern
    `TokenCounter` and `ContextRanker` use for their own injected dependencies.
    """
    rules = rules if rules is not None else BUILTIN_SECURITY_RULES
    facts = {fact: True for fact in extract_all_facts(code)}

    engine = RuleEngine()
    engine.add_rules_batch(rules)

    findings = []
    for rule in rules:
        status, message = engine.check_for_violation(rule, facts)
        if status is VerificationStatus.FAIL:
            findings.append(
                Finding(
                    rule_id=rule.id,
                    rule_name=rule.name,
                    status=status,
                    severity=rule.severity,
                    message=message or "",
                    path=path,
                )
            )
    return findings


def scan_file(path: Path, rel_path: str = "", rules: list[Rule] | None = None) -> list[Finding]:
    """Scan one file on disk. Unreadable or unparseable files yield no findings.

    Silent on a read/syntax error rather than raising: a repo-wide scan must
    not stop because one file is a fragment or has non-UTF-8 bytes in it, and
    `extract_all_facts`'s own extractors already return an empty set on a
    `SyntaxError` for the same reason.
    """
    try:
        code = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return scan_code(code, path=rel_path or str(path), rules=rules)


def scan_repo(root: Path, rules: list[Rule] | None = None) -> ReliabilityReport:
    """Scan every Python file in a repository.

    Reuses `graph.ingest`'s walk and vendored-project exclusion, so a security
    scan respects the exact same scope as the code graph — a vendored
    dependency's vulnerabilities are not this project's to fix, and flagging
    them would be noise indistinguishable from a real finding.
    """
    root = Path(root).resolve()
    all_files = walk_repo(root)
    nested = find_nested_projects(root, all_files)

    findings: list[Finding] = []
    scanned = 0

    for path in all_files:
        if path.suffix != ".py":
            continue
        if any(parent in nested for parent in path.parents):
            continue

        scanned += 1
        rel = str(path.relative_to(root))
        findings.extend(scan_file(path, rel_path=rel, rules=rules))

    return ReliabilityReport(findings=findings, files_scanned=scanned)
