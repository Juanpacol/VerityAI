#!/usr/bin/env bash
# Pilot 7's trial setup. Structure is shared with pilots 4-6 -- see
# ../lib/setup_phase_a.sh for what the two conditions are and why the
# phase-A investigation is fabricated rather than agent-produced.
#
# Unlike pilots 4-6, this fixture's fix is trivial to find (the whole
# function is a one-line no-op) -- the obvious bug is not the point. The
# point is a business-rule ambiguity the code cannot resolve on its own:
# whether the exact day a grace period ends still counts as within grace.
# Two equally reasonable fixes (days_overdue > grace_days vs
# days_overdue >= grace_days) both pass the one visible test identically,
# and only diverge on a boundary case never shown to the agent. The
# decision below states the real convention; nothing in the code does.
set -euo pipefail
cd "$(dirname "$0")"
source ../lib/setup_phase_a.sh

PHASE_A_TASK="Fix failing test test_invoice_applies_late_fee_when_overdue in tests/test_billing.py"
PHASE_A_NEXT="Implement apply_late_fee() in billing/late_fee.py to apply FEE_RATE once days_overdue is strictly greater than grace_days"
PHASE_A_FILE="billing/late_fee.py"
PHASE_A_DECISION='apply_late_fee() in billing/late_fee.py is currently a no-op (always returns subtotal unchanged) and needs the fee logic implemented. Business rule, confirmed with billing: the grace period is inclusive -- the day the grace period ends (days_overdue == grace_days) still counts as within grace, no fee. The fee only applies once days_overdue is STRICTLY GREATER than grace_days (use >, not >=). This is a policy choice, not something derivable from the existing code.'
PHASE_A_DISCOVERY='FEE_RATE (0.02) is already correct and defined at module level in billing/late_fee.py. The only change needed is the comparison inside apply_late_fee() -- there is no other logic to fix.'

setup_phase_a
