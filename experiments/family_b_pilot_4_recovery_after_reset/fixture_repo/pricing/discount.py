"""Bulk-order discount calculation."""

from pricing import config


def get_bulk_discount_rate(tier: str, quantity: int) -> float:
    """Return the discount rate for a bulk order, or 0.0 if none applies."""
    # BUG: this should compare against config.THRESHOLDS[tier]["quantity"],
    # the per-tier threshold -- not the legacy config.DEFAULT_THRESHOLD,
    # which is far higher and was never meant to gate tiered discounts.
    if quantity >= config.DEFAULT_THRESHOLD:
        return config.THRESHOLDS.get(tier, {}).get("rate", 0.0)
    return 0.0
