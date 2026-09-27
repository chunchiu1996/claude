"""Session baskets, both {product_id(str): qty}.

- cart: priced items for checkout (prices always recomputed from the database)
- quote list: items the customer wants a price quote for (no prices shown, no stock limit)
"""
from flask import session

from .catalog import PRODUCT_SELECT, get_tiers
from .pricing import next_tier, pluralize, unit_price


def get_cart():
    return dict(session.get("cart") or {})


def save_cart(cart):
    session["cart"] = {k: v for k, v in cart.items() if v > 0}


def clear_cart():
    session.pop("cart", None)


def get_quote_list():
    return dict(session.get("quote") or {})


def save_quote_list(quote):
    session["quote"] = {k: v for k, v in quote.items() if v > 0}


def build_quote_lines(db, quote):
    lines = []
    for pid, qty in quote.items():
        product = db.execute(PRODUCT_SELECT + " WHERE p.id = ? AND p.active = 1", (int(pid),)).fetchone()
        if product is not None:
            lines.append({"product": product, "qty": qty})
    return lines


def build_lines(db, cart):
    lines, problems = [], []
    for pid, qty in cart.items():
        product = db.execute(PRODUCT_SELECT + " WHERE p.id = ? AND p.active = 1", (int(pid),)).fetchone()
        if product is None:
            continue
        tiers = get_tiers(db, product["id"])
        price = unit_price(product["price_cents"], tiers, qty)
        if product["quote_only"]:
            problems.append(f"{product['name']} is priced by quote only. Move your cart to a quote request below.")
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
