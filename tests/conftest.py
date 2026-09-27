import pytest

from shop import create_app
from shop.db import get_db
from shop.seed import seed_demo

CSRF = "test-csrf-token"


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
