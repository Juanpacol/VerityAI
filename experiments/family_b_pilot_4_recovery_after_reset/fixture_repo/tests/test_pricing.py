import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pricing.order import calculate_total


def test_bulk_discount_uses_correct_threshold():
    order = {"tier": "gold", "quantity": 25, "unit_price": 10.0}
    # Gold tier's bulk threshold is 20 units; 25 units should trigger the
    # tier's 10% discount rate.
    assert calculate_total(order) == 225.0
