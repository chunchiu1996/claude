import os
import secrets
from pathlib import Path

from flask import Flask, abort, request, session

from . import db
from .pricing import fmt_money, parse_money, pluralize


def _load_or_create_secret(data_dir):
    """Keep sessions valid across restarts/workers when SECRET_KEY isn't set: stored beside the database."""
    path = Path(data_dir) / "secret_key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    key = secrets.token_hex(32)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return key


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    env = os.environ.get
    app.config.from_mapping(
        SECRET_KEY=env("SECRET_KEY"),
        DATABASE=env("DATABASE", os.path.join(app.instance_path, "shop.db")),
        UPLOAD_FOLDER=env("UPLOAD_FOLDER", os.path.join(app.instance_path, "uploads")),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        SITE_NAME=env("SITE_NAME", "Liqueator USA"),
        SITE_TAGLINE=env(
            "SITE_TAGLINE", "Factory-direct flooring, tile, cabinets & fixtures — in stock at our US warehouse."
        ),
        CONTACT_EMAIL=env("CONTACT_EMAIL", "sales@example.com"),
        CONTACT_PHONE=env("CONTACT_PHONE", ""),
        WAREHOUSE_ADDRESS=env("WAREHOUSE_ADDRESS", "Ontario, CA 91761"),
        ADMIN_PASSWORD=env("ADMIN_PASSWORD"),
        STRIPE_SECRET_KEY=env("STRIPE_SECRET_KEY"),
        DELIVERY_FEE_CENTS=parse_money(env("DELIVERY_FEE", "150")),
        ALLOW_INVOICE=env("ALLOW_INVOICE", "1") != "0",
        # Where quote requests and orders are sent
        WHATSAPP_NUMBER=env("WHATSAPP_NUMBER", ""),
        WECHAT_ID=env("WECHAT_ID", ""),
        NOTIFY_EMAIL=env("NOTIFY_EMAIL", ""),
        SMTP_HOST=env("SMTP_HOST", ""),
        SMTP_PORT=int(env("SMTP_PORT", "587")),
        SMTP_USER=env("SMTP_USER", ""),
        SMTP_PASSWORD=env("SMTP_PASSWORD", ""),
        SMTP_FROM=env("SMTP_FROM", ""),
        SMTP_STARTTLS=env("SMTP_STARTTLS", "1") != "0",
        # Chinese seller-recruitment site (/zh/), used as the landing page for email campaigns to
        # Chinese factories and stock owners. Optionally served on its own domain.
        SELLER_SITE_NAME=env("SELLER_SITE_NAME", ""),
        SELLER_SITE_DOMAIN=env("SELLER_SITE_DOMAIN", ""),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=env("SESSION_COOKIE_SECURE", "0") == "1",
    )
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"]:
        data_dir = Path(app.config["DATABASE"]).parent
        data_dir.mkdir(parents=True, exist_ok=True)
        app.config["SECRET_KEY"] = _load_or_create_secret(data_dir)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    if app.config["SELLER_SITE_DOMAIN"]:
        from .seller import SellerDomainMiddleware

        app.wsgi_app = SellerDomainMiddleware(app.wsgi_app, app.config["SELLER_SITE_DOMAIN"])
    if env("TRUST_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    with app.app_context():
        db.init_db()

    _install_csrf(app)
    _install_template_helpers(app)

    from . import admin, seller, storefront

    app.register_blueprint(storefront.bp)   # English store for US customers, at /
    app.register_blueprint(seller.bp)       # Chinese site for factories / stock owners, at /zh/
    app.register_blueprint(admin.bp)
    return app


def _csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def _install_csrf(app):
    @app.before_request
    def check_csrf():
        if request.method == "POST":
            token = request.form.get("_csrf", "")
            if not token or not secrets.compare_digest(token, session.get("_csrf", "")):
                abort(400, "Your session expired. Go back, refresh the page and try again.")


def _install_template_helpers(app):
    app.jinja_env.filters["money"] = fmt_money
    app.jinja_env.filters["plural"] = pluralize
    app.jinja_env.globals["csrf_token"] = _csrf_token

    @app.template_filter("pct")
    def pct(rate):
        return f"{rate * 100:g}%"

    from .notify import site_info, whatsapp_link

    app.jinja_env.globals["whatsapp_link"] = whatsapp_link

    @app.context_processor
    def inject_globals():
        from .catalog import categories_with_counts
        from .db import get_db
        from .payments import enabled as stripe_enabled

        return {
            "site": site_info(),
            "nav_categories": [c for c in categories_with_counts(get_db()) if c["product_count"]],
            "cart_count": len(session.get("cart") or {}),
            "quote_count": len(session.get("quote") or {}),
            "stripe_enabled": stripe_enabled(),
        }
