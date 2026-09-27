"""Order placement, cancellation and status changes. Stock is reserved when an order is placed."""
import secrets
from datetime import datetime, timezone

STATUSES = {
    "pending_payment": "Awaiting payment",
    "paid": "Paid – to fulfill",
    "fulfilled": "Fulfilled",
    "cancelled": "Cancelled",
}
# Orders in these states count toward sales and supplier earnings.
EARNING_STATUSES = ("paid", "fulfilled")
# Unpaid card checkouts older than this release their reserved stock (Stripe sessions expire in 24h).
CARD_HOLD_HOURS = 24


class StockError(Exception):
    pass


def _new_number(db):
    while True:
        number = f"{datetime.now(timezone.utc):%y%m%d}-{secrets.randbelow(100000):05d}"
        if not db.execute("SELECT 1 FROM orders WHERE number = ?", (number,)).fetchone():
            return number


def place_order(db, customer, lines, shipping_cents, payment_method):
    """Atomically reserve stock and create the order. Returns the new order row."""
    subtotal = sum(line["line_total_cents"] for line in lines)
    number = _new_number(db)
    try:
        for line in lines:
            product = line["product"]
            cur = db.execute(
                "UPDATE products SET stock_qty = stock_qty - ?, updated_at = datetime('now') "
                "WHERE id = ? AND stock_qty >= ? AND active = 1",
                (line["qty"], product["id"], line["qty"]),
            )
            if cur.rowcount != 1:
                raise StockError(f"Sorry, we no longer have {line['qty']} of {product['name']} in stock.")
        order_id = db.execute(
            """INSERT INTO orders (number, access_token, payment_method, customer_name, email, phone,
                   company, customer_type, fulfillment, address, city, state, zip, notes,
                   subtotal_cents, shipping_cents, total_cents)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                number,
                secrets.token_urlsafe(18),
                payment_method,
                customer["customer_name"],
                customer["email"],
                customer.get("phone"),
                customer.get("company"),
                customer.get("customer_type"),
                customer["fulfillment"],
                customer.get("address"),
                customer.get("city"),
                customer.get("state"),
                customer.get("zip"),
                customer.get("notes"),
                subtotal,
                shipping_cents,
                subtotal + shipping_cents,
            ),
        ).lastrowid
        for line in lines:
            product = line["product"]
            supplier = db.execute(
                "SELECT commission_rate FROM suppliers WHERE id = ?", (product["supplier_id"],)
            ).fetchone()
            db.execute(
                """INSERT INTO order_items (order_id, product_id, supplier_id, sku, name, unit, qty,
                       unit_price_cents, line_total_cents, commission_rate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id,
                    product["id"],
                    product["supplier_id"],
                    product["sku"],
                    product["name"],
                    product["unit"],
                    line["qty"],
                    line["unit_price_cents"],
                    line["line_total_cents"],
                    supplier["commission_rate"] if supplier else 0,
                ),
            )
            db.execute(
                "INSERT INTO stock_movements (product_id, qty_change, kind, reference) VALUES (?, ?, 'sale', ?)",
                (product["id"], -line["qty"], f"Order {number}"),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_order(db, order_id)


def get_order(db, order_id):
    return db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def get_items(db, order_id):
    return db.execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)).fetchall()


def cancel_order(db, order_id, reason=None):
    """Cancel and return reserved stock. Returns False if it was already cancelled."""
    order = get_order(db, order_id)
    if order is None or order["status"] == "cancelled":
        return False
    for item in get_items(db, order_id):
        if item["product_id"] is None:
            continue
        db.execute(
            "UPDATE products SET stock_qty = stock_qty + ?, updated_at = datetime('now') WHERE id = ?",
            (item["qty"], item["product_id"]),
        )
        db.execute(
            "INSERT INTO stock_movements (product_id, qty_change, kind, reference, note) VALUES (?, ?, 'cancel', ?, ?)",
            (item["product_id"], item["qty"], f"Order {order['number']}", reason),
        )
    db.execute(
        "UPDATE orders SET status = 'cancelled', updated_at = datetime('now') WHERE id = ?", (order_id,)
    )
    db.commit()
    return True


def set_status(db, order_id, status):
    if status not in STATUSES:
        raise ValueError("Unknown status")
    if status == "cancelled":
        return cancel_order(db, order_id, "Cancelled by staff")
    order = get_order(db, order_id)
    if order["status"] == "cancelled":
        raise ValueError("Cancelled orders can't be reopened – their stock was already returned. Place a new order instead.")
    db.execute("UPDATE orders SET status = ?, updated_at = datetime('now') WHERE id = ?", (status, order_id))
    db.commit()
    return True


def release_stale_card_orders(db):
    """Cancel card checkouts that were abandoned before payment, so their stock is sellable again."""
    stale = db.execute(
        "SELECT id FROM orders WHERE status = 'pending_payment' AND payment_method = 'card' "
        "AND created_at < datetime('now', ?)",
        (f"-{CARD_HOLD_HOURS} hours",),
    ).fetchall()
    for row in stale:
        cancel_order(db, row["id"], "Card checkout abandoned")
    return len(stale)
