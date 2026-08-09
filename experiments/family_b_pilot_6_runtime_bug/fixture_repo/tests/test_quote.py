import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from catalog.quote import build_quote


def test_quote_compares_tiers_for_the_same_item():
    prices = build_quote([("widget", "standard"), ("widget", "premium")])
    # The premium tier costs 25% more than standard for the same item.
    assert prices == [40.0, 50.0]
