#!/usr/bin/env bash
# Pilot 4's trial setup -- the first of the three recovery-after-reset pilots.
# See ../lib/setup_phase_a.sh for what the two conditions are and why the
# phase-A investigation is fabricated rather than agent-produced.
#
# This fixture's bug is two function calls away from the failing test:
# get_bulk_discount_rate() gates the discount on a legacy flat threshold
# instead of the per-tier one, and pytest's failure message names neither
# file. Pilots 5 and 6 raise the difficulty further.
set -euo pipefail
cd "$(dirname "$0")"
source ../lib/setup_phase_a.sh

PHASE_A_TASK="Fix failing test test_bulk_discount_uses_correct_threshold in tests/test_pricing.py"
PHASE_A_NEXT="Change get_bulk_discount_rate() in pricing/discount.py to compare quantity against config.THRESHOLDS[tier]['quantity'] instead of config.DEFAULT_THRESHOLD"
PHASE_A_FILE="pricing/discount.py"
PHASE_A_DECISION='Root cause: get_bulk_discount_rate() in pricing/discount.py gates the discount on config.DEFAULT_THRESHOLD (a legacy fallback of 100 units), instead of the per-tier threshold in config.THRESHOLDS[tier]["quantity"]. Traced via calculate_total() -> apply_discounts() -> get_bulk_discount_rate().'
PHASE_A_DISCOVERY='config.THRESHOLDS already has correct per-tier quantity/rate pairs (silver/gold/platinum). config.DEFAULT_THRESHOLD is a stale fallback from an older, non-tiered pricing scheme and should not gate tiered bulk discounts.'

setup_phase_a
