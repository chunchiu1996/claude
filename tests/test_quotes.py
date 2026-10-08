import smtplib
import urllib.parse

from shop import notify

from .conftest import Client, product


def _quote_lines(client):
    with client.session() as s:
        return dict(s.get("quote") or {})


def test_quote_only_product_hides_price_and_cannot_be_bought(client, db):
    slab = product(db, "QTZ-SLAB-CAL-3CM")
    page = client.get(f"/p/{slab['slug']}").data.decode()
    assert "Price on request" in page and "Add to quote list" in page and "Add to cart" not in page
    assert "Price on request" in client.get("/shop/countertops").data.decode()

    client.post("/cart/add", data={"product_id": slab["id"], "qty": 1})
    with client.session() as s:
        assert not s.get("cart")


def test_quote_list_accepts_any_quantity(client, db):
    slab = product(db, "QTZ-SLAB-CAL-3CM")  # 45 in stock
    client.post("/quote/add", data={"product_id": slab["id"], "qty": 200})
    client.post("/quote/add", data={"product_id": slab["id"], "qty": 10})
    assert _quote_lines(client) == {str(slab["id"]): 210}
    html = client.get("/quote").data.decode()
    assert "QTZ-SLAB-CAL-3CM" in html and 'value="210"' in html and "$" not in html.split("<main")[1].split("</main>")[0]


def test_move_cart_to_quote(client, db):
    p = product(db, "SPC-7MM-OAK")
    client.post("/cart/add", data={"product_id": p["id"], "qty": 30})
    client.post("/quote/from-cart")
    with client.session() as s:
        assert not s.get("cart") and s["quote"] == {str(p["id"]): 30}


def test_quote_request_validation(client):
    resp = client.post("/quote", data={"name": "", "contact_pref": "whatsapp"})
    html = resp.data.decode()
    assert resp.status_code == 200
    assert "Please enter your name" in html and "WhatsApp number" in html and "describe what you need" in html


def test_quote_request_saved_notified_and_sendable(app, client, db, monkeypatch):
    app.config.update(WHATSAPP_NUMBER="+1 (626) 555-0199", CONTACT_EMAIL="sales@shop.test")
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body, reply_to=None: sent.append((to, subject, body)))

    slab, faucet = product(db, "QTZ-SLAB-CAL-3CM"), product(db, "FAU-KIT-PD-BN")
    client.post("/quote/add", data={"product_id": slab["id"], "qty": 12})
    client.post("/quote/add", data={"product_id": faucet["id"], "qty": 5})
    resp = client.post("/quote", data={
        "name": "Maria Lopez", "company": "ML Remodeling", "phone": "909-555-0123", "email": "maria@example.com",
        "contact_pref": "whatsapp", "location": "91761", "items": "Also 20 matching backsplash tiles",
        f"qty_{faucet['id']}": "8",  # quantity edited on the same form before sending
    })
    assert resp.status_code == 302 and "/quote/r/Q-" in resp.location
    assert not _quote_lines(client)

    inquiry = db.execute("SELECT * FROM inquiries WHERE name = 'Maria Lopez'").fetchone()
    items = {r["sku"]: r["qty"] for r in db.execute("SELECT * FROM inquiry_items WHERE inquiry_id = ?", (inquiry["id"],))}
    assert items == {"QTZ-SLAB-CAL-3CM": 12, "FAU-KIT-PD-BN": 8}
    assert inquiry["contact_pref"] == "whatsapp" and inquiry["items"] == "Also 20 matching backsplash tiles"

    admin_mail = next(m for m in sent if m[0] == "sales@shop.test")
    assert inquiry["reference"] in admin_mail[1] and "QTZ-SLAB-CAL-3CM" in admin_mail[2] and "Prefers: WhatsApp" in admin_mail[2]
    assert any(m[0] == "maria@example.com" and inquiry["reference"] in m[1] for m in sent)  # customer copy

    page = client.get(resp.location).data.decode()
    assert inquiry["reference"] in page and "to be quoted" in page
    wa = next(part for part in page.split('"')
              if part.startswith("https://wa.me/") and inquiry["reference"] in urllib.parse.unquote(part))
    assert wa.startswith("https://wa.me/16265550199?text=")
    text = urllib.parse.unquote(wa.split("text=")[1].replace("&amp;", "&"))
    assert inquiry["reference"] in text and "× 12 slabs" in text and "$" not in text
    assert client.get(f"/quote/r/{inquiry['reference']}/wrong").status_code == 404


def test_admin_reply_draft_prefills_known_prices(admin, db):
    inquiry = db.execute("SELECT * FROM inquiries WHERE name = 'Demo Builder'").fetchone()
    html = admin.get(f"/admin/inquiries/{inquiry['id']}").data.decode()
    assert "TIL-POR-2448-CAL" in html
    assert "× 300 boxes @ $40.00 = $12,000.00" in html   # 120+ volume price
    assert "× 12 slabs @ $____" in html                   # quote-only: staff fill it in
    assert "https://wa.me/19095550101?text=" in html        # customer's number, US country code added
    admin.post(f"/admin/inquiries/{inquiry['id']}", data={"status": "quoted"})
    assert db.execute("SELECT status FROM inquiries WHERE id = ?", (inquiry["id"],)).fetchone()[0] == "quoted"


def test_admin_quote_only_product_needs_no_price(admin, db):
    admin.post("/admin/products/new", data={"sku": "Q-1", "name": "Custom Stair Treads", "quote_only": "1", "unit": "set"})
    p = product(db, "Q-1")
    assert p["quote_only"] == 1 and p["price_cents"] == 0
    resp = admin.post("/admin/products/new", data={"sku": "Q-2", "name": "No price"})
    assert "tick “Quote only”".encode() in resp.data and product(db, "Q-2") is None


def test_send_email_uses_smtp(app, monkeypatch):
    delivered = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            delivered.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context):
            delivered.append(("starttls",))

        def login(self, user, password):
            delivered.append(("login", user))

        def send_message(self, msg):
            delivered.append(("send", msg["To"], msg["Subject"], msg["Reply-To"]))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    app.config.update(SMTP_HOST="smtp.test", SMTP_USER="bot@shop.test", SMTP_PASSWORD="x")
    with app.test_request_context():
        assert notify.send_email("sales@shop.test", "Hello", "Body", reply_to="c@example.com")
        app.config["SMTP_HOST"] = ""
        assert not notify.send_email("sales@shop.test", "Hello", "Body")
    assert delivered == [("connect", "smtp.test", 587), ("starttls",), ("login", "bot@shop.test"),
                         ("send", "sales@shop.test", "Hello", "c@example.com")]


def test_whatsapp_and_mailto_links():
    assert notify.whatsapp_link("(626) 555-0199", "Hi there") == "https://wa.me/16265550199?text=Hi%20there"
    assert notify.whatsapp_link("+86 138 0013 8000") == "https://wa.me/8613800138000"
    assert notify.whatsapp_link("") is None
    assert notify.mailto_link("a@b.co", "Quote Q-1", "Line 1\nLine 2") == "mailto:a@b.co?subject=Quote%20Q-1&body=Line%201%0ALine%202"


def test_invoice_order_notifies_staff(app, client, db, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body, reply_to=None: sent.append((to, subject, body)))
    p = product(db, "HDW-BARN-6FT")
    client.post("/cart/add", data={"product_id": p["id"], "qty": 2})
    client.post("/checkout", data={"customer_name": "Pat", "email": "pat@example.com", "phone": "555",
                                   "fulfillment": "pickup", "payment_method": "invoice"})
    assert [to for to, _, _ in sent] == ["sales@example.com", "pat@example.com"]
    assert "New order" in sent[0][1] and "HDW-BARN-6FT" in sent[0][2]
    assert "confirmed" in sent[1][1] and "invoice within one business day" in sent[1][2]


def test_old_database_is_migrated(tmp_path):
    import sqlite3

    from shop import create_app

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE products (id INTEGER PRIMARY KEY, sku TEXT, slug TEXT, name TEXT, category_id INTEGER,
            supplier_id INTEGER, price_cents INTEGER NOT NULL DEFAULT 0, stock_qty INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE inquiries (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, email TEXT, status TEXT);
        INSERT INTO products (sku, slug, name, price_cents) VALUES ('OLD-1', 'old-1', 'Old product', 500);
    """)
    old.close()
    create_app({"TESTING": True, "SECRET_KEY": "t", "DATABASE": str(path), "UPLOAD_FOLDER": str(tmp_path / "u")})
    db = sqlite3.connect(path)
    assert "quote_only" in {r[1] for r in db.execute("PRAGMA table_info(products)")}
    assert {"reference", "access_token", "contact_pref"} <= {r[1] for r in db.execute("PRAGMA table_info(inquiries)")}
    assert db.execute("SELECT quote_only FROM products WHERE sku = 'OLD-1'").fetchone()[0] == 0


def test_empty_store_shows_getting_started(tmp_path):
    from shop import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "t", "DATABASE": str(tmp_path / "e.db"),
                      "UPLOAD_FOLDER": str(tmp_path / "u"), "ADMIN_PASSWORD": "pw"})
    client = Client(app.test_client())
    assert b"New stock is arriving soon" in client.get("/").data
    client.post("/admin/login", data={"password": "pw"})
    assert b"Getting started" in client.get("/admin/").data
