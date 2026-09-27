"""Bulk product/stock import from the CSV spreadsheets factories send with each shipment.

The import is all-or-nothing: if any row has an error nothing is saved, so re-uploading a
corrected file never double-counts received stock.
"""
import csv
import io

from .catalog import (
    get_or_create_category,
    get_or_create_supplier,
    normalize_specs,
    record_movement,
    set_tiers,
    slugify,
    unique_slug,
)
from .pricing import parse_money, parse_tiers

COLUMNS = [
    "sku", "name", "category", "supplier", "price", "compare_at_price", "unit", "unit_note",
    "moq", "stock", "warehouse", "tiers", "description", "specs", "image_url", "featured", "active",
]
HEADER_ALIASES = {"retail_price": "price", "qty": "stock", "quantity": "stock", "factory": "supplier",
                  "min_order": "moq", "volume_pricing": "tiers", "image": "image_url"}
TRUE_VALUES = {"1", "y", "yes", "true", "x"}


class ImportResult:
    def __init__(self):
        self.created = 0
        self.updated = 0
        self.units_received = 0
        self.errors = []  # (row number, message)

    @property
    def ok(self):
        return not self.errors


def decode(raw):
    """Excel on Chinese-locale Windows often saves CSV as GBK, so fall back to GB18030."""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _norm_header(name):
    key = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    return HEADER_ALIASES.get(key, key)


def _int(value, field):
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        raise ValueError(f"{field} must be a whole number, got {value!r}") from None


def import_products(db, text, reference=None, stock_mode="add"):
    """stock_mode 'add': the stock column is units just received (a new shipment).
    stock_mode 'set': the stock column is the full count on hand (a stocktake)."""
    result = ImportResult()
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        result.errors.append((1, "The file is empty."))
        return result
    reader.fieldnames = [_norm_header(h) for h in reader.fieldnames]
    if "sku" not in reader.fieldnames:
        result.errors.append((1, "Missing a 'sku' column. Download the template to see the expected columns."))
        return result

    seen = set()
    try:
        for rownum, raw_row in enumerate(reader, start=2):
            row = {k: (v or "").strip() for k, v in raw_row.items() if k}
            if not any(row.values()):
                continue
            try:
                _import_row(db, row, reference, stock_mode, result, seen)
            except ValueError as exc:
                result.errors.append((rownum, str(exc)))
        if result.errors:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    return result


def _import_row(db, row, reference, stock_mode, result, seen):
    sku = row.get("sku", "")
    if not sku:
        raise ValueError("sku is required")
    if sku.lower() in seen:
        raise ValueError(f"SKU {sku} appears more than once in this file")
    seen.add(sku.lower())

    existing = db.execute("SELECT * FROM products WHERE sku = ? COLLATE NOCASE", (sku,)).fetchone()
    fields = {}
    if row.get("name"):
        fields["name"] = row["name"]
    if row.get("price"):
        fields["price_cents"] = parse_money(row["price"])
    if "compare_at_price" in row and row["compare_at_price"] != "":
        fields["compare_at_cents"] = parse_money(row["compare_at_price"])
    for col in ("unit", "unit_note", "warehouse", "description", "image_url"):
        if row.get(col):
            fields[col] = row[col]
    if row.get("specs"):
        fields["specs"] = normalize_specs(row["specs"])
    if row.get("moq"):
        fields["moq"] = _int(row["moq"], "moq")
        if fields["moq"] < 1:
            raise ValueError("moq must be at least 1")
    for flag in ("featured", "active"):
        if row.get(flag):
            fields[flag] = 1 if row[flag].lower() in TRUE_VALUES else 0
    if row.get("category"):
        fields["category_id"] = get_or_create_category(db, row["category"])
    if row.get("supplier"):
        fields["supplier_id"] = get_or_create_supplier(db, row["supplier"])
    tiers = parse_tiers(row["tiers"]) if row.get("tiers") else None
    stock = _int(row["stock"], "stock") if row.get("stock") else None
    if stock is not None and stock < 0:
        raise ValueError("stock can't be negative")

    if existing is None:
        if "name" not in fields or "price_cents" not in fields:
            raise ValueError(f"New SKU {sku} needs at least a name and a price")
        fields["sku"] = sku
        fields["slug"] = unique_slug(db, "products", slugify(fields["name"]))
        cols = ", ".join(fields)
        product_id = db.execute(
            f"INSERT INTO products ({cols}) VALUES ({', '.join('?' for _ in fields)})",
            list(fields.values()),
        ).lastrowid
        current_stock = 0
        result.created += 1
    else:
        product_id = existing["id"]
        current_stock = existing["stock_qty"]
        if fields:
            assignments = ", ".join(f"{col} = ?" for col in fields)
            db.execute(
                f"UPDATE products SET {assignments}, updated_at = datetime('now') WHERE id = ?",
                [*fields.values(), product_id],
            )
        result.updated += 1

    if tiers is not None:
        set_tiers(db, product_id, tiers)

    if stock is not None:
        if stock_mode == "set":
            delta = stock - current_stock
            if delta:
                record_movement(db, product_id, delta, "adjust", reference, "Stock count import")
        elif stock:
            record_movement(db, product_id, stock, "receive", reference, "CSV import")
            result.units_received += stock


def template_csv():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    writer.writerow([
        "SPC-7MM-OAK", "Rigid Core SPC Vinyl Plank - Natural Oak, 7mm", "Flooring",
        "Example Flooring Factory", "42.50", "66.00", "box", "23.64 sq ft per box", "1", "860",
        "Ontario, CA", "20:38.99 | 100:35.45", "Waterproof click-lock plank with attached IXPE pad.",
        "Thickness: 7mm; Wear layer: 20 mil; Plank size: 7.2 x 60 in", "", "yes", "yes",
    ])
    return out.getvalue()
