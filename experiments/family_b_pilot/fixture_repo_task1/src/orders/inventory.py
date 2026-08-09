"""Stock reservation for checkout.

Reservations accumulate per SKU across calls (multiple orders can each
reserve part of the same SKU's stock before any of them ships), tracked in
`_reserved`.
"""

_reserved: dict[str, int] = {}


def _current_reserved(sku: str) -> int:
    return _reserved.get(sku, 0)


def reset_reservations() -> None:
    """Test hook: clear all reservations between test cases."""
    _reserved.clear()


def reserve_stock(order, available: dict[str, int]) -> None:
    """Reserve stock for every line item in `order`.

    Raises `ValueError` if any item would oversell what's actually left
    once existing reservations for that SKU are accounted for.
    """
    for item in order.items:
        available_qty = available.get(item.sku, 0)
        reserved_qty = _current_reserved(item.sku)
        if available_qty - reserved_qty < item.quantity:
            raise ValueError(f"insufficient stock for {item.sku}")
        _reserved[item.sku] = reserved_qty + item.quantity
