from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

def calculate_price(mode: str, base_price: Optional[int], direct_price: Optional[int], factor: Decimal | int | float | str, fixed_delta: int = 0) -> Optional[int]:
    """Return an integer Rial price, or None when required source is absent."""
    if mode == "direct":
        return direct_price
    if mode != "multiplier":
        raise ValueError("mode must be 'direct' or 'multiplier'")
    if base_price is None:
        return None
    amount = (Decimal(base_price) * Decimal(str(factor))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return max(0, int(amount) + int(fixed_delta))
