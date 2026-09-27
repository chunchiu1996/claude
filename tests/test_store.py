from shop import orders, payments

from .conftest import product


def test_catalog_pages(client):
    home = client.get("/")
    assert home.status_code == 200
    assert b"Shaker White Base Cabinet" in home.data
    assert b"(Demo)" not in home.data  # factory names are never shown to shoppers

    flooring = client.get("/shop/flooring").data
    assert b"Natural Oak" in flooring and b"Pull-Down Kitchen Faucet" not in flooring

    search = client.get("/shop?q=faucet").data
    assert b"Pull-Down Kitchen Faucet" in search and b"Vinyl Plank" not in search

    assert client.get("/shop/not-a-category").status_code == 404


def test_product_page_shows_volume_pricing(client, db):
    p = product(db, "SPC-7MM-OAK")
    html = client.get(f"/p/{p['slug']}").data.decode()
    assert "$42.50" in html and "$38.99" in html and "$35.45" in html
    assert "23.64 sq ft per box" in html


def test_add_to_cart_enforces_moq_and_stock(client, db):
    mosaic = product(db, "TIL-MOS-HEX-WHT")  # moq 10
    client.post("/cart/add", data={"product_id": mosaic["id"], "qty": 1})
    with client.session() as s:
        assert s["cart"][str(mosaic["id"])] == 10

    vanity = product(db, "VAN-48-WHT-QTZ")
    client.post("/cart/add", data={"product_id": vanity["id"], "qty": 9999})
    with client.session() as s:
        assert s["cart"][str(vanity["id"])] == vanity["stock_qty"]


def test_cart_applies_tier_price(client, db):
    p = product(db, "SPC-7MM-OAK")
    client.post("/cart/add", data={"product_id": p["id"], "qty": 25})
    html = client.get("/cart").data.decode()
    assert "$38.99" in html and "$974.75" in html  # 25 x 38.99
    assert "Buy 100+" in html


def _checkout(client, **overrides):
    data = {
        "customer_name": "Pat Buyer",
        "email": "pat@example.com",
        "phone": "555-1234",
        "fulfillment": "pickup",
        "payment_method": "invoice",
    }
    data.update(overrides)
    return client.post("/checkout", data=data)


def test_invoice_checkout_reserves_stock(client, db):
    p = product(db, "FAU-KIT-PD-BN")
    client.post("/cart/add", data={"product_id": p["id"], "qty": 10})
    resp = _checkout(client)
    assert resp.status_code == 302 and "/order/" in resp.location

    order = db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 1").fetchone()
    assert order["status"] == "pending_payment"
    assert order["total_cents"] == 10 * 6100
    assert product(db, "FAU-KIT-PD-BN")["stock_qty"] == p["stock_qty"] - 10
    move = db.execute("SELECT * FROM stock_movements WHERE product_id = ? ORDER BY id DESC", (p["id"],)).fetchone()
    assert (move["kind"], move["qty_change"]) == ("sale", -10)
    with client.session() as s:
        assert "cart" not in s

    page = client.get(resp.location)
    assert page.status_code == 200 and order["number"].encode() in page.data
    assert client.get(f"/order/{order['number']}/wrong-token").status_code == 404


def test_checkout_validation_and_delivery_fee(client, db):
    p = product(db, "HDW-BARN-6FT")
    client.post("/cart/add", data={"product_id": p["id"], "qty": 1})
    resp = _checkout(client, fulfillment="delivery", email="nope")
    assert resp.status_code == 200
    assert b"valid email" in resp.data and b"Required for delivery" in resp.data

    resp = _checkout(client, fulfillment="delivery", address="1 Main", city="Riverside", state="CA", zip="92501")
    assert resp.status_code == 302
    order = db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 1").fetchone()
    assert order["shipping_cents"] == 15000 and order["total_cents"] == 7900 + 15000


def test_checkout_rejects_when_stock_sold_out_meanwhile(client, db):
    p = product(db, "VAN-48-WHT-QTZ")
    client.post("/cart/add", data={"product_id": p["id"], "qty": 5})
    db.execute("UPDATE products SET stock_qty = 2 WHERE id = ?", (p["id"],))
    db.commit()
    resp = _checkout(client)
    assert resp.status_code == 302 and resp.location.endswith("/cart")
    assert db.execute("SELECT COUNT(*) FROM orders WHERE customer_name = 'Pat Buyer'").fetchone()[0] == 0


def test_post_without_csrf_token_is_rejected(app):
    raw = app.test_client()
    assert raw.post("/cart/add", data={"product_id": 1}).status_code == 400


def test_card_checkout_with_stripe(app, client, db, monkeypatch):
    app.config["STRIPE_SECRET_KEY"] = "sk_test_x"
    created = {}

    def fake_create(order, items, success_url, cancel_url):
        created.update(order=order["number"], success=success_url, cancel=cancel_url,
                       amount=sum(i["line_total_cents"] for i in items))
        return "cs_test_123", "https://checkout.stripe.test/pay"

    monkeypatch.setattr(payments, "create_checkout_session", fake_create)
    monkeypatch.setattr(payments, "retrieve_checkout_session",
                        lambda sid: {"payment_status": "paid", "client_reference_id": created["order"]})

    p = product(db, "LGT-CAN-6-5CCT")
    client.post("/cart/add", data={"product_id": p["id"], "qty": 10})
    resp = _checkout(client, payment_method="card")
    assert resp.status_code == 303 and resp.location == "https://checkout.stripe.test/pay"
    assert created["amount"] == 10 * 3599

    order = db.execute("SELECT * FROM orders WHERE number = ?", (created["order"],)).fetchone()
    assert order["stripe_session_id"] == "cs_test_123" and order["status"] == "pending_payment"

    client.get(created["success"])  # Stripe redirects back after payment
    assert orders.get_order(db, order["id"])["status"] == "paid"


def test_cancelled_card_payment_restocks_and_restores_cart(app, client, db, monkeypatch):
    app.config["STRIPE_SECRET_KEY"] = "sk_test_x"
    urls = {}

    def fake_create(order, items, success_url, cancel_url):
        urls["cancel"] = cancel_url
        return "cs_test_456", "https://checkout.stripe.test/pay"

    monkeypatch.setattr(payments, "create_checkout_session", fake_create)
    p = product(db, "SHW-SYS-12-BN")
    client.post("/cart/add", data={"product_id": p["id"], "qty": 3})
    _checkout(client, payment_method="card")
    assert product(db, "SHW-SYS-12-BN")["stock_qty"] == p["stock_qty"] - 3

    resp = client.get(urls["cancel"])
    assert resp.location.endswith("/cart")
    assert product(db, "SHW-SYS-12-BN")["stock_qty"] == p["stock_qty"]
    with client.session() as s:
        assert s["cart"] == {str(p["id"]): 3}


def test_stale_card_orders_release_stock(db):
    p = product(db, "HDW-LEV-PASS-MB")
    line = {"product": p, "qty": 5, "unit_price_cents": 1250, "line_total_cents": 6250}
    order = orders.place_order(db, {"customer_name": "X", "email": "x@example.com", "fulfillment": "pickup"},
                               [line], 0, "card")
    db.execute("UPDATE orders SET created_at = datetime('now', '-2 days') WHERE id = ?", (order["id"],))
    db.commit()
    assert orders.release_stale_card_orders(db) == 1
    assert orders.get_order(db, order["id"])["status"] == "cancelled"
    assert product(db, "HDW-LEV-PASS-MB")["stock_qty"] == p["stock_qty"]


def test_supplier_application_form(client, db):
    assert client.post("/sell-with-us", data={"name": "Li"}).status_code == 200
    resp = client.post("/sell-with-us", data={"name": "Li", "email": "li@example.com", "company": "Foshan Co",
                                              "items": "Tile, 2 containers"})
    assert "谢谢".encode() in resp.data
    row = db.execute("SELECT * FROM inquiries WHERE name = 'Li'").fetchone()
    assert row["kind"] == "supplier" and row["reference"].startswith("F-")


def test_feeds(client):
    feed = client.get("/feed/products.xml")
    assert feed.status_code == 200 and b"<g:id>SPC-7MM-OAK</g:id>" in feed.data
    assert b"<g:price>42.50 USD</g:price>" in feed.data
    assert b"QTZ-SLAB-CAL-3CM" not in feed.data  # quote-only products have no price for Google
    assert b"/p/" in client.get("/sitemap.xml").data
    assert b"Disallow: /admin" in client.get("/robots.txt").data
