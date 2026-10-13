"""Cloudflare Workers entry point: runs the whole shop on Cloudflare, no server needed.

* Every page request goes to one Durable Object ("Shop"), which runs the Flask app with the
  Durable Object's built-in SQLite database. One object = one shop, so stock and orders stay consistent.
* Product photos are stored in the R2 bucket bound as MEDIA, and served straight from R2 here.
* /static/ files (CSS) are served by Workers Static Assets before this code runs.

Deploy with `uv run pywrangler deploy` from this folder (see deploy/CLOUDFLARE-WORKERS.md).
Docker/gunicorn use ../wsgi.py instead.
"""
import os
from urllib.parse import unquote, urlsplit

import js
from workers import DurableObject, WorkerEntrypoint, wsgi

import shop  # noqa: F401  (imported at deploy time, so it's part of the start-up snapshot)
from shop import cloud

# Worker variables and secrets that configure the shop (same names as in .env for Docker).
SETTINGS = (
    "SECRET_KEY", "ADMIN_PASSWORD", "SITE_NAME", "SITE_TAGLINE", "CONTACT_EMAIL", "CONTACT_PHONE",
    "WAREHOUSE_ADDRESS", "DELIVERY_FEE", "ALLOW_INVOICE", "PAYMENT_INSTRUCTIONS", "STRIPE_SECRET_KEY",
    "WHATSAPP_NUMBER", "WECHAT_ID", "NOTIFY_EMAIL", "SMTP_FROM", "SELLER_SITE_NAME", "SELLER_SITE_DOMAIN",
    "MAX_UPLOAD_MB", "CLIENT_IP_HEADER",
)

_app = None


def get_app():
    global _app
    if _app is None:
        for name in SETTINGS:
            value = cloud.setting(name)
            if value:
                os.environ[name] = value
        from shop import create_app

        _app = create_app({"DATABASE": "/tmp/unused.db", "UPLOAD_FOLDER": "/tmp/uploads"})
    return _app


class Shop(DurableObject):
    """The shop itself, with its database. There is exactly one, named "shop"."""

    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        cloud.bind(env, cloud.raw(ctx).storage)

    async def fetch(self, request):
        if urlsplit(request.url).path == "/_diag/email" and cloud.setting("DEBUG_EMAIL"):
            return _email_diagnostics()
        response = await wsgi.fetch(get_app(), request, self.env)
        if cloud.setting("LOG_MEMORY"):  # diagnostics: how close the shop gets to the 128 MB limit
            import pyodide_js

            print(f"memory {pyodide_js._module.HEAP8.length / 1e6:.0f} MB after {request.method} {request.url}")
        return response


def _email_diagnostics():
    """Temporary (CI only, DEBUG_EMAIL set): report each step of a Cloudflare email send."""
    import json
    import traceback

    steps = []

    def step(name, fn):
        try:
            value = fn()
            steps.append(f"{name}: ok {getattr(value, 'typeof', type(value).__name__)} {str(value)[:120]}")
            return value
        except BaseException:
            steps.append(f"{name}: FAILED {traceback.format_exc()[-1500:]}")
            return None

    binding = step("binding", lambda: cloud.binding("EMAIL"))
    message = step("message", lambda: cloud.js_json({"to": "a@example.com", "from": "sales@liquidatorusa.com",
                                                       "subject": "diag", "text": "hello"}))
    step("send attribute", lambda: binding.send)
    result = step("call send", lambda: binding.send(message))
    step("wait", lambda: cloud.wait(result))
    return js.Response.new(json.dumps(steps, indent=1), cloud.js_object({"headers": {"Content-Type": "application/json"}}))


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        path = urlsplit(request.url).path
        if path.startswith("/media/"):
            return await self.photo(unquote(path[len("/media/"):]))
        namespace = self.env.SHOP
        # Keep the shop's database near its US customers, wherever the first visitor comes from.
        hint = getattr(self.env, "SHOP_LOCATION", None) or "wnam"
        stub = namespace.get(namespace.idFromName("shop"), {"locationHint": hint})
        return await stub.fetch(request)

    async def photo(self, name):
        bucket = cloud.raw(getattr(self.env, "MEDIA", None))
        found = await bucket.get(name) if bucket and name and "/" not in name else None
        if found is None:
            return js.Response.new("Not found", cloud.js_object({"status": 404}))
        content_type = getattr(found.httpMetadata, "contentType", None) or "image/jpeg"
        headers = {"Content-Type": content_type, "Cache-Control": "public, max-age=31536000, immutable",
                   "X-Content-Type-Options": "nosniff"}
        return js.Response.new(found.body, cloud.js_object({"headers": headers}))
