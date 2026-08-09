"""Task 1's objective success check.

FAILS on the fixture as shipped (the bug under test). Passes once
`apply_discount` in `src/orders/pricing.py` is fixed to subtract the
discount rather than add it. This is the only file the task's fix needs to
touch.
"""

from orders.models import LineItem, Order
from orders.pricing import calculate_total


def test_discount_reduces_the_total():
    order = Order(order_id="o1", items=[LineItem("SKU1", 1, 100.0)], discount_code="SAVE10")

    total = calculate_total(order)

    # subtotal 100, 10% discount -> 90, 8% tax -> 97.2
    assert total == 97.2


def test_no_discount_code_is_unaffected():
    order = Order(order_id="o2", items=[LineItem("SKU1", 1, 100.0)], discount_code=None)

    assert calculate_total(order) == 108.0


def test_an_unknown_discount_code_is_ignored():
    order = Order(order_id="o3", items=[LineItem("SKU1", 1, 50.0)], discount_code="NOTREAL")

    assert calculate_total(order) == 54.0
