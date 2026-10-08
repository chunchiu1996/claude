"""Shared catalog queries and helpers used by the storefront, admin and importer."""
import re
import secrets

PRODUCT_SELECT = """
SELECT p.*, c.name AS category_name, c.slug AS category_slug, s.name AS supplier_name,
       (SELECT MIN(t.price_cents) FROM price_tiers t WHERE t.product_id = p.id) AS best_tier_cents
FROM products p
LEFT JOIN categories c ON c.id = p.category_id
LEFT JOIN suppliers s ON s.id = p.supplier_id
"""

CUSTOMER_TYPES = [
    "Homeowner",
    "Contractor / Remodeler",
    "Retailer / Distributor",
    "Property manager / Landlord",
    "Other",
]


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:80] or "item"


def unique_slug(db, table, base, exclude_id=None, alt=None):
    """base, else base-<alt> (e.g. the SKU, so many same-named products stay fast and readable), else base-2, -3…"""
    def free(slug):
        row = db.execute(f"SELECT id FROM {table} WHERE slug = ?", (slug,)).fetchone()
        return row is None or row["id"] == exclude_id

    if free(base):
        return base
    if alt and free(f"{base}-{alt}"):
        return f"{base}-{alt}"
    n = 2
    while not free(f"{base}-{n}"):
        n += 1
    return f"{base}-{n}"


def get_tiers(db, product_id):
    rows = db.execute(
        "SELECT min_qty, price_cents FROM price_tiers WHERE product_id = ? ORDER BY min_qty",
        (product_id,),
    )
    return [(r["min_qty"], r["price_cents"]) for r in rows]


def set_tiers(db, product_id, tiers):
    db.execute("DELETE FROM price_tiers WHERE product_id = ?", (product_id,))
    db.executemany(
        "INSERT INTO price_tiers (product_id, min_qty, price_cents) VALUES (?, ?, ?)",
        [(product_id, qty, price) for qty, price in tiers],
    )


def get_or_create_category(db, name):
    name = name.strip()
    row = db.execute("SELECT id FROM categories WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row["id"]
    return db.execute(
        "INSERT INTO categories (name, slug) VALUES (?, ?)",
        (name, unique_slug(db, "categories", slugify(name))),
    ).lastrowid


def get_or_create_supplier(db, name):
    name = name.strip()
    row = db.execute("SELECT id FROM suppliers WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row["id"]
    return db.execute(
        "INSERT INTO suppliers (name, portal_token) VALUES (?, ?)", (name, secrets.token_urlsafe(18))
    ).lastrowid


def normalize_specs(text):
    """Accepts 'Key: Value' pairs separated by newlines or semicolons; returns newline form."""
    pairs = []
    for part in re.split(r"[\n;]+", text or ""):
        part = part.strip()
        if part:
            pairs.append(part)
    return "\n".join(pairs)


def parse_specs(text):
    specs = []
    for line in (text or "").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            specs.append((key.strip(), value.strip()))
        elif line.strip():
            specs.append((line.strip(), ""))
    return specs


def record_movement(db, product_id, qty_change, kind, reference=None, note=None):
    """Change stock and log it. Caller commits."""
    row = db.execute("SELECT stock_qty FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown product")
    if row["stock_qty"] + qty_change < 0:
        raise ValueError(f"Stock cannot go below zero (currently {row['stock_qty']})")
    db.execute(
        "UPDATE products SET stock_qty = stock_qty + ?, updated_at = datetime('now') WHERE id = ?",
        (qty_change, product_id),
    )
    db.execute(
        "INSERT INTO stock_movements (product_id, qty_change, kind, reference, note) VALUES (?, ?, ?, ?, ?)",
        (product_id, qty_change, kind, reference or None, note or None),
    )


def categories_with_counts(db):
    return db.execute(
        """SELECT c.*, COUNT(p.id) AS product_count
           FROM categories c LEFT JOIN products p ON p.category_id = c.id AND p.active = 1
           GROUP BY c.id ORDER BY c.sort_order, c.name"""
    ).fetchall()
