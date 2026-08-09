#!/usr/bin/env bash
# Sets up pilot 4's 10 trial directories from fixture_repo/.
#
# naive_N/: a plain copy of the fixture -- no .verity/ state at all, as if
# a reset wiped everything.
#
# verity_N/: the same copy, plus a .verity/ directory pre-loaded (via the
# real `verity task` / `verity remember` CLI, not hand-written JSON) with
# the investigation a prior agent supposedly already did: the task, the
# root-cause decision, and the supporting discovery. This is deliberately
# fabricated by the harness rather than produced by a live agent, so the
# pilot measures whether recovering a handoff helps -- not whether some
# other agent investigated well.
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
    verity task "Fix failing test test_bulk_discount_uses_correct_threshold in tests/test_pricing.py" \
      --next "Change get_bulk_discount_rate() in pricing/discount.py to compare quantity against config.THRESHOLDS[tier]['quantity'] instead of config.DEFAULT_THRESHOLD" \
      --file "pricing/discount.py"
    verity remember decision 'Root cause: get_bulk_discount_rate() in pricing/discount.py gates the discount on config.DEFAULT_THRESHOLD (a legacy fallback of 100 units), instead of the per-tier threshold in config.THRESHOLDS[tier]["quantity"]. Traced via calculate_total() -> apply_discounts() -> get_bulk_discount_rate().'
    verity remember discovery 'config.THRESHOLDS already has correct per-tier quantity/rate pairs (silver/gold/platinum). config.DEFAULT_THRESHOLD is a stale fallback from an older, non-tiered pricing scheme and should not gate tiered bulk discounts.'
  )
  echo "prepared: naive_$i, verity_$i"
done
