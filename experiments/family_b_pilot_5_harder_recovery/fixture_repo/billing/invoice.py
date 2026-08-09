"""Invoice total calculation."""

from billing.late_fee import apply_late_fee
from billing.tax import apply_tax


def calculate_invoice(order: dict) -> float:
    subtotal = order["subtotal"]
    with_tax = apply_tax(subtotal, order["region"])
    return apply_late_fee(with_tax, order["region"], order["days_overdue"])
