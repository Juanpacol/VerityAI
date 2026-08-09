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
    check_decision_resurfacing,
    check_file_exists,
    check_symbol_exists,
    check_symbol_relation,
    render_report,
    run_consistency_check,
)
from verityai.core.models import CheckStatus, Claim, ClaimKind, Decision, DecisionStatus
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


def relation_claim(subject, relation, target):
    return Claim(
        kind=ClaimKind.SYMBOL_RELATION,
        subject=subject,
        relation=relation,
        target=target,
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
