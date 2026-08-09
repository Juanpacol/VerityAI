"""Price lookup with memoization."""

from catalog.prices import BASE_PRICES, TIER_MULTIPLIERS

_price_cache: dict[str, float] = {}


def _compute_price(item: str, tier: str) -> float:
    return BASE_PRICES[item] * TIER_MULTIPLIERS[tier]


def get_price(item: str, tier: str) -> float:
    if item not in _price_cache:
        _price_cache[item] = _compute_price(item, tier)
    return _price_cache[item]
