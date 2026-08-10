import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from billing.invoice import calculate_invoice


def test_invoice_applies_late_fee_when_overdue():
    order = {"subtotal": 1000.0, "days_overdue": 20, "grace_days": 10}
    # 20 days overdue against a 10-day grace period is unambiguously overdue;
    # the 2% late fee should apply.
    assert calculate_invoice(order) == 1020.0
