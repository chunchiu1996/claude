"""Consignment statements: what each factory has sold, our commission, and what we owe them."""
from .orders import EARNING_STATUSES
from .pricing import commission_cents

_EARNING_SQL = "(" + ", ".join(f"'{s}'" for s in EARNING_STATUSES) + ")"


def supplier_statement(db, supplier_id):
    rows = db.execute(
        f"""SELECT oi.*, o.number, o.created_at AS sold_at, o.status
            FROM order_items oi JOIN orders o ON o.id = oi.order_id
            WHERE oi.supplier_id = ? AND o.status IN {_EARNING_SQL}
            ORDER BY o.created_at DESC, oi.id DESC""",
        (supplier_id,),
    ).fetchall()
    sales = []
    for row in rows:
        sale = dict(row)
        sale["commission_cents"] = commission_cents(row["line_total_cents"], row["commission_rate"])
        sale["net_cents"] = row["line_total_cents"] - sale["commission_cents"]
        sales.append(sale)
    gross = sum(s["line_total_cents"] for s in sales)
    commission = sum(s["commission_cents"] for s in sales)
    payouts = db.execute(
        "SELECT * FROM payouts WHERE supplier_id = ? ORDER BY created_at DESC, id DESC", (supplier_id,)
    ).fetchall()
    paid_out = sum(p["amount_cents"] for p in payouts)

    products = db.execute(
        f"""SELECT p.id, p.sku, p.name, p.unit, p.stock_qty, p.price_cents, p.active,
               (SELECT COALESCE(SUM(qty_change), 0) FROM stock_movements m
                  WHERE m.product_id = p.id AND m.kind = 'receive') AS received,
               (SELECT COALESCE(SUM(oi.qty), 0) FROM order_items oi JOIN orders o ON o.id = oi.order_id
                  WHERE oi.product_id = p.id AND o.status IN {_EARNING_SQL}) AS sold,
               (SELECT COALESCE(SUM(oi.qty), 0) FROM order_items oi JOIN orders o ON o.id = oi.order_id
                  WHERE oi.product_id = p.id AND o.status = 'pending_payment') AS reserved
            FROM products p WHERE p.supplier_id = ? ORDER BY p.name""",
        (supplier_id,),
    ).fetchall()
    movements = db.execute(
        """SELECT m.*, p.sku, p.name FROM stock_movements m JOIN products p ON p.id = m.product_id
           WHERE p.supplier_id = ? ORDER BY m.created_at DESC, m.id DESC LIMIT 50""",
        (supplier_id,),
    ).fetchall()
    return {
        "sales": sales,
        "gross_cents": gross,
        "commission_cents": commission,
        "net_cents": gross - commission,
        "payouts": payouts,
        "paid_out_cents": paid_out,
        "balance_cents": gross - commission - paid_out,
        "products": products,
        "movements": movements,
        "stock_value_cents": sum(p["stock_qty"] * p["price_cents"] for p in products),
    }


def dashboard_stats(db):
    one = lambda sql, *args: db.execute(sql, args).fetchone()[0]  # noqa: E731
    return {
        "revenue_30d": one(
            f"SELECT COALESCE(SUM(total_cents), 0) FROM orders WHERE status IN {_EARNING_SQL} "
            "AND created_at >= datetime('now', '-30 days')"
        ),
        "orders_30d": one(
            f"SELECT COUNT(*) FROM orders WHERE status IN {_EARNING_SQL} AND created_at >= datetime('now', '-30 days')"
        ),
        "to_fulfill": one("SELECT COUNT(*) FROM orders WHERE status = 'paid'"),
        "awaiting_payment": one("SELECT COUNT(*) FROM orders WHERE status = 'pending_payment'"),
        "new_inquiries": one("SELECT COUNT(*) FROM inquiries WHERE status = 'new'"),
        "stock_value": one("SELECT COALESCE(SUM(stock_qty * price_cents), 0) FROM products WHERE active = 1"),
        "sku_count": one("SELECT COUNT(*) FROM products WHERE active = 1"),
    }
