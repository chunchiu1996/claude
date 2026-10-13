"""The Cloudflare Workers version: database adapter, photos in R2, Cloudflare Email Service, Stripe via fetch.

Real Cloudflare objects are JavaScript; here they're imitated with small Python fakes so the
Workers code paths run under plain pytest.
"""
import io
import types

import pytest

from shop import cloud, create_app, orders
from shop import db as shop_db

from .conftest import CSRF, image_bytes
from .fake_durable_object import FakeStorage


@pytest.fixture
def do_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shop_db, "_js_null", lambda: None)
    monkeypatch.setattr(shop_db, "_js_callable", lambda fn: (fn, lambda: None))
    storage = FakeStorage(str(tmp_path / "do.db"))
    yield shop_db.DurableObjectDB(storage)
    storage.sql.conn.close()


def test_adapter_behaves_like_sqlite3(do_db):
    do_db.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, note TEXT); CREATE TABLE u (x INTEGER);")
    cur = do_db.execute("INSERT INTO t (name, note) VALUES (?, ?)", ("oak", None))
    assert cur.lastrowid == 1 and cur.rowcount == 1
    do_db.executemany("INSERT INTO t (name) VALUES (?)", [("ash",), ("elm",)])
    row = do_db.execute("SELECT id, name, note FROM t WHERE name = ?", ("oak",)).fetchone()
    assert row["name"] == "oak" and row[0] == 1 and row["note"] is None
    assert dict(row) == {"id": 1, "name": "oak", "note": None} and row.keys() == ["id", "name", "note"]
    assert [r["name"] for r in do_db.execute("SELECT name FROM t ORDER BY id")] == ["oak", "ash", "elm"]
    assert do_db.execute("UPDATE t SET note = 'x' WHERE id > 1").rowcount == 2
    assert do_db.execute("SELECT * FROM t WHERE id = 99").fetchone() is None
    assert do_db.execute("PRAGMA foreign_keys = ON").fetchall() == []  # managed by Cloudflare
    assert {r["name"] for r in do_db.execute("PRAGMA table_info(t)")} == {"id", "name", "note"}


def test_adapter_transactions_roll_back_and_keep_the_error(do_db):
    do_db.execute("CREATE TABLE stock (qty INTEGER)")
    do_db.execute("INSERT INTO stock VALUES (5)")

    class OutOfStock(Exception):
        pass

    def sell():
        do_db.execute("UPDATE stock SET qty = qty - 3")
        raise OutOfStock("no more")

    with pytest.raises(OutOfStock):
        shop_db.atomic(do_db, sell)
    assert do_db.execute("SELECT qty FROM stock").fetchone()[0] == 5  # nothing half-saved
    assert shop_db.atomic(do_db, lambda: do_db.execute("UPDATE stock SET qty = 4").rowcount) == 1
    assert do_db.execute("SELECT qty FROM stock").fetchone()[0] == 4


def test_order_that_runs_out_of_stock_changes_nothing(app, db):
    """Placing an order is all-or-nothing on both databases (normal and Cloudflare)."""
    a = db.execute("SELECT * FROM products WHERE stock_qty > 5 ORDER BY id LIMIT 1").fetchone()
    b = db.execute("SELECT * FROM products WHERE stock_qty > 5 AND id != ? ORDER BY id LIMIT 1", (a["id"],)).fetchone()
    lines = [{"product": a, "qty": 2, "unit_price_cents": 100, "line_total_cents": 200},
             {"product": b, "qty": b["stock_qty"] + 1, "unit_price_cents": 100, "line_total_cents": 100}]
    customer = {"customer_name": "X", "email": "x@example.com", "fulfillment": "pickup"}
    with pytest.raises(orders.StockError):
        orders.place_order(db, customer, lines, 0, "invoice")
    assert db.execute("SELECT stock_qty FROM products WHERE id = ?", (a["id"],)).fetchone()[0] == a["stock_qty"]
    assert db.execute("SELECT COUNT(*) FROM orders WHERE email = 'x@example.com'").fetchone()[0] == 0


# ---------------------------------------------------------------- the app as it runs on Cloudflare


class FakeBucket:
    """R2 bucket."""

    def __init__(self):
        self.files = {}

    def put(self, name, data, options):
        self.files[name] = (bytes(data), options["httpMetadata"]["contentType"])

    def _object(self, name):
        if name not in self.files:
            return None
        raw, content_type = self.files[name]
        return types.SimpleNamespace(httpMetadata=types.SimpleNamespace(contentType=content_type),
                                     arrayBuffer=lambda: raw)

    get = head = _object

    def delete(self, name):
        self.files.pop(name, None)


class FakeEmail:
    """Cloudflare Email Service binding."""

    def __init__(self):
        self.sent, self.error = [], None

    def send(self, message):
        if self.error:
            raise RuntimeError(self.error)
        self.sent.append(message)


@pytest.fixture
def workers(tmp_path, monkeypatch):
    """The shop configured the way worker.py starts it on Cloudflare."""
    storage = FakeStorage(str(tmp_path / "do.db"))
    bucket, email = FakeBucket(), FakeEmail()
    env = types.SimpleNamespace(MEDIA=bucket, EMAIL=email, ADMIN_PASSWORD="pw", SITE_NAME="Liquidator USA",
                                SMTP_FROM="sales@liquidatorusa.com")
    monkeypatch.setattr(cloud, "ON_WORKERS", True)
    for name, fake in {"wait": lambda value: value, "js_object": lambda value: value, "js_json": lambda value: value,
                       "js_bytes": bytes, "py_bytes": bytes,
                       "call_binding": lambda binding, method, *args: getattr(binding, method)(*args)}.items():
        monkeypatch.setattr(cloud, name, fake)
    monkeypatch.setattr(shop_db, "_js_null", lambda: None)
    monkeypatch.setattr(shop_db, "_js_callable", lambda fn: (fn, lambda: None))
    for name in ("SECRET_KEY", "SMTP_HOST", "NOTIFY_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {"ADMIN_PASSWORD": "pw", "SMTP_FROM": "sales@liquidatorusa.com",
                        "CONTACT_EMAIL": "sales@liquidatorusa.com"}.items():  # as worker.py copies them
        monkeypatch.setenv(name, value)
    cloud.bind(env, storage)
    app = create_app({"TESTING": True, "DATABASE": "/nonexistent/unused.db", "UPLOAD_FOLDER": "/nonexistent/uploads"})
    client = app.test_client()
    base = "https://shop.liquidatorusa.com"
    with client.session_transaction(base_url=base) as sess:
        sess["_csrf"] = CSRF
    yield types.SimpleNamespace(app=app, client=client, base=base, bucket=bucket, email=email, storage=storage)
    cloud.bind(None, None)
    storage.sql.conn.close()


def _post(w, url, data=None, **kwargs):
    return w.client.post(url, data={"_csrf": CSRF, **(data or {})}, base_url=w.base, **kwargs)


def test_workers_settings_and_session_key(workers):
    cfg = workers.app.config
    assert cfg["SESSION_COOKIE_SECURE"] and cfg["CLIENT_IP_HEADER"] == "CF-Connecting-IP"
    assert cfg["MAX_CONTENT_LENGTH"] == 25 * 1024 * 1024  # each request must fit in 128 MB of memory
    stored = workers.storage.sql.exec("SELECT value FROM app_settings WHERE key = 'secret_key'").raw().toArray()
    assert cfg["SECRET_KEY"] == stored[0][0] and len(cfg["SECRET_KEY"]) == 64  # survives restarts: it's in the DB


def test_workers_photos_go_to_r2_and_email_through_cloudflare(workers):
    w = workers
    assert _post(w, "/admin/login", {"password": "pw"}).status_code == 302
    _post(w, "/admin/products/new", {"sku": "CF-1", "name": "Cloud Tile", "price": "10", "active": "1"})
    results = _post(w, "/admin/photos/upload", {"format": "json",
                    "files": [(io.BytesIO(image_bytes("JPEG", (2400, 1800))), "CF-1.jpg")]},
                    content_type="multipart/form-data", headers={"Accept": "application/json"}).get_json()["results"]
    assert results[0]["status"] == "ok"
    names = sorted(w.bucket.files)
    assert len(names) == 2 and sum(n.endswith("-sm.jpg") for n in names) == 1
    main = next(n for n in names if not n.endswith("-sm.jpg"))
    page = w.client.get("/shop", base_url=w.base).data.decode()
    assert main[:-4] + "-sm.jpg" in page  # cards use the small photo, without asking R2 if it exists
    served = w.client.get(f"/media/{main}", base_url=w.base)
    assert served.status_code == 200 and served.data[:3] == b"\xff\xd8\xff"
    assert "immutable" in served.headers["Cache-Control"]
    assert w.client.get("/media/nope.jpg", base_url=w.base).status_code == 404

    html = w.client.get("/admin/email", base_url=w.base).data.decode()
    assert "Cloudflare Email Service" in html and "SMTP_HOST" not in html
    resp = _post(w, "/admin/email", {"to": "owner@example.com"}, follow_redirects=True)
    assert b"Test email sent" in resp.data
    message = w.email.sent[-1]
    assert message["to"] == "owner@example.com" and message["from"] == {
        "email": "sales@liquidatorusa.com", "name": "Liquidator USA"}
    w.email.error = "E_SENDER_NOT_VERIFIED: sender not verified"
    resp = _post(w, "/admin/email", {"to": "owner@example.com"}, follow_redirects=True)
    assert b"Email Sending" in resp.data  # tells you where to fix it in Cloudflare


def test_workers_checkout_emails_and_invoice(workers, monkeypatch):
    w = workers
    _post(w, "/admin/login", {"password": "pw"})
    _post(w, "/admin/products/new", {"sku": "CF-2", "name": "Cloud Vanity", "price": "100", "active": "1",
                                     "initial_stock": "5"})
    product_id = w.storage.sql.exec("SELECT id FROM products WHERE sku = 'CF-2'").raw().toArray()[0][0]
    _post(w, "/cart/add", {"product_id": product_id, "qty": 2})
    _post(w, "/checkout", {"customer_name": "Kim", "email": "kim@example.com", "phone": "555",
                           "fulfillment": "pickup", "payment_method": "invoice"})
    assert [m["to"] for m in w.email.sent] == ["sales@liquidatorusa.com", "kim@example.com"]
    order_id = w.storage.sql.exec("SELECT id FROM orders").raw().toArray()[0][0]
    resp = _post(w, f"/admin/orders/{order_id}/email-invoice", follow_redirects=True)
    assert b"Invoice emailed to kim@example.com" in resp.data
    assert "INVOICE" in w.email.sent[-1]["html"] and w.email.sent[-1]["replyTo"] == "sales@liquidatorusa.com"


def test_workers_stripe_uses_fetch(workers, monkeypatch):
    from shop import payments

    calls = []

    def fake_request(method, url, headers=None, body=None):
        calls.append((method, url, headers, body))
        return 200, b'{"id": "cs_9", "url": "https://checkout.stripe.test/cs_9"}'

    monkeypatch.setattr(cloud, "http_request", fake_request)
    workers.app.config["STRIPE_SECRET_KEY"] = "sk_test_cf"
    with workers.app.test_request_context():
        assert payments.retrieve_checkout_session("cs_9")["id"] == "cs_9"
        monkeypatch.setattr(cloud, "http_request", lambda *a, **k: (402, b'{"error": "card declined"}'))
        with pytest.raises(payments.PaymentError, match="402"):
            payments.retrieve_checkout_session("cs_9")
    assert calls[0][:2] == ("GET", "https://api.stripe.com/v1/checkout/sessions/cs_9")
    assert calls[0][2]["Authorization"] == "Bearer sk_test_cf"
