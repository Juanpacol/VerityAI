"""Tests for checking claims against the graph and against memory.

The central case is hallucination detection: an agent asserting a symbol that
does not exist must be CONTRADICTED, confidently, and an agent asserting one
that does must be SUPPORTED. Everything else in this module exists to make
sure the checker never guesses when it cannot tell -- an ambiguous or
unresolved case must come back UNVERIFIABLE, never a coin flip dressed up as a
verdict, echoing the same discipline ADR-0006 applied to the graph itself.
"""

import pytest

from verityai.consistency.check import (
    check_constraint_violations,
    check_decision_resurfacing,
    check_file_exists,
    check_file_imports,
    check_symbol_exists,
    check_symbol_relation,
    render_report,
    run_consistency_check,
)
from verityai.core.models import (
    CheckStatus,
    Claim,
    ClaimKind,
    Constraint,
    Decision,
    DecisionStatus,
)
from verityai.graph.ingest import ingest_repo
from verityai.graph.query import GraphQuery
from verityai.graph.store import GraphStore


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "limits.py").write_text(
        "class Base:\n"
        "    pass\n\n\n"
        "class Service(Base):\n"
        "    def run(self, n):\n"
        "        return self.helper(n)\n\n"
        "    def helper(self, n):\n"
        "        return n\n"
    )
    return tmp_path


@pytest.fixture
def query(project):
    store = GraphStore()
    ingest_repo(project, store)
    yield GraphQuery(store)
    store.close()


def symbol_claim(name):
    return Claim(kind=ClaimKind.SYMBOL_EXISTS, subject=name, raw_text=f"`{name}`")


def relation_claim(subject, relation, target, negated=False):
    return Claim(
        kind=ClaimKind.SYMBOL_RELATION,
        subject=subject,
        relation=relation,
        target=target,
        negated=negated,
        raw_text=f"`{subject}` {relation} `{target}`",
    )


def file_claim(path):
    return Claim(kind=ClaimKind.FILE_EXISTS, subject=path, raw_text=f"`{path}`")


class TestSymbolExistence:
    def test_a_real_symbol_is_supported(self, query):
        result = check_symbol_exists(symbol_claim("Service"), query)

        assert result.status is CheckStatus.SUPPORTED
        assert result.confidence == 1.0

    def test_an_invented_symbol_is_contradicted(self, query):
        """The core hallucination-detection case."""
        result = check_symbol_exists(symbol_claim("TotallyMadeUpClass"), query)

        assert result.status is CheckStatus.CONTRADICTED
        assert result.confidence == 1.0
        assert "no definition" in result.explanation

    def test_supported_claims_carry_evidence(self, query):
        result = check_symbol_exists(symbol_claim("Service"), query)

        assert result.evidence
        assert "limits.py" in result.evidence[0].locator

    def test_a_qualified_method_name_resolves(self, query):
        result = check_symbol_exists(symbol_claim("run"), query)

        assert result.status is CheckStatus.SUPPORTED

    def test_a_bare_snake_case_miss_gets_a_caveat_not_a_softer_verdict(self, query):
        """Regression (ADR-0018): `with_tax` backtick-quoted for emphasis in
        ordinary prose is lexically identical to an invented function name.
        The verdict must stay CONTRADICTED at full confidence -- softening
        it would have silently cost the 14/14 recall the real pilot
        measured -- but the explanation should say the evidence is weaker
        here than for a clearly code-shaped miss."""
        result = check_symbol_exists(symbol_claim("with_tax"), query)

        assert result.status is CheckStatus.CONTRADICTED
        assert result.confidence == 1.0
        assert "local variable" in result.explanation

    def test_a_clearly_code_shaped_miss_gets_no_caveat(self, query):
        """`TotallyMadeUpClass` (CamelCase) isn't the shape a bare local
        variable name would take, so the caveat would be misleading noise
        here -- the evidence really is as strong as the confidence claims."""
        result = check_symbol_exists(symbol_claim("TotallyMadeUpClass"), query)

        assert result.status is CheckStatus.CONTRADICTED
        assert "local variable" not in result.explanation


class TestSymbolRelation:
    def test_a_real_relation_is_supported(self, query):
        result = check_symbol_relation(relation_claim("run", "calls", "helper"), query)

        assert result.status is CheckStatus.SUPPORTED

    def test_a_real_inheritance_is_supported(self, query):
        result = check_symbol_relation(relation_claim("Service", "inherits", "Base"), query)

        assert result.status is CheckStatus.SUPPORTED

    def test_missing_subject_is_contradicted(self, query):
        result = check_symbol_relation(relation_claim("Nonexistent", "calls", "helper"), query)

        assert result.status is CheckStatus.CONTRADICTED
        assert "Nonexistent" in result.explanation

    def test_missing_target_is_contradicted(self, query):
        result = check_symbol_relation(relation_claim("run", "calls", "Nonexistent"), query)

        assert result.status is CheckStatus.CONTRADICTED

    def test_both_symbols_real_but_unrelated_is_contradicted(self, query):
        """Both exist, but no edge connects them -- a real, specific lie."""
        result = check_symbol_relation(relation_claim("Base", "calls", "helper"), query)

        assert result.status is CheckStatus.CONTRADICTED
        assert "no calls edge" in result.explanation.lower() or "no calls" in result.explanation

    def test_an_unmapped_relation_is_unverifiable_not_guessed(self, query):
        claim = relation_claim("run", "depends_on", "helper")

        result = check_symbol_relation(claim, query)

        assert result.status is CheckStatus.UNVERIFIABLE
        assert result.confidence == 0.0

    def test_an_unresolved_ambiguous_call_is_unverifiable(self, tmp_path):
        """Per ADR-0006, an ambiguous call is deliberately left unresolved
        rather than guessed at -- and the consistency checker must honor
        that same caution rather than reporting a false contradiction."""
        (tmp_path / "a.py").write_text("def shared():\n    pass\n")
        (tmp_path / "b.py").write_text("def shared():\n    pass\n")
        (tmp_path / "c.py").write_text("def caller():\n    shared()\n")
        store = GraphStore()
        ingest_repo(tmp_path, store)
        query = GraphQuery(store)

        result = check_symbol_relation(relation_claim("caller", "calls", "shared"), query)

        assert result.status is CheckStatus.UNVERIFIABLE
        store.close()


class TestSymbolCallsFileRelation:
    """ADR-0018 found that a relation claim targeting a FILE, not a function,
    silently decomposed into two independent, both-true existence checks and
    vanished. These reproduce the exact real-world case: a function whose
    file does not import the file it's claimed to "call into"."""

    @pytest.fixture
    def two_file_project(self, tmp_path):
        (tmp_path / "rates.py").write_text("REGION_RATES = {}\n")
        (tmp_path / "policy.py").write_text("ACTIVE_POLICY = {}\n")
        (tmp_path / "tax.py").write_text(
            "from rates import REGION_RATES\n\n\ndef apply_tax(subtotal, region):\n    return subtotal\n"
        )
        return tmp_path

    @pytest.fixture
    def two_file_query(self, two_file_project):
        store = GraphStore()
        ingest_repo(two_file_project, store)
        yield GraphQuery(store)
        store.close()

    def test_an_import_with_no_call_evidence_is_unverifiable(self, two_file_query):
        """ADR-0021: an IMPORTS edge is necessary but not sufficient evidence
        for a "calls" claim. `apply_tax` here only reads `REGION_RATES`, a
        module-level dict -- it never calls anything in rates.py. Reporting
        SUPPORTED from the import alone (the pre-ADR-0021 behavior)
        reintroduced exactly the false-affirmation ADR-0018 was meant to
        close, just inverted: a silent miss became a confident wrong answer.
        """
        result = check_symbol_relation(
            relation_claim("apply_tax", "calls", "rates.py"), two_file_query
        )

        assert result.status is CheckStatus.UNVERIFIABLE
        assert "does not confirm a call" in result.explanation

    def test_a_file_never_imported_is_contradicted(self, two_file_query):
        """apply_tax's file (tax.py) never imports policy.py -- the exact
        shape of hallucination ADR-0018 found real agents producing."""
        result = check_symbol_relation(
            relation_claim("apply_tax", "calls", "policy.py"), two_file_query
        )

        assert result.status is CheckStatus.CONTRADICTED
        assert "does not import" in result.explanation

    def test_a_nonexistent_target_file_is_contradicted(self, two_file_query):
        result = check_symbol_relation(
            relation_claim("apply_tax", "calls", "nonexistent.py"), two_file_query
        )

        assert result.status is CheckStatus.CONTRADICTED
        assert "no file at" in result.explanation.lower()

    def test_missing_subject_is_still_contradicted(self, two_file_query):
        result = check_symbol_relation(
            relation_claim("nonexistent_fn", "calls", "rates.py"), two_file_query
        )

        assert result.status is CheckStatus.CONTRADICTED
        assert "nonexistent_fn" in result.explanation


class TestFileImportsRelation:
    """Phase 3: 'imports' is checkable directly and confidently -- unlike
    'calls' on a file target (ADR-0021), an IMPORTS edge IS the claim here,
    not a proxy standing in for a different one."""

    @pytest.fixture
    def two_file_project(self, tmp_path):
        (tmp_path / "rates.py").write_text("REGION_RATES = {}\n")
        (tmp_path / "tax.py").write_text("from rates import REGION_RATES\n")
        return tmp_path

    @pytest.fixture
    def two_file_query(self, two_file_project):
        store = GraphStore()
        ingest_repo(two_file_project, store)
        yield GraphQuery(store)
        store.close()

    def test_a_real_import_is_confidently_supported(self, two_file_query):
        result = check_file_imports(relation_claim("tax.py", "imports", "rates.py"), two_file_query)

        assert result.status is CheckStatus.SUPPORTED
        assert result.confidence == 1.0

    def test_a_missing_import_is_contradicted(self, two_file_query):
        result = check_file_imports(
            relation_claim("rates.py", "imports", "tax.py"), two_file_query
        )

        assert result.status is CheckStatus.CONTRADICTED

    def test_a_nonexistent_target_file_is_contradicted(self, two_file_query):
        result = check_file_imports(
            relation_claim("tax.py", "imports", "nonexistent.py"), two_file_query
        )

        assert result.status is CheckStatus.CONTRADICTED
        assert "no file at" in result.explanation.lower()

    def test_a_non_file_subject_is_unverifiable_not_guessed(self, two_file_query):
        """'imports' is a file-level relation -- a symbol subject is a
        different, unclear claim this checker declines to guess at."""
        result = check_file_imports(
            relation_claim("apply_tax", "imports", "rates.py"), two_file_query
        )

        assert result.status is CheckStatus.UNVERIFIABLE

    def test_dispatches_through_check_symbol_relation(self, two_file_query):
        result = check_symbol_relation(
            relation_claim("tax.py", "imports", "rates.py"), two_file_query
        )

        assert result.status is CheckStatus.SUPPORTED


class TestNegatedRelation:
    """Phase 3: "`X` does not call `Y`" must invert the affirmative verdict,
    not be silently dropped or silently matched as if unnegated."""

    def test_a_negated_true_relation_is_contradicted(self, query):
        """`run` really does call `helper` -- claiming it does NOT is false."""
        result = check_symbol_relation(
            relation_claim("run", "calls", "helper", negated=True), query
        )

        assert result.status is CheckStatus.CONTRADICTED

    def test_a_negated_false_relation_is_supported(self, query):
        """`Base` really does not call `helper` -- the negation holds."""
        result = check_symbol_relation(
            relation_claim("Base", "calls", "helper", negated=True), query
        )

        assert result.status is CheckStatus.SUPPORTED

    def test_negation_extracts_with_the_right_polarity(self):
        from verityai.consistency.claims import extract_claims

        claims = extract_claims("`run` does not call `helper`.")

        assert len(claims) == 1
        assert claims[0].negated is True
        assert claims[0].relation == "calls"

    def test_affirmative_text_is_never_extracted_as_negated(self):
        from verityai.consistency.claims import extract_claims

        claims = extract_claims("`run` calls `helper`.")

        assert len(claims) == 1
        assert claims[0].negated is False


class TestMultiTargetRelation:
    """Phase 3: "`X` calls `Y` and `Z`" must produce a claim against both
    targets, not silently bind only the first."""

    def test_extracts_a_claim_per_target(self):
        from verityai.consistency.claims import extract_claims

        claims = extract_claims("`run` calls `helper` and `other`.")

        assert len(claims) == 2
        assert {c.target for c in claims} == {"helper", "other"}
        assert all(c.subject == "run" and c.relation == "calls" for c in claims)

    def test_each_target_is_checked_independently(self, query):
        real = check_symbol_relation(relation_claim("run", "calls", "helper"), query)
        fake = check_symbol_relation(relation_claim("run", "calls", "Nonexistent"), query)

        assert real.status is CheckStatus.SUPPORTED
        assert fake.status is CheckStatus.CONTRADICTED


class TestConstraintViolations:
    """Phase 3: consistency/check.py previously read only
    store.decisions() -- constraints, recorded specifically to bind future
    work, were never consulted. Same heuristic shape and honesty discipline
    as check_decision_resurfacing (ADR-0018's normalization fix reused)."""

    @pytest.fixture
    def store_with_hard_constraint(self, store):
        store.append(
            Constraint(
                statement="must not call the legacy pricing module directly",
                hard=True,
            )
        )
        return store

    def test_resembling_text_is_flagged(self, store_with_hard_constraint):
        checks = check_constraint_violations(
            "I'll call the legacy pricing module directly here to save time.",
            store_with_hard_constraint,
        )

        assert checks
        assert checks[0].status is CheckStatus.CONTRADICTED
        assert "hard constraint" in checks[0].explanation

    def test_confidence_is_never_full_certainty(self, store_with_hard_constraint):
        checks = check_constraint_violations(
            "call the legacy pricing module directly", store_with_hard_constraint
        )

        assert all(c.confidence < 1.0 for c in checks)

    def test_unrelated_text_is_not_flagged(self, store_with_hard_constraint):
        checks = check_constraint_violations(
            "Let's improve the CLI help text formatting.", store_with_hard_constraint
        )

        assert checks == []

    def test_soft_constraints_are_never_checked(self, store):
        store.append(Constraint(statement="prefer stdlib over third-party packages", hard=False))

        checks = check_constraint_violations(
            "I'll pull in a third-party package here instead of stdlib.", store
        )

        assert checks == []

    def test_wired_into_run_consistency_check(self, store_with_hard_constraint):
        report = run_consistency_check(
            "I'll call the legacy pricing module directly here.",
            store=store_with_hard_constraint,
        )

        assert any(
            "hard constraint" in c.explanation for c in report.contradictions
        )


class TestFileExistence:
    def test_an_existing_file_is_supported(self, project):
        result = check_file_exists(file_claim("src/limits.py"), project)

        assert result.status is CheckStatus.SUPPORTED

    def test_a_missing_file_is_contradicted(self, project):
        result = check_file_exists(file_claim("src/does_not_exist.py"), project)

        assert result.status is CheckStatus.CONTRADICTED

    def test_no_repo_root_is_unverifiable(self):
        result = check_file_exists(file_claim("anything.py"), None)

        assert result.status is CheckStatus.UNVERIFIABLE

    def test_a_path_escaping_the_repo_is_contradicted_not_followed(self, project):
        """A claim must never cause a filesystem read outside the repo."""
        result = check_file_exists(file_claim("../../etc/passwd"), project)

        assert result.status is CheckStatus.CONTRADICTED
        assert "outside" in result.explanation


class TestDecisionResurfacing:
    @pytest.fixture
    def store_with_rejected(self, store):
        store.append(
            Decision(
                statement="use a global mutable cache for session state",
                status=DecisionStatus.REJECTED,
                rationale="caused race conditions under concurrent requests",
            )
        )
        store.append(Decision(statement="use per-request session objects"))
        return store

    def test_resembling_text_is_flagged(self, store_with_rejected):
        checks = check_decision_resurfacing(
            "I'll add a global mutable cache for session state to speed this up.",
            store_with_rejected,
        )

        assert checks
        assert checks[0].status is CheckStatus.CONTRADICTED
        assert "rejected" in checks[0].explanation

    def test_confidence_is_never_full_certainty(self, store_with_rejected):
        """A lexical-overlap heuristic must never read as certain as a graph
        lookup -- it can be wrong in both directions."""
        checks = check_decision_resurfacing(
            "a global mutable cache for session state", store_with_rejected
        )

        assert all(c.confidence < 1.0 for c in checks)

    def test_unrelated_text_is_not_flagged(self, store_with_rejected):
        checks = check_decision_resurfacing(
            "Let's improve the CLI help text formatting.", store_with_rejected
        )

        assert checks == []

    def test_a_single_rejected_decision_does_not_swallow_unrelated_text(self, store):
        """Regression: with only one or two decisions on record, normalizing
        against the corpus's own max score made the closest-of-the-available
        matches always land at 1.0, even sharing no real content with the
        checked text -- a genuinely unrelated proposal always "resembled"
        the one decision on file. Found via a real pilot
        (experiments/consistency_pilot_1_hallucination_detection/), not by
        this test suite -- the pre-existing unrelated-text fixture had zero
        token overlap and never exercised the near-zero-but-nonzero BM25
        regime where this bug actually lived. See docs/adr/0018."""
        store.append(
            Decision(
                statement=(
                    "Apply the late fee using DEPRECATED_POLICY directly for all "
                    "regions, skipping the per-tier ACTIVE_POLICY lookup, to keep "
                    "the calculation simpler."
                ),
                status=DecisionStatus.REJECTED,
                rationale="DEPRECATED_POLICY has stale grace periods.",
            )
        )

        checks = check_decision_resurfacing(
            "I'd like to add a caching layer for tax rate lookups, since rates "
            "rarely change within a business day, to cut down on repeated work.",
            store,
        )

        assert checks == []

    def test_active_decisions_are_never_flagged(self, store_with_rejected):
        """Resurfacing an ACTIVE decision is not a problem -- it is agreement."""
        checks = check_decision_resurfacing(
            "I'll use per-request session objects here.", store_with_rejected
        )

        assert all("per-request" not in c.explanation for c in checks)

    def test_no_inactive_decisions_means_nothing_to_flag(self, store):
        store.append(Decision(statement="use postgres"))

        assert check_decision_resurfacing("anything at all", store) == []

    def test_the_rationale_is_surfaced_when_present(self, store_with_rejected):
        checks = check_decision_resurfacing(
            "global mutable cache for session state", store_with_rejected
        )

        assert "race conditions" in checks[0].explanation


class TestResurfacingSurfacingLog:
    """Phase 2 (ADR-0023): a resurfacing warning is itself a "surfaced and
    ignored" signal -- the checked text resembles a decision explicitly
    rejected or superseded, which is the opposite of having used it."""

    @pytest.fixture
    def store_with_rejected(self, store):
        store.append(
            Decision(
                statement="use a global mutable cache for session state",
                status=DecisionStatus.REJECTED,
                rationale="caused race conditions under concurrent requests",
            )
        )
        return store

    def test_a_flagged_resurfacing_appends_a_surfacing_record(self, store_with_rejected):
        rejected = store_with_rejected.decisions(include_inactive=True)[0]

        check_decision_resurfacing(
            "I'll add a global mutable cache for session state to speed this up.",
            store_with_rejected,
        )

        surfacings = store_with_rejected.surfacings()
        assert len(surfacings) == 1
        assert surfacings[0].surfaced_via == "decision_resurfacing"
        assert surfacings[0].record_ids == [rejected.id]
        assert surfacings[0].used is False

    def test_unrelated_text_appends_no_surfacing_record(self, store_with_rejected):
        check_decision_resurfacing(
            "Let's improve the CLI help text formatting.", store_with_rejected
        )

        assert store_with_rejected.surfacings() == []


class TestFullPipeline:
    def test_extraction_and_checking_compose(self, query, project):
        # One relation claim (subsumes both backtick spans it matches) plus
        # one file claim -- see TestRelationExtraction for why a matched
        # relation is not also double-counted as two bare symbol claims.
        text = "`run` calls `helper` and lives in `src/limits.py`."

        report = run_consistency_check(text, query=query, repo_root=project)

        assert report.claims_extracted == 2
        assert report.is_clean

    def test_a_hallucinated_symbol_surfaces_as_a_contradiction(self, query, project):
        text = "The fix touches `TotallyInventedClass`."

        report = run_consistency_check(text, query=query, repo_root=project)

        assert not report.is_clean
        assert len(report.contradictions) == 1

    def test_missing_backends_degrade_rather_than_skip_silently(self):
        report = run_consistency_check("Uses `SomeClass.method` here.")

        assert report.degraded_reason
        assert "no code graph" in report.degraded_reason
        assert report.checks[0].status is CheckStatus.UNVERIFIABLE

    def test_every_extracted_claim_produces_exactly_one_check_group(self, query, project, store):
        """No claim should be silently dropped between extraction and report."""
        text = "`Service` calls `helper`. Also see `src/limits.py`."

        report = run_consistency_check(text, query=query, store=store, repo_root=project)

        assert len(report.checks) == report.claims_extracted

    def test_missing_memory_store_is_named_in_degraded_reason(self, query, project):
        report = run_consistency_check("`Service`", query=query, repo_root=project)

        assert "memory store" in report.degraded_reason


class TestRendering:
    def test_clean_report_states_the_zero_count(self, query, project):
        report = run_consistency_check("`run` calls `helper`.", query=query, repo_root=project)

        assert "0 contradiction" in render_report(report)

    def test_contradictions_are_marked_fail(self, query, project):
        report = run_consistency_check("`NotReal`", query=query, repo_root=project)

        assert "FAIL" in render_report(report)

    def test_no_claims_extracted_says_so(self):
        report = run_consistency_check("Just a plain sentence, nothing to check.")

        assert "No checkable claims" in render_report(report)

    def test_degraded_reason_is_shown(self):
        report = run_consistency_check("`Something`")

        assert "degraded" in render_report(report)
