import os

import pytest

from shop import create_app
from shop.db import get_db
from shop.seed import seed_demo

CSRF = "test-csrf-token"
ON_FAKE_DURABLE_OBJECT = os.environ.get("SHOP_TEST_DB") == "durable-object"


@pytest.fixture(autouse=True)
def durable_object_storage(monkeypatch):
    """With SHOP_TEST_DB=durable-object, every test runs on the Cloudflare database adapter."""
    if not ON_FAKE_DURABLE_OBJECT:
        yield
        return
    from flask import current_app

    from shop import cloud
    from shop import db as shop_db

    from .fake_durable_object import FakeStorage

    storages = {}
    original_connect = shop_db.connect

    def connect():
        if cloud.ON_WORKERS:  # the simulated-Cloudflare tests bring their own storage
            return original_connect()
        path = current_app.config["DATABASE"]
        if path not in storages:
            storages[path] = FakeStorage(path)
        return shop_db.DurableObjectDB(storages[path])

    monkeypatch.setattr(shop_db, "connect", connect)
    monkeypatch.setattr(shop_db, "_js_null", lambda: None)
    monkeypatch.setattr(shop_db, "_js_callable", lambda fn: (fn, lambda: None))
    yield
    for storage in storages.values():
        storage.sql.conn.close()


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test",
            "DATABASE": str(tmp_path / "test.db"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "ADMIN_PASSWORD": "letmein",
            "STRIPE_SECRET_KEY": None,
            "DELIVERY_FEE_CENTS": 15000,
            "ALLOW_INVOICE": True,
        }
    )
    with app.app_context():
        seed_demo(get_db())
    return app


class Client:
    """Test client that includes the CSRF token on every POST."""

    def __init__(self, flask_client):
        self.c = flask_client
        with self.c.session_transaction() as s:
            s["_csrf"] = CSRF

    def get(self, *args, **kwargs):
        return self.c.get(*args, **kwargs)

    def post(self, url, data=None, **kwargs):
        return self.c.post(url, data={"_csrf": CSRF, **(data or {})}, **kwargs)

    def session(self):
        return self.c.session_transaction()


@pytest.fixture
def client(app):
    return Client(app.test_client())


@pytest.fixture
def admin(app):
    client = Client(app.test_client())
    resp = client.post("/admin/login", data={"password": "letmein"})
    assert resp.status_code == 302
    return client


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


def product(db, sku):
    return db.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()


def image_bytes(fmt="PNG", size=(40, 30), color=(181, 71, 27)):
    """A real little image, as an upload would contain."""
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt)
    return out.getvalue()
