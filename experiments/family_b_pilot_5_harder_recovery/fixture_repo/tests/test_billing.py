import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from billing.invoice import calculate_invoice


def test_invoice_applies_current_late_fee_policy():
    order = {"subtotal": 1000.0, "region": "US", "days_overdue": 20}
    # US grace period under the current policy is 10 days; 20 days overdue
    # should trigger the 1.5% late fee on top of the 7% tax.
    assert calculate_invoice(order) == 1086.05
