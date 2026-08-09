"""Domain models for the toy order-processing service."""

from dataclasses import dataclass, field


@dataclass
class LineItem:
    sku: str
    quantity: int
    unit_price: float


@dataclass
class Order:
    order_id: str
    items: list[LineItem] = field(default_factory=list)
    discount_code: str | None = None

    @property
    def subtotal(self) -> float:
        return sum(item.unit_price * item.quantity for item in self.items)
