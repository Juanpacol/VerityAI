"""Tests for risk-adaptive verification (`reliability/risk.py`).

These run against a **real ingested graph**, not a mock. The first version of
this file was the only one in the suite to import `unittest.mock`, and that
choice hid a total failure: `MagicMock` returns whatever node you configure
regardless of the path string it is asked for, so no test could distinguish
`classify_file_risk("src/x.py", q)` from
`classify_file_risk("/abs/src/x.py", q)`. Against the real graph the second
form finds nothing and tiers every file `low` -- a checker reporting "nothing
needs deep verification" precisely when it has measured nothing at all
(ADR-0028, and T6's lesson in CLAUDE.md).

`TestPathForm` is the class that pins that down. The rest follow
`tests/unit/test_graph_query.py`'s convention: real files in `tmp_path`, real
`ingest_repo`, real `GraphQuery`.

The fixture separates signals that are naturally coupled -- three callers
normally implies fan-in three -- so each branch can be asserted alone:

| file                 | callers | fan-in | untested | isolates                     |
|----------------------|---------|--------|----------|------------------------------|
| `src/hub.py`         | 3       | 1      | 1        | blast radius, not fan-in     |
| `src/edges.py`       | 2       | 2      | 1        | fan-in, and the <3 boundary  |
| `src/lonely.py`      | 0       | 0      | 1        | untested alone               |
| `src/plain.py`       | 1       | 1      | 0        | the genuine `low` (TESTS edge)|
| `src/auth/tokens.py` | 0       | 0      | 1        | path marker over a real node |
"""

import pytest

from verityai.core.models import Rule
from verityai.graph.ingest import ingest_repo
from verityai.graph.query import GraphQuery
from verityai.graph.store import GraphStore
from verityai.reliability.risk import classify_file_risk, classify_paths, rules_for_tier


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "tests").mkdir()

    # 3 callers from one file: blast radius without fan-in.
    (tmp_path / "src" / "hub.py").write_text(
        '"""Widely used helper."""\n\n\ndef shared(x):\n    return x + 1\n'
    )
    (tmp_path / "src" / "many_callers.py").write_text(
        "from hub import shared\n\n\n"
        "def first(x):\n    return shared(x)\n\n\n"
        "def second(x):\n    return shared(x)\n\n\n"
        "def third(x):\n    return shared(x)\n"
    )

    # 2 importers, 2 callers: fan-in fires, blast radius must not.
    (tmp_path / "src" / "edges.py").write_text(
        '"""Imported by two files."""\n\n\ndef helper(x):\n    return x * 2\n'
    )
    (tmp_path / "src" / "pair_a.py").write_text(
        "from edges import helper\n\n\ndef use_a(x):\n    return helper(x)\n"
    )
    (tmp_path / "src" / "pair_b.py").write_text(
        "from edges import helper\n\n\ndef use_b(x):\n    return helper(x)\n"
    )

    (tmp_path / "src" / "lonely.py").write_text(
        '"""Nothing calls this, nothing tests it."""\n\n\n'
        "def never_called_public(x):\n    return x\n"
    )

    # The only genuinely low file: a real TESTS edge reaches it.
    (tmp_path / "src" / "plain.py").write_text(
        '"""Ordinary, tested, unremarkable."""\n\n\ndef tidy(x):\n    return x.strip()\n'
    )
    (tmp_path / "tests" / "test_plain.py").write_text(
        "from plain import tidy\n\n\ndef test_tidy_strips():\n    assert tidy(' a ') == 'a'\n"
    )

    (tmp_path / "src" / "auth" / "tokens.py").write_text(
        '"""Token minting."""\n\n\ndef mint():\n    return "token"\n'
    )
    return tmp_path


@pytest.fixture
def query(project):
    store = GraphStore()
    ingest_repo(project, store)
    yield GraphQuery(store)
    store.close()


def _reasons_mentioning(reasons: list[str], fragment: str) -> list[str]:
    return [r for r in reasons if fragment in r]


class TestPathConvention:
    @pytest.mark.parametrize(
        "path,marker",
        [
            ("src/auth/token_handler.py", "auth"),
            ("migrations/0021_add_payment_fields.py", "migrations"),
            ("src/api/routes.py", "api"),
            ("src/security/crypto.py", "security"),
            ("src/payment/processor.py", "payment"),
            ("src/billing/invoice.py", "billing"),
        ],
    )
    def test_a_high_risk_fragment_is_high_regardless_of_the_graph(self, path, marker, query):
        """None of these exist in the fixture, which is the point: a change to
        authentication code earns depth even when the graph knows nothing."""
        tier, reasons = classify_file_risk(path, query)

        assert tier == "high"
        assert _reasons_mentioning(reasons, marker)

    def test_a_marker_file_that_is_in_the_graph_reports_both(self, query):
        """`src/auth/tokens.py` is really ingested, so the marker reason and
        the graph's own signals must both appear -- and the "not ingested"
        note must not."""
        tier, reasons = classify_file_risk("src/auth/tokens.py", query)

        assert tier == "high"
        assert _reasons_mentioning(reasons, "auth")
        assert not _reasons_mentioning(reasons, "no graph node found")

    @pytest.mark.parametrize(
        "path", ["src/rapid/poller.py", "src/therapist/notes.py", "src/scrapility/x.py"]
    )
    def test_the_substring_false_positive_is_pinned_not_hidden(self, path, query):
        """`_HIGH_RISK_PATH_MARKERS` matches anywhere in the path, not on
        segment boundaries, so "api" fires inside r-*api*-d, ther-*api*-st and
        scr-*api*-lity. A real false positive, declared rather than quietly
        narrowed (ADR-0028) -- narrowing it is its own decision. This test
        exists so the behaviour cannot change silently in either direction."""
        tier, reasons = classify_file_risk(path, query)

        assert tier == "high"
        assert _reasons_mentioning(reasons, "'api'")

    def test_a_marker_file_outside_the_graph_still_says_it_was_not_consulted(self, query):
        """The reasons list must not imply signals were measured when the
        lookup never resolved. Before ADR-0028 this note was suppressed
        whenever any other reason had already fired."""
        tier, reasons = classify_file_risk("src/api/not_ingested.py", query)

        assert tier == "high"
        assert _reasons_mentioning(reasons, "api")
        assert _reasons_mentioning(reasons, "no graph node found")


class TestPathForm:
    """The regression the mocks hid (ADR-0028)."""

    def test_an_absolute_path_without_a_root_is_a_stated_non_result(self, project, query):
        absolute = str(project / "src" / "hub.py")

        tier, reasons = classify_file_risk(absolute, query)

        assert tier == "low"
        assert _reasons_mentioning(reasons, "absolute")
        assert _reasons_mentioning(reasons, "non-result")
        # Distinct from the merely-not-ingested case: a caller must be able to
        # tell "I passed the wrong form" from "this file has no signals".
        assert not _reasons_mentioning(reasons, "no graph node found")

    def test_an_absolute_path_with_a_root_tiers_the_same_as_the_relative_one(self, project, query):
        """The load-bearing assertion: with the root supplied, the two forms
        must be indistinguishable."""
        absolute = str(project / "src" / "hub.py")

        assert classify_file_risk(absolute, query, repo_root=project) == classify_file_risk(
            "src/hub.py", query
        )

    def test_a_dot_slash_prefix_is_normalized(self, query):
        """`./src/hub.py` is the same file, but an exact `path =` match
        rejects it -- and shell globs and `Path(".")` walks produce this form
        routinely."""
        assert classify_file_risk("./src/hub.py", query) == classify_file_risk("src/hub.py", query)

    def test_a_path_outside_the_repo_root_says_so(self, project, query):
        tier, reasons = classify_file_risk("/etc/hosts", query, repo_root=project)

        assert tier == "low"
        assert _reasons_mentioning(reasons, "outside repo_root")

    def test_a_relative_path_that_is_simply_absent_says_what_form_is_expected(self, query):
        tier, reasons = classify_file_risk("src/nope.py", query)

        assert tier == "low"
        assert _reasons_mentioning(reasons, "repo-relative")


class TestBlastRadius:
    def test_three_callers_is_medium(self, query):
        tier, reasons = classify_file_risk("src/hub.py", query)

        assert tier == "medium"
        assert _reasons_mentioning(reasons, "3 callers")

    def test_two_callers_do_not_trigger_blast_radius(self, query):
        """`src/edges.py` has exactly 2 callers and 2 importers, so it is
        medium *from fan-in*. The assertion is on the absence of the callers
        reason, not on the tier -- a mock could zero the other signals to make
        the tier itself prove the point, but a real graph cannot."""
        _, reasons = classify_file_risk("src/edges.py", query)

        assert not _reasons_mentioning(reasons, "callers")


class TestFanIn:
    def test_two_importers_is_medium(self, query):
        tier, reasons = classify_file_risk("src/edges.py", query)

        assert tier == "medium"
        assert _reasons_mentioning(reasons, "imported by 2 other files")

    def test_one_importer_does_not_trigger_fan_in(self, query):
        _, reasons = classify_file_risk("src/hub.py", query)

        assert not _reasons_mentioning(reasons, "imported by")


class TestUntestedPublicSymbols:
    def test_an_untested_public_symbol_is_medium_and_carries_the_caveat(self, query):
        tier, reasons = classify_file_risk("src/lonely.py", query)

        assert tier == "medium"
        assert _reasons_mentioning(reasons, "1 public symbol")
        # The over-reporting warning must ride along wherever the signal is
        # used -- `GraphQuery.untested`'s own docstring insists on this.
        assert _reasons_mentioning(reasons, "over-reports")

    def test_a_symbol_with_a_real_test_edge_is_not_flagged(self, query):
        """The genuine `low` case. If `untested()` stopped resolving the TESTS
        edge, every file in every repo would tier at least medium and the tier
        would stop distinguishing anything."""
        tier, reasons = classify_file_risk("src/plain.py", query)

        assert tier == "low"
        assert reasons == ["no elevating signal found"]

    def test_the_untested_set_is_what_the_graph_says_it_is(self, query):
        """Asserted separately so a failure above tells you whether the graph
        or the tiering broke."""
        untested = {node.name for node in query.untested()}

        assert "never_called_public" in untested
        assert "tidy" not in untested


class TestTierOrdering:
    def test_every_firing_signal_contributes_a_reason(self, query):
        tier, reasons = classify_file_risk("src/hub.py", query)

        assert tier == "medium"
        assert len(reasons) >= 2
        assert _reasons_mentioning(reasons, "callers")
        assert _reasons_mentioning(reasons, "public symbol")

    def test_a_path_marker_outranks_graph_signals(self, query):
        tier, _ = classify_file_risk("src/auth/tokens.py", query)

        assert tier == "high"

    def test_a_tier_never_appears_without_a_reason(self, query):
        """invariant 5's spirit, over every file in the fixture."""
        for path in ("src/hub.py", "src/edges.py", "src/plain.py", "src/auth/tokens.py"):
            _, reasons = classify_file_risk(path, query)
            assert reasons, f"{path} produced a bare tier with no reason"


def _rules() -> list[Rule]:
    return [
        Rule(id="cheap", name="Cheap", formal_spec="PRE: a; POST: b", risk_tier="low"),
        Rule(id="mid", name="Mid", formal_spec="PRE: a; POST: b", risk_tier="medium"),
        Rule(id="deep", name="Deep", formal_spec="PRE: a; POST: b", risk_tier="high"),
    ]


class TestRulesForTier:
    """Pure over `list[Rule]` -- no graph involved, and none faked."""

    def test_low_admits_only_low_tier_rules(self):
        assert [r.id for r in rules_for_tier("low", _rules())] == ["cheap"]

    def test_medium_admits_low_and_medium(self):
        assert {r.id for r in rules_for_tier("medium", _rules())} == {"cheap", "mid"}

    def test_high_admits_everything(self):
        assert len(rules_for_tier("high", _rules())) == 3

    def test_a_rule_defaults_to_low_tier(self):
        rules = [Rule(id="unset", name="Unset", formal_spec="PRE: a; POST: b")]

        assert rules_for_tier("low", rules) == rules

    def test_an_unknown_tier_falls_back_to_the_low_ceiling(self):
        assert [r.id for r in rules_for_tier("bogus", _rules())] == ["cheap"]

    def test_only_the_low_tier_rule_is_admitted_at_the_low_tier(self):
        """Pinned so a future rule addition/removal is caught here rather
        than silently changing what a low-tier scan actually covers. Until
        `shell-command-injection` (ADR-0032), this admitted nothing -- the
        reason ADR-0026 stops at reporting tiers instead of gating scans."""
        from verityai.reliability.security import BUILTIN_SECURITY_RULES

        assert {r.id for r in rules_for_tier("low", BUILTIN_SECURITY_RULES)} == {
            "shell-command-injection"
        }
        assert len(rules_for_tier("high", BUILTIN_SECURITY_RULES)) == len(BUILTIN_SECURITY_RULES)


class TestClassifyPaths:
    def test_every_path_is_tiered_and_keyed_as_given(self, query):
        paths = ["src/auth/tokens.py", "src/plain.py", "src/hub.py", "src/nope.py"]

        result = classify_paths(paths, query)

        assert set(result) == set(paths)
        assert result["src/auth/tokens.py"][0] == "high"
        assert result["src/hub.py"][0] == "medium"
        assert result["src/plain.py"][0] == "low"
        assert result["src/nope.py"][0] == "low"

    def test_the_repo_root_is_forwarded(self, project, query):
        absolute = str(project / "src" / "hub.py")

        result = classify_paths([absolute], query, repo_root=project)

        assert result[absolute][0] == "medium", "repo_root must reach classify_file_risk"
