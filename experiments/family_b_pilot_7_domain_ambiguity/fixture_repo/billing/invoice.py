"""Invoice total calculation."""

from billing.late_fee import apply_late_fee


def calculate_invoice(order: dict) -> float:
    return apply_late_fee(order["subtotal"], order["days_overdue"], order["grace_days"])
