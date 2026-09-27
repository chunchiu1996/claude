"""Minimal Stripe Checkout client (no SDK dependency). Enabled when STRIPE_SECRET_KEY is set."""
import json
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

API = "https://api.stripe.com/v1"


class PaymentError(Exception):
    pass


def enabled():
    return bool(current_app.config.get("STRIPE_SECRET_KEY"))


def _flatten(data, prefix=""):
    """Encode nested dicts/lists the way Stripe expects: line_items[0][price_data][currency]=usd."""
    pairs = []
    if isinstance(data, dict):
        for key, value in data.items():
            pairs.extend(_flatten(value, f"{prefix}[{key}]" if prefix else key))
    elif isinstance(data, (list, tuple)):
        for i, value in enumerate(data):
            pairs.extend(_flatten(value, f"{prefix}[{i}]"))
    elif data is not None:
        pairs.append((prefix, str(data)))
    return pairs


def _call(method, path, data=None):
    body = urllib.parse.urlencode(_flatten(data)).encode() if data else None
    req = urllib.request.Request(API + path, data=body, method=method)
    req.add_header("Authorization", "Bearer " + current_app.config["STRIPE_SECRET_KEY"])
    if body:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PaymentError(f"Stripe error {exc.code}: {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise PaymentError(f"Could not reach Stripe: {exc.reason}") from exc


def create_checkout_session(order, items, success_url, cancel_url):
    line_items = [
        {
            "quantity": item["qty"],
            "price_data": {
                "currency": "usd",
                "unit_amount": item["unit_price_cents"],
                "product_data": {"name": f"{item['name']} ({item['sku']})"},
            },
        }
        for item in items
    ]
    if order["shipping_cents"]:
        line_items.append(
            {
                "quantity": 1,
                "price_data": {
                    "currency": "usd",
                    "unit_amount": order["shipping_cents"],
                    "product_data": {"name": "Local delivery"},
                },
            }
        )
    session = _call(
        "POST",
        "/checkout/sessions",
        {
            "mode": "payment",
            "line_items": line_items,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": order["number"],
            "customer_email": order["email"],
            "metadata": {"order_number": order["number"]},
        },
    )
    return session["id"], session["url"]


def retrieve_checkout_session(session_id):
    return _call("GET", "/checkout/sessions/" + urllib.parse.quote(session_id, safe=""))
