"""Tax calculation. Healthy -- correctly reads the current rate table."""

from billing import tax_rates


def apply_tax(subtotal: float, region: str) -> float:
    rate = tax_rates.REGION_RATES.get(region, 0.0)
    return subtotal * (1 + rate)
