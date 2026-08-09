"""Pricing configuration."""

# Per-tier bulk-order quantity thresholds and discount rates.
THRESHOLDS = {
    "silver": {"quantity": 50, "rate": 0.05},
    "gold": {"quantity": 20, "rate": 0.10},
    "platinum": {"quantity": 10, "rate": 0.15},
}

# Legacy fallback threshold, kept for an older pricing scheme that no longer
# applies to tiered bulk orders. Nothing in the current pricing model should
# read this for tier-based discounts.
DEFAULT_THRESHOLD = 100
