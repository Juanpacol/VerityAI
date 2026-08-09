"""Order total calculation."""

from pricing.discount import get_bulk_discount_rate


def apply_discounts(tier: str, quantity: int, unit_price: float) -> float:
    subtotal = quantity * unit_price
    rate = get_bulk_discount_rate(tier, quantity)
    return subtotal * (1 - rate)


def calculate_total(order: dict) -> float:
    return apply_discounts(order["tier"], order["quantity"], order["unit_price"])
