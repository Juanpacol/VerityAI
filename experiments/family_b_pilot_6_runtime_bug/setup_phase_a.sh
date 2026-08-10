#!/usr/bin/env bash
# Pilot 6's trial setup. Structure is shared with pilots 4 and 5 --
# see ../lib/setup_phase_a.sh for what the two conditions are and why the
# phase-A investigation is fabricated rather than agent-produced.
#
# This fixture's bug is call-sequence-dependent, not a single wrong constant:
# get_price() in catalog/cache.py caches by `item` only, ignoring `tier`, so a
# second call for the same item under a different tier silently returns the
# first call's cached price. Reading get_price() in isolation doesn't reveal
# this -- it looks like reasonable memoization -- it only shows up by tracing
# the actual call sequence catalog/quote.py::build_quote() makes, or by running
# the failing test and reasoning backward from the wrong value.
set -euo pipefail
cd "$(dirname "$0")"
source ../lib/setup_phase_a.sh

PHASE_A_TASK="Fix failing test test_quote_compares_tiers_for_the_same_item in tests/test_quote.py"
PHASE_A_NEXT="Change get_price() in catalog/cache.py to key _price_cache by (item, tier) instead of item alone"
PHASE_A_FILE="catalog/cache.py"
PHASE_A_DECISION='Root cause: get_price() in catalog/cache.py memoizes _price_cache keyed only by item, ignoring tier. When build_quote() requests the same item under two different tiers in one call (e.g. standard then premium), the second call silently returns the first calls cached price instead of recomputing for its own tier. Found by tracing the actual sequence of get_price() calls build_quote() makes, not by reading get_price() in isolation -- it reads like ordinary memoization until you follow the call order.'
PHASE_A_DISCOVERY='catalog/prices.py BASE_PRICES and TIER_MULTIPLIERS are both correct. The bug is entirely in the cache key in catalog/cache.py -- _compute_price() itself computes the right value every time, but get_price() only calls it on the first request per item, regardless of tier.'

setup_phase_a
