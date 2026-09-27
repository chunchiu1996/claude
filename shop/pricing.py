"""Money helpers and volume (tiered) pricing. All amounts are integer cents."""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_TIER_SPLIT = re.compile(r"[|;\n]+")


def fmt_money(cents):
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def parse_money(value):
    """'$1,299.5' -> 129950. Blank -> None. Raises ValueError on garbage or negatives."""
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "").replace("USD", "").strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{value!r} is not a valid price") from None
    if amount < 0 or not amount.is_finite():
        raise ValueError(f"{value!r} is not a valid price")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_tiers(text):
    """'50:2.49 | 200:2.19' -> [(50, 249), (200, 219)]. Accepts | ; or newlines."""
    tiers = {}
    for part in _TIER_SPLIT.split(text or ""):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"Volume price {part!r} should look like QTY:PRICE, e.g. 50:2.49")
        qty_text, price_text = part.split(":", 1)
        try:
            qty = int(qty_text.strip().rstrip("+").replace(",", ""))
        except ValueError:
            raise ValueError(f"Volume price {part!r} has an invalid quantity") from None
        if qty < 2:
            raise ValueError(f"Volume price {part!r}: quantity must be at least 2")
        price = parse_money(price_text)
        if price is None:
            raise ValueError(f"Volume price {part!r} is missing a price")
        tiers[qty] = price
    return sorted(tiers.items())


def format_tiers(tiers):
    return " | ".join(f"{qty}:{price / 100:.2f}" for qty, price in tiers)


def unit_price(base_cents, tiers, qty):
    """Best per-unit price for buying `qty` units."""
    price = base_cents
    for min_qty, tier_price in tiers:
        if qty >= min_qty and tier_price < price:
            price = tier_price
    return price


def next_tier(base_cents, tiers, qty):
    """The next volume break above `qty` that would lower the unit price, or None."""
    current = unit_price(base_cents, tiers, qty)
    for min_qty, tier_price in tiers:
        if min_qty > qty and tier_price < current:
            return min_qty, tier_price
    return None


def commission_cents(amount_cents, rate):
    return int((Decimal(amount_cents) * Decimal(str(rate))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def pluralize(unit, n):
    if n == 1 or not unit or unit.endswith("s") or " " in unit:
        return unit
    if unit.endswith(("x", "ch", "sh")):
        return unit + "es"
    return unit + "s"
