"""Task 2's objective success check.

FAILS on the fixture as shipped (the bug under test). Passes once
`reserve_stock` in `src/orders/inventory.py` is fixed to accumulate
reservations per SKU (`_reserved[item.sku] = reserved_qty + item.quantity`)
instead of overwriting them. This is the only file the task's fix needs to
touch.
"""

import pytest

from orders.inventory import reserve_stock, reset_reservations
from orders.models import LineItem, Order


@pytest.fixture(autouse=True)
def _clean_reservations():
    reset_reservations()
    yield
    reset_reservations()


def test_reservations_accumulate_across_orders_for_the_same_sku():
    stock = {"SKU1": 10}
    reserve_stock(Order("o1", [LineItem("SKU1", 5, 1.0)]), stock)
    reserve_stock(Order("o2", [LineItem("SKU1", 4, 1.0)]), stock)

    # 9 of 10 units are now reserved -- a third order for 6 more must fail.
    with pytest.raises(ValueError):
        reserve_stock(Order("o3", [LineItem("SKU1", 6, 1.0)]), stock)


def test_a_single_reservation_within_stock_succeeds():
    stock = {"SKU1": 10}
    reserve_stock(Order("o1", [LineItem("SKU1", 5, 1.0)]), stock)  # must not raise


def test_independent_skus_do_not_interfere():
    stock = {"SKU1": 10, "SKU2": 5}
    reserve_stock(Order("o1", [LineItem("SKU1", 10, 1.0)]), stock)
    reserve_stock(Order("o2", [LineItem("SKU2", 5, 1.0)]), stock)  # must not raise
