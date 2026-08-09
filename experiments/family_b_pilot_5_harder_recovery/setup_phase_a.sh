#!/usr/bin/env bash
# Sets up pilot 5's 10 trial directories from fixture_repo/.
#
# naive_N/: a plain copy of the fixture -- no .verity/ state, as if a
# reset wiped everything.
#
# verity_N/: the same copy, plus a .verity/ directory pre-loaded (via the
# real `verity task` / `verity remember` CLI) with a fabricated prior
# investigation naming the exact root cause -- same pattern as pilot 4
# (experiments/family_b_pilot_4_recovery_after_reset/), on a fixture
# designed to be genuinely harder to find cold: two structurally identical
# subsystems (tax, healthy; late_fee, broken), and two similarly-named
# config dicts within the broken one (ACTIVE_POLICY vs DEPRECATED_POLICY).
set -euo pipefail
cd "$(dirname "$0")"

rm -rf trials
mkdir -p trials

for i in 1 2 3 4 5; do
  cp -r fixture_repo "trials/naive_$i"

  cp -r fixture_repo "trials/verity_$i"
  (
    cd "trials/verity_$i"
    verity init >/dev/null
    verity task "Fix failing test test_invoice_applies_current_late_fee_policy in tests/test_billing.py" \
      --next "Change apply_late_fee() in billing/late_fee.py to read policy.ACTIVE_POLICY instead of policy.DEPRECATED_POLICY" \
      --file "billing/late_fee.py"
    verity remember decision 'Root cause: apply_late_fee() in billing/late_fee.py reads policy.DEPRECATED_POLICY (a superseded collections policy with a 30-day grace period) instead of policy.ACTIVE_POLICY (the current one, 10-14 day grace depending on region). Traced via calculate_invoice() -> apply_late_fee(). The tax subsystem (billing/tax.py, billing/tax_rates.py) was checked and is unrelated -- it already reads the correct REGION_RATES table.'
    verity remember discovery 'billing/policy.py defines both ACTIVE_POLICY (current) and DEPRECATED_POLICY (superseded by the 2024 collections policy update) with the same shape, which is what makes apply_late_fee() reading the wrong one easy to miss. billing/tax_rates.py has an analogous pair (REGION_RATES vs LEGACY_REGION_RATES) but billing/tax.py already reads the correct one -- that subsystem is healthy, not the bug.'
  )
  echo "prepared: naive_$i, verity_$i"
done
