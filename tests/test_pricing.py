import pytest

from shop.pricing import (
    commission_cents,
    fmt_money,
    format_tiers,
    next_tier,
    parse_money,
    parse_tiers,
    pluralize,
    unit_price,
)


def test_money_round_trip():
    assert parse_money("$1,299.50") == 129950
    assert parse_money("42.505") == 4251
    assert parse_money("") is None
    assert fmt_money(129950) == "$1,299.50"
    assert fmt_money(-5) == "-$0.05"
    with pytest.raises(ValueError):
        parse_money("abc")
    with pytest.raises(ValueError):
        parse_money("-3")


def test_parse_tiers_accepts_common_separators():
    assert parse_tiers("100:35.45 | 20:38.99") == [(20, 3899), (100, 3545)]
    assert parse_tiers("20+: $38.99; 1,000:30") == [(20, 3899), (1000, 3000)]
    assert parse_tiers("") == []
    assert format_tiers([(20, 3899), (100, 3545)]) == "20:38.99 | 100:35.45"
    for bad in ("20", "1:5.00", "x:5", "20:"):
        with pytest.raises(ValueError):
            parse_tiers(bad)


def test_unit_price_uses_best_eligible_tier():
    tiers = [(20, 3899), (100, 3545)]
    assert unit_price(4250, tiers, 1) == 4250
    assert unit_price(4250, tiers, 20) == 3899
    assert unit_price(4250, tiers, 500) == 3545
    assert next_tier(4250, tiers, 5) == (20, 3899)
    assert next_tier(4250, tiers, 20) == (100, 3545)
    assert next_tier(4250, tiers, 100) is None


def test_commission_and_plural():
    assert commission_cents(10001, 0.2) == 2000
    assert commission_cents(12345, 0.18) == 2222
    assert pluralize("box", 2) == "boxes"
    assert pluralize("piece", 1) == "piece"
    assert pluralize("sq ft", 5) == "sq ft"
