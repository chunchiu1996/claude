"""Email notifications (SMTP, optional) and WhatsApp / mailto link helpers."""
import re
import smtplib
import ssl
import threading
import urllib.parse
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from flask import current_app, render_template, url_for

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


def email_enabled():
    return bool(current_app.config.get("SMTP_HOST"))


def from_address():
    cfg = current_app.config
    return cfg.get("SMTP_FROM") or cfg.get("SMTP_USER") or cfg["CONTACT_EMAIL"]


def admin_address():
    return current_app.config.get("NOTIFY_EMAIL") or current_app.config["CONTACT_EMAIL"]


def _message(to, subject, body, reply_to=None, html=None):
    msg = EmailMessage()
    msg["Subject"] = subject
    sender = from_address()
    msg["From"] = formataddr((current_app.config["SITE_NAME"], sender))
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def deliver(to, subject, body, reply_to=None, html=None):
    """Send now. Returns None when sent, or a short explanation of what went wrong."""
    cfg = current_app.config
    if not cfg.get("SMTP_HOST"):
        return "Email is not set up yet (SMTP_HOST is empty). See the Email page in admin."
    if not to:
        return "No email address to send to."
    host, port = cfg["SMTP_HOST"], int(cfg.get("SMTP_PORT") or 587)
    msg = _message(to, subject, body, reply_to, html)
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if port != 465 and cfg.get("SMTP_STARTTLS", True):
                server.starttls(context=ssl.create_default_context())
            if cfg.get("SMTP_USER"):
                server.login(cfg["SMTP_USER"], cfg.get("SMTP_PASSWORD") or "")
            server.send_message(msg)
        return None
    except smtplib.SMTPAuthenticationError:
        error = "The email server rejected the login. Check SMTP_USER and SMTP_PASSWORD."
    except smtplib.SMTPSenderRefused:
        error = f"The email server won't send from {from_address()}. Use an address your provider has verified (SMTP_FROM)."
    except smtplib.SMTPRecipientsRefused:
        error = f"The email server refused the address {to}."
    except (OSError, smtplib.SMTPException) as exc:
        error = f"Could not send through {host}:{port} ({exc.__class__.__name__}: {exc})"
    current_app.logger.error("Email %r to %s failed: %s", subject, to, error)
    return error


def send_email(to, subject, body, reply_to=None):
    """Send a notification. Returns False if email isn't set up. Outside tests the email is sent in the
    background, so customers never wait on a slow mail server."""
    if not email_enabled() or not to:
        return False
    if current_app.testing:
        return deliver(to, subject, body, reply_to) is None
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            deliver(to, subject, body, reply_to)

    threading.Thread(target=run, name="send-email", daemon=True).start()
    return True


def send_test(to):
    site = site_info()
    body = (f"This is a test email from {site['name']}.\n\n"
            f"If you can read this, order confirmations, invoices and quote-request alerts will be delivered.\n\n"
            f"Sent from: {from_address()}\nAlerts go to: {admin_address()}\n")
    return deliver(to, f"Test email from {site['name']}", body)


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
    if inquiry["source"]:
        contact.append(f"Source: {inquiry['source']}")
    body = [f"{kind} {inquiry['reference']}", "", *contact, ""]
    if items:
        body += ["Products:", *[item_line(i) for i in items], ""]
    if inquiry["items"]:
        body += ["Other items / products:" if inquiry["kind"] == "quote" else "Products & quantities:", inquiry["items"], ""]
    if inquiry["message"]:
        body += ["Message:", inquiry["message"], ""]
    body.append(f"Open and reply: {admin_url}")
    send_email(admin_address(), f"{kind} {inquiry['reference']} from {inquiry['company'] or inquiry['name']}",
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
                   reply_to=admin_address())


def _order_lines(order, items):
    lines = [f"• {i['sku']} – {i['name']} × {i['qty']} @ {fmt_money(i['unit_price_cents'])} = "
             f"{fmt_money(i['line_total_cents'])}" for i in items]
    lines += ["", f"Subtotal: {fmt_money(order['subtotal_cents'])}",
              f"{'Delivery' if order['fulfillment'] == 'delivery' else 'Warehouse pickup'}: "
              f"{fmt_money(order['shipping_cents'])}",
              f"Total: {fmt_money(order['total_cents'])}"]
    return lines


def _fulfillment_line(order, site):
    if order["fulfillment"] == "delivery":
        return f"Delivery to: {order['address']}, {order['city']}, {order['state']} {order['zip']}"
    return f"Pickup at: {site['warehouse']} (bring your order number)"


def payment_instructions():
    return (current_app.config.get("PAYMENT_INSTRUCTIONS") or "").replace("\\n", "\n").strip()


def new_order(order, items):
    """Tell staff about a new order and send the customer a confirmation."""
    site = site_info()
    paid = order["status"] == "paid"
    lines = [
        f"New order {order['number']} — {fmt_money(order['total_cents'])}",
        f"Payment: {'card (paid)' if paid else 'invoice — send an invoice'}",
        f"{'Delivery' if order['fulfillment'] == 'delivery' else 'Warehouse pickup'}",
        "",
        f"Customer: {order['customer_name']}" + (f" — {order['company']}" if order["company"] else ""),
        f"Email: {order['email']}  Phone: {order['phone'] or '—'}",
        "",
        *_order_lines(order, items),
        "",
        f"Open: {url_for('admin.order_detail', order_id=order['id'], _external=True)}",
    ]
    send_email(admin_address(), f"New order {order['number']} from {order['customer_name']}", "\n".join(lines),
               reply_to=order["email"])

    order_url = url_for("store.order_status", number=order["number"], token=order["access_token"], _external=True)
    copy = [f"Hi {order['customer_name']},", ""]
    if paid:
        copy += [f"Thank you! We received your payment for order {order['number']}.",
                 f"We'll contact you to schedule {'delivery' if order['fulfillment'] == 'delivery' else 'pickup'}."]
    else:
        copy += [f"Thank you! Your order {order['number']} is confirmed and the items are reserved for you.",
                 "We'll email your invoice within one business day."]
    copy += ["", *_order_lines(order, items), "", _fulfillment_line(order, site), "",
             f"Order details: {order_url}", "", site["name"]]
    copy += [x for x in (site["phone"], site["email"]) if x]
    send_email(order["email"], f"Order {order['number']} confirmed – {site['name']}", "\n".join(copy),
               reply_to=admin_address())


def send_invoice(order, items):
    """Email the invoice (plain text + printable HTML). Returns None when sent, or what went wrong."""
    site = site_info()
    instructions = payment_instructions()
    invoice_url = url_for("store.order_invoice", number=order["number"], token=order["access_token"], _external=True)
    paid = order["status"] in ("paid", "fulfilled")
    body = [f"Hi {order['customer_name']},", "",
            f"Here is the invoice for order {order['number']}" + (" (paid — thank you!)." if paid else "."), "",
            *_order_lines(order, items), "", _fulfillment_line(order, site), ""]
    if not paid:
        body += ["How to pay:", instructions or f"Reply to this email or call {site['phone'] or 'us'} to arrange payment.",
                 f"Please include {order['number']} with your payment.", ""]
    body += [f"View or print the invoice: {invoice_url}", "", site["name"]]
    body += [x for x in (site["phone"], site["email"]) if x]
    html = render_template("invoice.html", order=order, items=items, payment_instructions=instructions,
                           for_email=True, invoice_url=invoice_url)
    return deliver(order["email"], f"Invoice {order['number']} – {site['name']}", "\n".join(body),
                   reply_to=admin_address(), html=html)
