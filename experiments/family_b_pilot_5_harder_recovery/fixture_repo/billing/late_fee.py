"""Late-fee calculation."""

from billing import policy


def apply_late_fee(subtotal: float, region: str, days_overdue: int) -> float:
    region_policy = policy.DEPRECATED_POLICY.get(region)
    if region_policy is None:
        return subtotal
    if days_overdue > region_policy["grace_days"]:
        return subtotal * (1 + region_policy["fee_rate"])
    return subtotal
