"""Quote requests (no prices; we reply with pricing) and factory applications."""
import secrets
from datetime import datetime, timezone

from .catalog import get_tiers
from .pricing import fmt_money, pluralize, unit_price

CONTACT_PREFS = {"email": "Email", "whatsapp": "WhatsApp", "phone": "Phone call", "wechat": "WeChat"}
STATUSES = ("new", "contacted", "quoted", "won", "lost")


def _new_reference(db, prefix):
    while True:
        reference = f"{prefix}-{datetime.now(timezone.utc):%y%m%d}-{secrets.randbelow(100000):05d}"
        if not db.execute("SELECT 1 FROM inquiries WHERE reference = ?", (reference,)).fetchone():
            return reference


def create(db, kind, data, lines=()):
    """Save an inquiry. `lines` are quote-list lines: {"product": row, "qty": n}."""
    inquiry_id = db.execute(
        """INSERT INTO inquiries (kind, reference, access_token, contact_pref, name, email, phone, company,
               role, location, items, message, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            kind,
            _new_reference(db, "Q" if kind == "quote" else "F"),
            secrets.token_urlsafe(18),
            data.get("contact_pref") or "email",
            data.get("name", ""),
            data.get("email", ""),
            data.get("phone") or None,
            data.get("company") or None,
            data.get("role") or None,
            data.get("location") or None,
            data.get("items") or None,
            data.get("message") or None,
            data.get("source") or None,
        ),
    ).lastrowid
    db.executemany(
        "INSERT INTO inquiry_items (inquiry_id, product_id, sku, name, unit, qty) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (inquiry_id, line["product"]["id"], line["product"]["sku"], line["product"]["name"],
             line["product"]["unit"], line["qty"])
            for line in lines
        ],
    )
    db.commit()
    return get(db, inquiry_id)


def get(db, inquiry_id):
    return db.execute("SELECT * FROM inquiries WHERE id = ?", (inquiry_id,)).fetchone()


def get_items(db, inquiry_id):
    return db.execute(
        """SELECT ii.*, p.slug, p.stock_qty, p.price_cents, p.quote_only, p.moq, p.unit_note
           FROM inquiry_items ii LEFT JOIN products p ON p.id = ii.product_id
           WHERE ii.inquiry_id = ? ORDER BY ii.id""",
        (inquiry_id,),
    ).fetchall()


def item_line(item):
    return f"• {item['sku']} – {item['name']} × {item['qty']:,} {pluralize(item['unit'] or '', item['qty'])}".rstrip()


def request_text(site_name, inquiry, items, url=None):
    """The customer's message to us (WhatsApp / email). No prices."""
    lines = [f"Hi {site_name}, I'd like a quote please. Ref {inquiry['reference']}", ""]
    lines += [item_line(i) for i in items]
    if inquiry["items"]:
        lines += ["", "Other items:", inquiry["items"]]
    if inquiry["message"]:
        lines += ["", inquiry["message"]]
    lines += ["", f"Name: {inquiry['name']}" + (f" ({inquiry['company']})" if inquiry["company"] else "")]
    if inquiry["location"]:
        lines.append(f"ZIP: {inquiry['location']}")
    if url:
        lines.append(f"Details: {url}")
    return "\n".join(lines)


def reply_draft(db, site, inquiry, items):
    """A price quote for staff to edit and send back. Listed prices are pre-filled; quote-only items are blank."""
    lines = [f"Hi {inquiry['name']}, thank you for your quote request {inquiry['reference']}.", "", "Our pricing:"]
    total, complete = 0, bool(items) and not inquiry["items"]
    for item in items:
        if item["product_id"] and item["price_cents"] and not item["quote_only"]:
            price = unit_price(item["price_cents"], get_tiers(db, item["product_id"]), item["qty"])
            total += price * item["qty"]
            lines.append(f"{item_line(item)} @ {fmt_money(price)} = {fmt_money(price * item['qty'])}")
        else:
            complete = False
            lines.append(f"{item_line(item)} @ $____ = $____")
    if inquiry["items"]:
        lines += ["", f"Other items ({inquiry['items']}): $____"]
    if complete:
        lines += ["", f"Subtotal: {fmt_money(total)}"]
    lines += ["", f"Pickup at our {site['warehouse']} warehouse, or we can quote delivery. "
                  "Prices are valid for 7 days.", "", site["name"]]
    if site.get("phone"):
        lines.append(site["phone"])
    return "\n".join(lines)


def seller_reply_draft(seller, inquiry, template_url):
    """Reply to a factory / stock owner who applied on the Chinese site (in Chinese)."""
    lines = [
        f"{inquiry['name']} 您好：",
        "",
        f"感谢您申请与 {seller['name']} 合作（申请编号 {inquiry['reference']}）。",
        "",
        "为了尽快评估和安排上架，请您准备以下资料：",
        f"1. 产品表：请用我们的模板填写货号、品名、数量和价格 {template_url}",
        "2. 产品图片和规格参数",
        "3. 货物所在仓库、数量，以及预计到仓时间",
        "",
        "收到资料后，我们会和您确认底价、佣金和结算方式。",
        "",
        seller["name"],
    ]
    if seller.get("wechat"):
        lines.append(f"微信：{seller['wechat']}")
    if seller.get("phone"):
        lines.append(f"电话：{seller['phone']}")
    lines.append(f"邮箱：{seller['email']}")
    return "\n".join(lines)
