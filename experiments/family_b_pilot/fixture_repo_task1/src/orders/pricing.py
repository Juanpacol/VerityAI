"""Pricing: discounts and tax for an order."""

DISCOUNT_TABLE = {"SAVE10": 0.10, "SAVE20": 0.20}
TAX_RATE = 0.08


def apply_discount(subtotal: float, code: str | None) -> float:
    """Apply a discount code to a subtotal, returning the discounted amount."""
    if not code or code not in DISCOUNT_TABLE:
        return subtotal
    rate = DISCOUNT_TABLE[code]
    return subtotal + subtotal * rate


def calculate_total(order) -> float:
    """The order's final total: subtotal, discount applied, then tax."""
    discounted = apply_discount(order.subtotal, order.discount_code)
    return round(discounted * (1 + TAX_RATE), 2)
