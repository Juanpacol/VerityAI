#!/usr/bin/env bash
# Sets up pilot 6's 10 trial directories from fixture_repo/.
#
# Same pattern as pilots 4 and 5 (experiments/family_b_pilot_4.../,
# family_b_pilot_5.../): naive_N/ is a plain copy, verity_N/ additionally
# gets a .verity/ directory pre-loaded (via the real `verity task` /
# `verity remember` CLI) with a fabricated prior investigation.
#
# This fixture's bug is call-sequence-dependent, not a single wrong
# constant (see README): get_price() in catalog/cache.py caches by `item`
# only, ignoring `tier`, so a second call for the same item under a
# different tier silently returns the first call's cached price. Reading
# get_price() in isolation doesn't reveal this -- it looks like reasonable
# memoization -- it only shows up by tracing the actual call sequence
# catalog/quote.py::build_quote() makes, or by running the failing test and
# reasoning backward from the wrong value.
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
    verity task "Fix failing test test_quote_compares_tiers_for_the_same_item in tests/test_quote.py" \
      --next "Change get_price() in catalog/cache.py to key _price_cache by (item, tier) instead of item alone" \
      --file "catalog/cache.py"
    verity remember decision 'Root cause: get_price() in catalog/cache.py memoizes _price_cache keyed only by item, ignoring tier. When build_quote() requests the same item under two different tiers in one call (e.g. standard then premium), the second call silently returns the first calls cached price instead of recomputing for its own tier. Found by tracing the actual sequence of get_price() calls build_quote() makes, not by reading get_price() in isolation -- it reads like ordinary memoization until you follow the call order.'
    verity remember discovery 'catalog/prices.py BASE_PRICES and TIER_MULTIPLIERS are both correct. The bug is entirely in the cache key in catalog/cache.py -- _compute_price() itself computes the right value every time, but get_price() only calls it on the first request per item, regardless of tier.'
  )
  echo "prepared: naive_$i, verity_$i"
done
