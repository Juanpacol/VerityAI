"""Deprecated pricing module, kept only for historical reference.

Nothing in `service.py` calls into this module any more -- `pricing.py`
replaced it. Present in this fixture deliberately, as a distractor: a
grep for `apply_discount` finds two matches, and only one of them is the
function actually wired into checkout.
"""


def apply_discount(subtotal: float, code: str | None) -> float:
    """Old flat-dollar discount scheme. Unused -- do not edit this one."""
    flat_discounts = {"SAVE10": 10.0, "SAVE20": 20.0}
    return subtotal - flat_discounts.get(code, 0.0)
