"""Order checkout: ties pricing and inventory together."""

from orders.inventory import reserve_stock
from orders.pricing import calculate_total


class OrderService:
    def __init__(self, available_stock: dict[str, int]):
        self.available_stock = available_stock

    def checkout(self, order) -> float:
        """Reserve stock, then return the order's final total."""
        reserve_stock(order, self.available_stock)
        return calculate_total(order)
