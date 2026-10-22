from decimal import Decimal
from app.price_engine import calculate_price

def test_direct_price():
    assert calculate_price("direct", 100, 125, Decimal("1.5"), 10) == 125

def test_multiplier_price_rounding_and_delta():
    assert calculate_price("multiplier", 1000, None, Decimal("1.075"), -20) == 1055

def test_missing_base_has_no_derived_price():
    assert calculate_price("multiplier", None, None, 1.2, 0) is None

def test_unknown_mode_rejected():
    try: calculate_price("other", 100, None, 1, 0)
    except ValueError: return
    raise AssertionError("unknown mode should raise ValueError")
