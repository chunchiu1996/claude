"""Stripe client, failure paths, and admin features that the flow tests don't reach."""
import io
import json
import urllib.error
import urllib.parse

import pytest

from shop import orders, payments

from .conftest import image_bytes, product

PNG = image_bytes("PNG")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_stripe_session_request_is_encoded_correctly(app, db, monkeypatch):
    app.config["STRIPE_SECRET_KEY"] = "sk_test_abc"
    captured = {}

    def fake_urlopen(req, timeout):
        captured.update(url=req.full_url, auth=req.get_header("Authorization"),
                        body=urllib.parse.parse_qs(req.data.decode()))
        return FakeResponse(json.dumps({"id": "cs_1", "url": "https://checkout.stripe.test/cs_1"}).encode())

    monkeypatch.setattr(payments.urllib.request, "urlopen", fake_urlopen)
    order = db.execute("SELECT * FROM orders WHERE shipping_cents > 0").fetchone()
    with app.test_request_context():
        session_id, url = payments.create_checkout_session(
            order, orders.get_items(db, order["id"]), "https://shop.test/ok", "https://shop.test/cancel")
    assert (session_id, url) == ("cs_1", "https://checkout.stripe.test/cs_1")
    assert captured["url"] == "https://api.stripe.com/v1/checkout/sessions"
    assert captured["auth"] == "Bearer sk_test_abc"
    body = captured["body"]
    assert body["mode"] == ["payment"] and body["client_reference_id"] == [order["number"]]
    assert body["line_items[0][price_data][currency]"] == ["usd"]
    delivery = [k for k, v in body.items() if v == ["Local delivery"]]
    assert delivery, "delivery fee should be its own line item"


def test_stripe_errors_become_payment_errors(app, monkeypatch):
    app.config["STRIPE_SECRET_KEY"] = "sk_test_abc"

    def failing(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 402, "Payment Required", {}, io.BytesIO(b'{"error": "card"}'))

    monkeypatch.setattr(payments.urllib.request, "urlopen", failing)
    with app.test_request_context(), pytest.raises(payments.PaymentError, match="402"):
        payments.retrieve_checkout_session("cs_1")


def test_card_checkout_when_stripe_is_down_keeps_the_cart_and_stock(app, client, db, monkeypatch):
    app.config["STRIPE_SECRET_KEY"] = "sk_test_abc"

    def down(*args, **kwargs):
        raise payments.PaymentError("Could not reach Stripe")

    monkeypatch.setattr(payments, "create_checkout_session", down)
    p = product(db, "HDW-BARN-6FT")
    client.post("/cart/add", data={"product_id": p["id"], "qty": 2})
    resp = client.post("/checkout", data={"customer_name": "Pat", "email": "pat@example.com", "phone": "555",
                                          "fulfillment": "pickup", "payment_method": "card"})
    assert resp.status_code == 302 and resp.location.endswith("/checkout")
    assert product(db, "HDW-BARN-6FT")["stock_qty"] == p["stock_qty"]  # reservation released
    with client.session() as s:
        assert s["cart"] == {str(p["id"]): 2}  # cart restored
    assert db.execute("SELECT status FROM orders ORDER BY id DESC LIMIT 1").fetchone()[0] == "cancelled"


def test_cart_quantities_can_be_changed_and_removed(client, db):
    a, b = product(db, "HDW-LEV-PASS-MB"), product(db, "FAU-BTH-WS-MB")
    client.post("/cart/add", data={"product_id": a["id"], "qty": 3})
    client.post("/cart/add", data={"product_id": b["id"], "qty": 1})
    client.post("/cart/update", data={f"qty_{a['id']}": "7", f"qty_{b['id']}": "1"})
    with client.session() as s:
        assert s["cart"] == {str(a["id"]): 7, str(b["id"]): 1}
    client.post("/cart/update", data={"remove": str(b["id"])})
    client.post("/cart/update", data={f"qty_{a['id']}": "0"})
    with client.session() as s:
        assert s["cart"] == {}


def test_product_photo_upload_checks_the_file(admin, db, app):
    p = product(db, "HDW-BARN-6FT")
    form = {"sku": p["sku"], "name": p["name"], "price": "79.00", "unit": "set", "moq": "1", "active": "1"}
    resp = admin.post(f"/admin/products/{p['id']}", data={**form, "image": (io.BytesIO(b"<script>x</script>"), "evil.png")},
                      content_type="multipart/form-data")
    assert b"Not an image we can read" in resp.data and not product(db, p["sku"])["image_url"]

    admin.post(f"/admin/products/{p['id']}", data={**form, "image": (io.BytesIO(PNG), "barn.png")},
               content_type="multipart/form-data")
    url = product(db, p["sku"])["image_url"]
    assert url and url.startswith("/media/") and url.endswith(".jpg")  # re-encoded: nothing but pixels survives
    media = admin.get(url)
    assert media.status_code == 200 and media.data[:3] == b"\xff\xd8\xff"
    assert media.headers["X-Content-Type-Options"] == "nosniff"


def test_category_can_be_renamed(admin, db):
    cat = db.execute("SELECT * FROM categories WHERE name = 'Lighting'").fetchone()
    admin.post("/admin/categories", data={"id": cat["id"], "name": "Lighting & Fans", "sort_order": "3"})
    assert db.execute("SELECT name FROM categories WHERE id = ?", (cat["id"],)).fetchone()[0] == "Lighting & Fans"
    resp = admin.post("/admin/categories", data={"name": "tile"}, follow_redirects=True)
    assert b"already exists" in resp.data


def test_demo_data_is_never_loaded_twice(app):
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code != 0 and "refusing" in result.output


def test_inquiry_status_can_be_updated_from_the_list(admin, db):
    inquiry = db.execute("SELECT id FROM inquiries LIMIT 1").fetchone()
    admin.post("/admin/inquiries", data={"id": inquiry["id"], "status": "contacted"})
    admin.post("/admin/inquiries", data={"id": inquiry["id"], "status": "bogus"})
    assert db.execute("SELECT status FROM inquiries WHERE id = ?", (inquiry["id"],)).fetchone()[0] == "contacted"


def test_security_headers_and_friendly_csrf_error(app):
    raw = app.test_client()
    home = raw.get("/")
    assert home.headers["X-Frame-Options"] == "SAMEORIGIN" and home.headers["X-Content-Type-Options"] == "nosniff"
    resp = raw.post("/quote", data={"name": "x"})
    assert resp.status_code == 400 and b"session expired" in resp.data
