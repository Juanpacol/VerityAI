"""Quote assembly."""

from catalog.cache import get_price


def build_quote(lines: list[tuple[str, str]]) -> list[float]:
    """Return the price for each (item, tier) line, in order."""
    return [get_price(item, tier) for item, tier in lines]
