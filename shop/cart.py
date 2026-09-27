"""Session cart: {product_id(str): qty}. Prices are always recomputed from the database."""
from flask import session

from .catalog import PRODUCT_SELECT, get_tiers
from .pricing import next_tier, pluralize, unit_price


def get_cart():
    return dict(session.get("cart") or {})


def save_cart(cart):
    session["cart"] = {k: v for k, v in cart.items() if v > 0}


def clear_cart():
    session.pop("cart", None)


def build_lines(db, cart):
    lines, problems = [], []
    for pid, qty in cart.items():
        product = db.execute(PRODUCT_SELECT + " WHERE p.id = ? AND p.active = 1", (int(pid),)).fetchone()
        if product is None:
            continue
        tiers = get_tiers(db, product["id"])
        price = unit_price(product["price_cents"], tiers, qty)
        if qty < product["moq"]:
            problems.append(
                f"{product['name']}: minimum order is {product['moq']} {pluralize(product['unit'], product['moq'])}."
            )
        if qty > product["stock_qty"]:
            problems.append(
                f"{product['name']}: only {product['stock_qty']} {pluralize(product['unit'], product['stock_qty'])} in stock."
            )
        lines.append(
            {
                "product": product,
                "qty": qty,
                "unit_price_cents": price,
                "line_total_cents": price * qty,
                "savings_cents": (product["price_cents"] - price) * qty,
                "next_tier": next_tier(product["price_cents"], tiers, qty),
            }
        )
    subtotal = sum(line["line_total_cents"] for line in lines)
    return lines, subtotal, problems
