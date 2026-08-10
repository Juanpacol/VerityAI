#!/usr/bin/env bash
# Pilot 5's trial setup. Structure is shared with pilots 4 and 6 --
# see ../lib/setup_phase_a.sh for what the two conditions are and why the
# phase-A investigation is fabricated rather than agent-produced.
#
# This fixture is designed to be genuinely harder to diagnose cold than
# pilot 4's: two structurally identical subsystems (tax, healthy; late_fee,
# broken), and two similarly-named config dicts within the broken one
# (ACTIVE_POLICY vs DEPRECATED_POLICY). A cold agent has to rule out a
# plausible dead end, not just trace one chain.
set -euo pipefail
cd "$(dirname "$0")"
source ../lib/setup_phase_a.sh

PHASE_A_TASK="Fix failing test test_invoice_applies_current_late_fee_policy in tests/test_billing.py"
PHASE_A_NEXT="Change apply_late_fee() in billing/late_fee.py to read policy.ACTIVE_POLICY instead of policy.DEPRECATED_POLICY"
PHASE_A_FILE="billing/late_fee.py"
PHASE_A_DECISION='Root cause: apply_late_fee() in billing/late_fee.py reads policy.DEPRECATED_POLICY (a superseded collections policy with a 30-day grace period) instead of policy.ACTIVE_POLICY (the current one, 10-14 day grace depending on region). Traced via calculate_invoice() -> apply_late_fee(). The tax subsystem (billing/tax.py, billing/tax_rates.py) was checked and is unrelated -- it already reads the correct REGION_RATES table.'
PHASE_A_DISCOVERY='billing/policy.py defines both ACTIVE_POLICY (current) and DEPRECATED_POLICY (superseded by the 2024 collections policy update) with the same shape, which is what makes apply_late_fee() reading the wrong one easy to miss. billing/tax_rates.py has an analogous pair (REGION_RATES vs LEGACY_REGION_RATES) but billing/tax.py already reads the correct one -- that subsystem is healthy, not the bug.'

setup_phase_a
