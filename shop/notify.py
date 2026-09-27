"""Email notifications (SMTP, optional) and WhatsApp / mailto link helpers."""
import re
import smtplib
import ssl
import urllib.parse
from email.message import EmailMessage

from flask import current_app, url_for

from .inquiries import CONTACT_PREFS, item_line
from .pricing import fmt_money


def site_info():
    cfg = current_app.config
    return {
        "name": cfg["SITE_NAME"],
        "tagline": cfg["SITE_TAGLINE"],
        "email": cfg["CONTACT_EMAIL"],
        "phone": cfg["CONTACT_PHONE"],
        "warehouse": cfg["WAREHOUSE_ADDRESS"],
        "whatsapp": cfg.get("WHATSAPP_NUMBER") or "",
        "wechat": cfg.get("WECHAT_ID") or "",
    }


def whatsapp_link(number, text=""):
    """https://wa.me link that opens a chat with `number`, pre-filled with `text`. None if no usable number."""
    digits = re.sub(r"\D", "", number or "")
    if len(digits) == 10:  # US number typed without the country code
        digits = "1" + digits
    if len(digits) < 8:
        return None
    return f"https://wa.me/{digits}" + (f"?text={urllib.parse.quote(text)}" if text else "")


def mailto_link(address, subject, body):
    if not address:
        return None
    query = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    return f"mailto:{address}?{query}"


def send_email(to, subject, body, reply_to=None):
    """Send a plain-text email. Returns False (and logs) if SMTP isn't configured or sending fails."""
    cfg = current_app.config
    if not cfg.get("SMTP_HOST") or not to:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("SMTP_FROM") or cfg.get("SMTP_USER") or cfg["CONTACT_EMAIL"]
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    port = int(cfg.get("SMTP_PORT") or 587)
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=15)
        with server:
            if port != 465 and cfg.get("SMTP_STARTTLS", True):
                server.starttls(context=ssl.create_default_context())
            if cfg.get("SMTP_USER"):
                server.login(cfg["SMTP_USER"], cfg.get("SMTP_PASSWORD") or "")
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        current_app.logger.exception("Could not send email %r to %s", subject, to)
        return False


def _admin_address():
    return current_app.config.get("NOTIFY_EMAIL") or current_app.config["CONTACT_EMAIL"]


def new_inquiry(inquiry, items):
    """Tell staff about a new quote request / factory application, and send the customer a copy."""
    site = site_info()
    kind = "Quote request" if inquiry["kind"] == "quote" else "Factory application"
    admin_url = url_for("admin.inquiry_detail", inquiry_id=inquiry["id"], _external=True)
    contact = [
        f"Name: {inquiry['name']}" + (f" — {inquiry['company']}" if inquiry["company"] else ""),
        f"Email: {inquiry['email'] or '—'}",
        f"Phone / WhatsApp / WeChat: {inquiry['phone'] or '—'}",
        f"Prefers: {CONTACT_PREFS.get(inquiry['contact_pref'], 'Email')}",
    ]
    if inquiry["role"]:
        contact.append(f"Type: {inquiry['role']}")
    if inquiry["location"]:
        contact.append(f"Location/ZIP: {inquiry['location']}")
    body = [f"{kind} {inquiry['reference']}", "", *contact, ""]
    if items:
        body += ["Products:", *[item_line(i) for i in items], ""]
    if inquiry["items"]:
        body += ["Other items / products:" if inquiry["kind"] == "quote" else "Products & quantities:", inquiry["items"], ""]
    if inquiry["message"]:
        body += ["Message:", inquiry["message"], ""]
    body.append(f"Open and reply: {admin_url}")
    send_email(_admin_address(), f"{kind} {inquiry['reference']} from {inquiry['company'] or inquiry['name']}",
               "\n".join(body), reply_to=inquiry["email"] or None)

    if inquiry["kind"] == "quote" and inquiry["email"]:
        public_url = url_for("store.quote_request", reference=inquiry["reference"], token=inquiry["access_token"],
                             _external=True)
        copy = [f"Hi {inquiry['name']},", "", f"Thanks for your quote request {inquiry['reference']}. We received:", ""]
        copy += [item_line(i) for i in items]
        if inquiry["items"]:
            copy += ["", inquiry["items"]]
        copy += ["", f"We'll reply by {CONTACT_PREFS.get(inquiry['contact_pref'], 'Email')} "
                     "with pricing and availability within one business day.",
                 f"View your request: {public_url}", "", site["name"]]
        if site["phone"]:
            copy.append(site["phone"])
        send_email(inquiry["email"], f"We received your quote request {inquiry['reference']}", "\n".join(copy),
                   reply_to=_admin_address())


def new_order(order, items):
    lines = [
        f"New order {order['number']} — {fmt_money(order['total_cents'])}",
        f"Payment: {'card (paid)' if order['status'] == 'paid' else 'invoice — send an invoice'}",
        f"{'Delivery' if order['fulfillment'] == 'delivery' else 'Warehouse pickup'}",
        "",
        f"Customer: {order['customer_name']}" + (f" — {order['company']}" if order["company"] else ""),
        f"Email: {order['email']}  Phone: {order['phone'] or '—'}",
        "",
        *[f"• {i['sku']} – {i['name']} × {i['qty']} @ {fmt_money(i['unit_price_cents'])}" for i in items],
        "",
        f"Open: {url_for('admin.order_detail', order_id=order['id'], _external=True)}",
    ]
    send_email(_admin_address(), f"New order {order['number']} from {order['customer_name']}", "\n".join(lines),
               reply_to=order["email"])
