"""Tests for risk-adaptive verification (`reliability/risk.py`).

Risk tiering gates which rules run against changed files — auth/migrations/api
changes deserve deeper scrutiny than trivial edits. This module classifies files
using graph signals (blast radius, fan-in, untested symbols, path conventions)
that are already available in the code graph, with no new AST facts needed.
"""

from unittest.mock import MagicMock

from verityai.core.models import Rule
from verityai.reliability.risk import classify_file_risk, classify_paths, rules_for_tier


class TestPathSignal:
    """Path convention heuristics trigger high risk unconditionally."""

    def test_auth_in_path_is_high_risk(self):
        tier, reasons = classify_file_risk("src/auth/token_handler.py", MagicMock(store=MagicMock(nodes_in_file=lambda p: [])))

        assert tier == "high"
        assert any("auth" in r for r in reasons)

    def test_migrations_in_path_is_high_risk(self):
        tier, reasons = classify_file_risk("migrations/0021_add_payment_fields.py", MagicMock(store=MagicMock(nodes_in_file=lambda p: [])))

        assert tier == "high"
        assert any("migrations" in r for r in reasons)

    def test_api_in_path_is_high_risk(self):
        tier, reasons = classify_file_risk("src/api/routes.py", MagicMock(store=MagicMock(nodes_in_file=lambda p: [])))

        assert tier == "high"

    def test_security_in_path_is_high_risk(self):
        tier, reasons = classify_file_risk("src/security/crypto.py", MagicMock(store=MagicMock(nodes_in_file=lambda p: [])))

        assert tier == "high"

    def test_payment_in_path_is_high_risk(self):
        tier, reasons = classify_file_risk("src/payment/processor.py", MagicMock(store=MagicMock(nodes_in_file=lambda p: [])))

        assert tier == "high"

    def test_billing_in_path_is_high_risk(self):
        tier, reasons = classify_file_risk("src/billing/invoice.py", MagicMock(store=MagicMock(nodes_in_file=lambda p: [])))

        assert tier == "high"

    def test_ordinary_path_is_low_risk_by_default(self):
        mock_query = MagicMock()
        mock_query.store.nodes_in_file.return_value = []

        tier, reasons = classify_file_risk("src/utils/helpers.py", mock_query)

        assert tier == "low"
        assert "no graph node" in reasons[0]


class TestBlastRadius:
    """Symbol blast radius (3+ callers) elevates to medium risk."""

    def test_high_caller_count_is_medium_risk(self):
        mock_node = MagicMock(id="Node:func_1")
        mock_caller_1, mock_caller_2, mock_caller_3 = MagicMock(), MagicMock(), MagicMock()

        mock_query = MagicMock()
        mock_query.store.nodes_in_file.return_value = [mock_node]
        mock_query.callers.return_value = [mock_caller_1, mock_caller_2, mock_caller_3]

        tier, reasons = classify_file_risk("src/core/utils.py", mock_query)

        assert tier == "medium"
        assert any("3 callers" in r or "blast radius" in r for r in reasons)

    def test_two_callers_do_not_trigger_blast_radius(self):
        mock_node = MagicMock(id="Node:func_1")

        mock_query = MagicMock()
        mock_query.store.nodes_in_file.return_value = [mock_node]
        mock_query.callers.return_value = [MagicMock(), MagicMock()]  # exactly 2
        mock_query.file_dependencies.return_value = {"imported_by": []}
        mock_query.untested.return_value = []

        tier, reasons = classify_file_risk("src/utils.py", mock_query)

        assert tier == "low"
        assert not any("callers" in r for r in reasons)


class TestFanIn:
    """File fan-in (2+ importers) elevates to medium risk."""

    def test_two_importers_is_medium_risk(self):
        mock_node = MagicMock(id="Node:module_a")

        mock_query = MagicMock()
        mock_query.store.nodes_in_file.return_value = [mock_node]
        mock_query.callers.return_value = []
        mock_query.file_dependencies.return_value = {"imported_by": ["src/b.py", "src/c.py"]}
        mock_query.untested.return_value = []

        tier, reasons = classify_file_risk("src/core/shared.py", mock_query)

        assert tier == "medium"
        assert any("imported by 2" in r for r in reasons)

    def test_one_importer_does_not_trigger_fan_in(self):
        mock_node = MagicMock(id="Node:module_a")

        mock_query = MagicMock()
        mock_query.store.nodes_in_file.return_value = [mock_node]
        mock_query.callers.return_value = []
        mock_query.file_dependencies.return_value = {"imported_by": ["src/b.py"]}  # exactly 1
        mock_query.untested.return_value = []

        tier, reasons = classify_file_risk("src/utils.py", mock_query)

        assert tier == "low"
        assert not any("imported by" in r for r in reasons)


class TestUntestedPublicSymbols:
    """Untested public symbols elevate to medium risk."""

    def test_untested_symbols_in_file_is_medium_risk(self):
        mock_node_1 = MagicMock(id="Node:public_func")
        mock_node_2 = MagicMock(id="Node:other_func")

        mock_query = MagicMock()
        mock_query.store.nodes_in_file.return_value = [mock_node_1, mock_node_2]
        mock_query.callers.return_value = []
        mock_query.file_dependencies.return_value = {"imported_by": []}
        # Only Node:public_func is untested
        mock_query.untested.return_value = [mock_node_1]

        tier, reasons = classify_file_risk("src/core.py", mock_query)

        assert tier == "medium"
        assert any("1 public symbol" in r for r in reasons)

    def test_no_untested_symbols_stays_low_if_no_other_signals(self):
        mock_node = MagicMock(id="Node:tested_func")

        mock_query = MagicMock()
        mock_query.store.nodes_in_file.return_value = [mock_node]
        mock_query.callers.return_value = []
        mock_query.file_dependencies.return_value = {"imported_by": []}
        mock_query.untested.return_value = []

        tier, reasons = classify_file_risk("src/tested_module.py", mock_query)

        assert tier == "low"


class TestTierOrder:
    """Higher signals override lower ones; all reasons are preserved."""

    def test_blast_radius_and_fan_in_both_report(self):
        mock_node = MagicMock(id="Node:critical")

        mock_query = MagicMock()
        mock_query.store.nodes_in_file.return_value = [mock_node]
        mock_query.callers.return_value = [MagicMock() for _ in range(5)]  # blast radius
        mock_query.file_dependencies.return_value = {"imported_by": ["a", "b", "c"]}  # fan-in
        mock_query.untested.return_value = []

        tier, reasons = classify_file_risk("src/core.py", mock_query)

        assert tier == "medium"
        assert len(reasons) >= 2
        assert any("caller" in r for r in reasons)
        assert any("imported by" in r for r in reasons)

    def test_path_signal_beats_graph_signals(self):
        mock_node = MagicMock(id="Node:func")

        mock_query = MagicMock()
        mock_query.store.nodes_in_file.return_value = [mock_node]
        mock_query.callers.return_value = []
        mock_query.file_dependencies.return_value = {"imported_by": []}
        mock_query.untested.return_value = []

        tier, reasons = classify_file_risk("src/auth/handler.py", mock_query)

        assert tier == "high"  # path signal wins
        assert any("auth" in r for r in reasons)


class TestRulesForTier:
    """Filtering rules by tier ceiling."""

    def test_low_tier_only_gets_low_risk_rules(self):
        rules = [
            Rule(id="rule-1", name="R1", formal_spec="...", risk_tier="low"),
            Rule(id="rule-2", name="R2", formal_spec="...", risk_tier="medium"),
            Rule(id="rule-3", name="R3", formal_spec="...", risk_tier="high"),
        ]

        filtered = rules_for_tier("low", rules)

        assert len(filtered) == 1
        assert filtered[0].id == "rule-1"

    def test_medium_tier_gets_low_and_medium_rules(self):
        rules = [
            Rule(id="rule-1", name="R1", formal_spec="...", risk_tier="low"),
            Rule(id="rule-2", name="R2", formal_spec="...", risk_tier="medium"),
            Rule(id="rule-3", name="R3", formal_spec="...", risk_tier="high"),
        ]

        filtered = rules_for_tier("medium", rules)

        assert len(filtered) == 2
        assert {r.id for r in filtered} == {"rule-1", "rule-2"}

    def test_high_tier_gets_all_rules(self):
        rules = [
            Rule(id="rule-1", name="R1", formal_spec="...", risk_tier="low"),
            Rule(id="rule-2", name="R2", formal_spec="...", risk_tier="medium"),
            Rule(id="rule-3", name="R3", formal_spec="...", risk_tier="high"),
        ]

        filtered = rules_for_tier("high", rules)

        assert len(filtered) == 3

    def test_default_tier_for_rule_is_low(self):
        rules = [
            Rule(id="rule-1", name="R1", formal_spec="..."),  # no risk_tier specified
            Rule(id="rule-2", name="R2", formal_spec="...", risk_tier="medium"),
        ]

        filtered = rules_for_tier("low", rules)

        assert len(filtered) == 1
        assert filtered[0].id == "rule-1"

    def test_unknown_tier_defaults_to_low_ceiling(self):
        rules = [
            Rule(id="rule-1", name="R1", formal_spec="...", risk_tier="low"),
            Rule(id="rule-2", name="R2", formal_spec="...", risk_tier="medium"),
        ]

        filtered = rules_for_tier("unknown-tier", rules)

        assert len(filtered) == 1
        assert filtered[0].id == "rule-1"


class TestClassifyPaths:
    """Batch tiering over multiple paths."""

    def test_classify_paths_returns_tier_and_reasons_for_each_path(self):
        mock_query = MagicMock()
        mock_query.store.nodes_in_file.return_value = []

        paths = ["src/auth/handler.py", "src/utils.py", "src/api/endpoint.py"]
        result = classify_paths(paths, mock_query)

        assert set(result.keys()) == set(paths)
        assert result["src/auth/handler.py"][0] == "high"
        assert result["src/api/endpoint.py"][0] == "high"
        assert result["src/utils.py"][0] == "low"
        assert all(isinstance(reasons, list) for _, reasons in result.values())
