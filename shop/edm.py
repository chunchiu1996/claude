"""Marketing emails (EDMs) written by AI and approved by a person before they go out.

1. Staff (or a scheduled `flask edm-draft` run) give a short brief. Claude writes the email from the
   shop's real data: products, prices and stock for US buyers, or the consignment offer for Chinese
   factories. It returns the copy plus a list of things a person should double-check.
2. The draft waits in *Admin → Campaigns → AI drafts*. Automatic checks flag prices that don't match
   the catalog, missing pieces, inactive products and spammy wording. The reviewer edits the copy,
   asks the AI for a rewrite with feedback, sends a test to themselves, then approves or rejects it.
3. Only an approved draft can be exported (HTML for Mailchimp, Brevo, SendCloud…) and marked as sent.
   Any edit to an approved draft sends it back for review, so what goes out is what was approved.

Claude is called over plain HTTPS (like Stripe in payments.py) so the same code runs on a server and
on Cloudflare Workers, where the Anthropic Python SDK's HTTP client isn't available.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import click
from flask import current_app, render_template, url_for
from flask.cli import with_appcontext

from . import cloud
from .catalog import PRODUCT_SELECT
from .pricing import fmt_money

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"
DEFAULT_MODEL = "claude-opus-5-5"

AUDIENCES = {
    "buyer": "US buyers — contractors, shops, homeowners (English)",
    "seller": "Chinese factories & stock owners — consignment (中文)",
}
STATUSES = {"review": "Needs review", "approved": "Approved", "rejected": "Rejected", "sent": "Sent"}
FIELDS = ("subject", "preheader", "headline", "intro", "bullets", "closing", "cta_label", "product_skus")
MAX_PRODUCTS = 6


class AIError(Exception):
    pass


def ai_enabled():
    return bool(current_app.config.get("ANTHROPIC_API_KEY"))


def clean_campaign(name):
    return "".join(ch for ch in (name or "").strip().lower().replace(" ", "-") if ch.isalnum() or ch in "-_")[:60]


def lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


# ---------------------------------------------------------------- facts the AI may use


def catalog_products(db, limit=60):
    """Active products the AI can feature: featured and well-stocked first."""
    return db.execute(
        PRODUCT_SELECT + " WHERE p.active = 1 ORDER BY p.featured DESC, (p.stock_qty > 0) DESC, p.stock_qty DESC, p.id"
        " LIMIT ?", (limit,)
    ).fetchall()


def _product_fact(p):
    fact = {"sku": p["sku"], "name": p["name"], "category": p["category_name"], "unit": p["unit"],
            "in_stock": p["stock_qty"], "min_order": p["moq"]}
    if p["unit_note"]:
        fact["unit_note"] = p["unit_note"]
    if p["quote_only"]:
        fact["price"] = "price on request (do not mention a price)"
    else:
        fact["price"] = f"{fmt_money(p['price_cents'])} per {p['unit']}"
        if p["best_tier_cents"] is not None and p["best_tier_cents"] < p["price_cents"]:
            fact["volume_price_from"] = fmt_money(p["best_tier_cents"])
        if p["compare_at_cents"]:
            fact["typical_retail"] = fmt_money(p["compare_at_cents"])
    if p["featured"]:
        fact["featured"] = True
    return fact


def facts(db, audience):
    """Everything the AI is allowed to state. It is told not to state anything else."""
    cfg = current_app.config
    categories = [r["name"] for r in db.execute(
        "SELECT c.name FROM categories c WHERE EXISTS (SELECT 1 FROM products p WHERE p.category_id = c.id"
        " AND p.active = 1) ORDER BY c.sort_order, c.name")]
    if audience == "seller":
        return {
            "company": cfg.get("SELLER_SITE_NAME") or cfg["SITE_NAME"],
            "what_we_do": "We sell home improvement products that Chinese factories have already shipped to the US. "
                          "Factories consign stock to our US warehouse; we list it, sell it to US contractors, "
                          "remodelers, building-supply shops and homeowners, keep a commission, and pay the "
                          "factory its share monthly.",
            "warehouse": cfg["WAREHOUSE_ADDRESS"],
            "services": ["receiving into our US warehouse", "listing and selling online and to trade customers",
                         "pickup and local delivery", "monthly settlement after goods sell",
                         "a private online dashboard showing stock, sales and earnings",
                         "communication in Chinese, WeChat available"],
            "accepted_categories": ["flooring", "tile / stone", "cabinets / vanities", "faucets / bathroom",
                                    "lighting", "doors / hardware", "countertops (quartz, sintered stone)",
                                    "other building materials"],
            "accepts": "stock already in a US warehouse, containers on the way, and stock planned for shipment",
            "categories_we_sell_now": categories,
            "commission": "agreed per factory; not a fixed public number (do not state a percentage)",
        }
    return {
        "store": cfg["SITE_NAME"],
        "tagline": cfg["SITE_TAGLINE"],
        "warehouse_pickup": f"free pickup at {cfg['WAREHOUSE_ADDRESS']}",
        "local_delivery_fee": fmt_money(cfg["DELIVERY_FEE_CENTS"]),
        "payment": "card online" + (", or reserve now and pay by invoice (Zelle/ACH/check)"
                                    if cfg.get("ALLOW_INVOICE") else ""),
        "quotes": "buyers can request a quote for any quantity without paying; we reply within one business day",
        "volume_pricing": "many products have automatic volume price breaks",
        "categories": categories,
        "products": [_product_fact(p) for p in catalog_products(db)],
    }


# ---------------------------------------------------------------- calling Claude

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subject_options", "preheader", "headline", "intro", "bullets", "closing", "cta_label",
                 "product_skus", "reviewer_notes"],
    "properties": {
        "subject_options": {"type": "array", "items": {"type": "string"},
                            "description": "Three subject lines, best first."},
        "preheader": {"type": "string", "description": "Inbox preview line."},
        "headline": {"type": "string"},
        "intro": {"type": "string", "description": "Opening paragraph."},
        "bullets": {"type": "array", "items": {"type": "string"}, "description": "3-5 short selling points."},
        "closing": {"type": "string", "description": "One or two sentences before the button."},
        "cta_label": {"type": "string", "description": "Button text, 2-5 words."},
        "product_skus": {"type": "array", "items": {"type": "string"},
                         "description": "SKUs to feature (buyer emails only), from the facts."},
        "reviewer_notes": {"type": "array", "items": {"type": "string"},
                           "description": "Anything the human reviewer should confirm before sending."},
    },
}

SYSTEM_PROMPT = """You write marketing emails (EDMs) for a small business. A staff member reviews, edits and \
approves every draft before it is sent, so your job is a strong first draft that is accurate.

Accuracy comes first:
- State only what the <facts> support. Never invent prices, discounts, sale deadlines, stock levels, \
shipping times, certifications, awards, reviews or customer names.
- If the brief asks for something the facts don't support (for example "20% off" when no discount is in the \
facts), write the copy without the unsupported claim and explain in reviewer_notes what the reviewer must \
confirm or add.
- Prices you mention must be copied exactly from the facts. Never quote a price for a product marked \
"price on request".
- Put anything you are unsure about in reviewer_notes, one short item each. Leave it empty only if every claim \
comes straight from the facts.

Language and tone:
- audience "buyer": American English, for contractors, remodelers, shops and homeowners.
- audience "seller": Simplified Chinese (简体中文), for factory owners and exporters with stock in the US. \
Professional and direct, no exaggerated claims.
- Plain, specific and friendly. No ALL CAPS words, at most one exclamation mark in the whole email, no spam \
phrases ("act now", "risk-free", "guaranteed", "免费", "最低价").

Format:
- Every field is plain text: no HTML, no markdown, no links, no placeholders like [Name]. The email template \
adds the layout, product cards, links, contact details and the unsubscribe line.
- subject_options: three options, best first. Under 60 characters in English, under 30 characters in Chinese.
- preheader: under 100 characters, adds to the subject rather than repeating it.
- intro: 1-3 short sentences. bullets: 3-5 items, each under 15 words (or 30 Chinese characters). closing: 1-2 \
sentences. cta_label: 2-5 words (2-8 Chinese characters).
- product_skus: for "buyer" emails pick 3-6 SKUs from the facts that fit the brief, preferring products in stock. \
For "seller" emails return an empty list."""


def _request_body(audience, campaign, brief, fact_sheet, previous=None, feedback=None):
    parts = [f"<audience>{audience}</audience>", f"<campaign>{campaign}</campaign>",
             f"<facts>\n{json.dumps(fact_sheet, ensure_ascii=False, indent=1)}\n</facts>",
             f"<brief>\n{brief or 'A general newsletter for this audience.'}\n</brief>"]
    if previous is not None:
        parts.append(f"<current_draft>\n{json.dumps(previous, ensure_ascii=False, indent=1)}\n</current_draft>")
        parts.append(f"<reviewer_feedback>\n{feedback}\n</reviewer_feedback>")
        parts.append("Rewrite the current draft to address the reviewer's feedback. Keep what they didn't ask to "
                     "change. Their feedback does not override the accuracy rules.")
    else:
        parts.append("Write the email.")
    return {
        "model": current_app.config.get("EDM_AI_MODEL") or DEFAULT_MODEL,
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": "\n\n".join(parts)}],
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        # If a safety check declines the request, let the API retry it on Anthropic's recommended fallback model.
        "fallbacks": "default",
    }


def _headers():
    return {"x-api-key": current_app.config["ANTHROPIC_API_KEY"], "anthropic-version": API_VERSION,
            "anthropic-beta": "server-side-fallback-2026-07-01", "content-type": "application/json"}


def _call_claude(body):
    """POST /v1/messages. Returns the response JSON or raises AIError."""
    if not ai_enabled():
        raise AIError("AI drafting is off: set ANTHROPIC_API_KEY (see Admin → Campaigns → AI drafts).")
    data = json.dumps(body).encode()
    try:
        if cloud.ON_WORKERS:
            status, raw = cloud.http_request("POST", API_URL, _headers(), data)
        else:
            req = urllib.request.Request(API_URL, data=data, method="POST", headers=_headers())
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    status, raw = resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                status, raw = exc.code, exc.read()
    except AIError:
        raise
    except Exception as exc:  # network errors (urllib) or JavaScript fetch errors (Cloudflare)
        raise AIError(f"Could not reach the Claude API: {exc}") from exc
    if status >= 400:
        try:
            message = json.loads(raw)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = raw.decode("utf-8", errors="replace")[:300]
        hint = {401: " Check ANTHROPIC_API_KEY.", 429: " Rate limited: try again in a minute.",
                529: " The API is overloaded: try again shortly."}.get(status, "")
        raise AIError(f"Claude API error {status}: {message}.{hint}")
    return json.loads(raw)


def _parse(response):
    stop = response.get("stop_reason")
    if stop == "refusal":
        explanation = (response.get("stop_details") or {}).get("explanation") or ""
        raise AIError(f"The AI declined to write this email. {explanation}".strip())
    if stop == "max_tokens":
        raise AIError("The AI's answer was cut off. Try a shorter brief.")
    text = "".join(b.get("text", "") for b in response.get("content", []) if b.get("type") == "text")
    try:
        out = json.loads(text)
    except ValueError as exc:
        raise AIError("The AI's answer wasn't in the expected format. Try again.") from exc
    for key in OUTPUT_SCHEMA["required"]:
        if key not in out:
            raise AIError(f"The AI's answer is missing '{key}'. Try again.")
    return out


def _as_fields(db, audience, out):
    """Turn the AI's answer into draft columns, dropping any SKU that isn't in the catalog."""
    notes = [n.strip() for n in out["reviewer_notes"] if n.strip()]
    skus = []
    if audience == "buyer":
        for sku in out["product_skus"]:
            row = db.execute("SELECT sku FROM products WHERE sku = ? COLLATE NOCASE AND active = 1",
                             (sku.strip(),)).fetchone()
            if row and row["sku"] not in skus:
                skus.append(row["sku"])
            elif not row:
                notes.append(f"The AI suggested SKU {sku!r}, which isn't an active product; it was left out.")
    subjects = [s.strip() for s in out["subject_options"] if s.strip()]
    return {
        "subject": subjects[0] if subjects else "",
        "subject_options": "\n".join(subjects[1:]),
        "preheader": out["preheader"].strip(),
        "headline": out["headline"].strip(),
        "intro": out["intro"].strip(),
        "bullets": "\n".join(b.strip() for b in out["bullets"] if b.strip()),
        "closing": out["closing"].strip(),
        "cta_label": out["cta_label"].strip(),
        "product_skus": "\n".join(skus[:MAX_PRODUCTS]),
        "ai_notes": "\n".join(notes),
    }


def _draft_for_ai(draft):
    return {"subject": draft["subject"], "preheader": draft["preheader"], "headline": draft["headline"],
            "intro": draft["intro"], "bullets": lines(draft["bullets"]), "closing": draft["closing"],
            "cta_label": draft["cta_label"], "product_skus": lines(draft["product_skus"])}


def log(db, draft_id, action, note=None):
    db.execute("INSERT INTO edm_events (draft_id, action, note) VALUES (?, ?, ?)", (draft_id, action, note or None))


def create_draft(db, audience, campaign, brief):
    """Ask the AI for a new draft and save it for review. Returns the new draft's id."""
    if audience not in AUDIENCES:
        raise AIError("Choose who the email is for.")
    campaign = clean_campaign(campaign)
    if not campaign:
        raise AIError("Give the campaign a name (letters, numbers and dashes).")
    body = _request_body(audience, campaign, brief, facts(db, audience))
    response = _call_claude(body)
    fields = _as_fields(db, audience, _parse(response))
    fields.update(campaign=campaign, audience=audience, brief=(brief or "").strip() or None,
                  ai_model=response.get("model") or body["model"], status="review")
    draft_id = db.execute(f"INSERT INTO edm_drafts ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
                          list(fields.values())).lastrowid
    log(db, draft_id, "generated", f"Written by {fields['ai_model']}")
    db.commit()
    return draft_id


def revise_draft(db, draft, feedback):
    """Ask the AI to rewrite a draft with the reviewer's feedback. The draft goes back to review."""
    feedback = (feedback or "").strip()
    if not feedback:
        raise AIError("Tell the AI what to change.")
    body = _request_body(draft["audience"], draft["campaign"], draft["brief"], facts(db, draft["audience"]),
                         previous=_draft_for_ai(draft), feedback=feedback)
    response = _call_claude(body)
    fields = _as_fields(db, draft["audience"], _parse(response))
    fields.update(ai_model=response.get("model") or body["model"], status="review", approved_at=None)
    db.execute(f"UPDATE edm_drafts SET {', '.join(f'{k} = ?' for k in fields)}, updated_at = datetime('now') "
               "WHERE id = ?", [*fields.values(), draft["id"]])
    log(db, draft["id"], "revised by AI", feedback)
    db.commit()


def get(db, draft_id):
    return db.execute("SELECT * FROM edm_drafts WHERE id = ?", (draft_id,)).fetchone()


def events(db, draft_id):
    return db.execute("SELECT * FROM edm_events WHERE draft_id = ? ORDER BY id DESC", (draft_id,)).fetchall()


def featured_products(db, draft):
    found = []
    for sku in lines(draft["product_skus"]):
        row = db.execute(PRODUCT_SELECT + " WHERE p.sku = ? COLLATE NOCASE", (sku,)).fetchone()
        if row:
            found.append(row)
    return found


# ---------------------------------------------------------------- automatic checks for the reviewer

SPAM_PHRASES = ("act now", "risk-free", "risk free", "guaranteed", "100% free", "click here", "limited time only",
                "免费", "最低价", "全网最低", "100%")
_PRICE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
_PLACEHOLDER = re.compile(r"\[[^\]]*\]|\{\{|\}\}|\bTODO\b|\bXXX\b|lorem ipsum", re.IGNORECASE)
_CJK = re.compile(r"[一-鿿]")


def _copy_text(draft):
    return "\n".join(draft[k] or "" for k in ("subject", "preheader", "headline", "intro", "bullets", "closing",
                                              "cta_label"))


def _known_prices(db, products):
    cents = {current_app.config["DELIVERY_FEE_CENTS"]}
    for p in products:
        cents.update(c for c in (p["price_cents"], p["compare_at_cents"]) if c)
        cents.update(r["price_cents"] for r in db.execute("SELECT price_cents FROM price_tiers WHERE product_id = ?",
                                                          (p["id"],)))
    return cents


def checks(db, draft):
    """[(level, message)]: 'error' blocks approval, 'warn' needs a look, 'ok' passed."""
    out = []
    text = _copy_text(draft)
    zh = draft["audience"] == "seller"

    for key, label in (("subject", "Subject line"), ("headline", "Headline"), ("cta_label", "Button text")):
        if not (draft[key] or "").strip():
            out.append(("error", f"{label} is empty."))
    subject_limit = 30 if zh else 60
    if len(draft["subject"] or "") > subject_limit:
        out.append(("warn", f"Subject is {len(draft['subject'])} characters; inboxes cut it off after about "
                            f"{subject_limit}."))
    if not (draft["preheader"] or "").strip():
        out.append(("warn", "No preheader: inboxes will show the first words of the email instead."))
    if _PLACEHOLDER.search(text):
        out.append(("error", f"Placeholder text left in the copy: “{_PLACEHOLDER.search(text).group(0)}”."))
    if zh and not _CJK.search((draft["subject"] or "") + (draft["headline"] or "")):
        out.append(("warn", "This email is for Chinese factories but the subject and headline aren't in Chinese."))
    if not zh and _CJK.search(text):
        out.append(("warn", "This email is for US buyers but contains Chinese text."))

    products = featured_products(db, draft) if not zh else []
    if not zh:
        missing = [s for s in lines(draft["product_skus"]) if s.lower() not in {p["sku"].lower() for p in products}]
        for sku in missing:
            out.append(("error", f"SKU {sku} isn't in the catalog."))
        for p in products:
            if not p["active"]:
                out.append(("error", f"{p['sku']} ({p['name']}) is hidden in the shop."))
            elif p["stock_qty"] <= 0:
                out.append(("warn", f"{p['sku']} ({p['name']}) is out of stock."))
        if not products and not missing:
            out.append(("warn", "No products featured."))
        known = _known_prices(db, products)
        for match in _PRICE.finditer(text):
            cents = round(float(match.group(1).replace(",", "")) * 100)
            if cents not in known:
                out.append(("error", f"{match.group(0)} in the copy doesn't match the price of any featured product "
                                     "or the delivery fee. Fix it or feature the product it refers to."))
    if re.search(r"\d+\s?%\s?(off|discount)|\d+\s?折|打折|优惠\s?\d", text, re.IGNORECASE):
        out.append(("warn", "The copy promises a discount: make sure it's real and the prices in the shop reflect it."))

    lowered = text.lower()
    spam = [p for p in SPAM_PHRASES if p in lowered]
    if spam:
        out.append(("warn", "Phrases that spam filters dislike: " + ", ".join(f"“{p}”" for p in spam) + "."))
    shouting = [w for w in re.findall(r"\b[A-Z]{4,}\b", text) if w not in {"SKU", "USA", "ACH", "LVP", "SPC"}]
    if len(shouting) > 1:
        out.append(("warn", "ALL-CAPS words look like spam: " + ", ".join(sorted(set(shouting))) + "."))
    if text.count("!") + text.count("！") > 2:
        out.append(("warn", "Lots of exclamation marks: spam filters and readers both dislike them."))

    notes = lines(draft["ai_notes"])
    if notes:
        out.append(("warn", f"The AI asked you to confirm {len(notes)} thing{'s' if len(notes) != 1 else ''} "
                            "(listed under “AI's notes”)."))
    if not any(level != "ok" for level, _ in out):
        out.append(("ok", "All automatic checks passed. Still read it through before approving."))
    return out


def blocking(check_results):
    return [msg for level, msg in check_results if level == "error"]


# ---------------------------------------------------------------- the email itself


def _tracked(url, campaign):
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urllib.parse.urlencode({'utm_source': 'edm', 'utm_medium': 'email', 'utm_campaign': campaign})}"


def _absolute(url):
    if url and url.startswith("/"):
        return url_for("store.home", _external=True).rstrip("/") + url
    return url


def render_html(db, draft):
    """The finished email as HTML that works in Outlook, Gmail, QQ Mail and 163 Mail. Needs a request context."""
    from .photos import thumb_url
    from .seller import landing_url, seller_site, seller_url

    campaign = draft["campaign"]
    context = {"draft": draft, "bullets": lines(draft["bullets"])}
    if draft["audience"] == "seller":
        context.update(
            lang="zh-Hans", sender=seller_site(), cta_url=landing_url(campaign),
            links=[("直接申请合作", seller_url("seller.apply", utm_source="edm", utm_campaign=campaign)),
                   ("下载产品表模板（Excel）", seller_url("seller.product_template"))],
            products=[], unsubscribe="如不希望再收到此类邮件，请回复“退订”，我们会将您移出邮件列表。",
        )
    else:
        from .notify import site_info

        products = [{
            "name": p["name"], "sku": p["sku"], "unit": p["unit"], "unit_note": p["unit_note"],
            "price": None if p["quote_only"] else fmt_money(p["price_cents"]),
            "compare_at": fmt_money(p["compare_at_cents"]) if p["compare_at_cents"] and not p["quote_only"] else None,
            "image": _absolute(thumb_url(p["image_url"])) if p["image_url"] else None,
            "url": _tracked(url_for("store.product", slug=p["slug"], _external=True), campaign),
        } for p in featured_products(db, draft)]
        context.update(
            lang="en", sender=site_info(), cta_url=_tracked(url_for("store.home", _external=True), campaign),
            links=[("Request a quote", _tracked(url_for("store.quote", _external=True), campaign))],
            products=products,
            unsubscribe="You're receiving this because you're a customer or asked to hear from us. "
                        "Reply “unsubscribe” and we'll take you off the list.",
        )
    return render_template("edm/email.html", **context)


def render_text(db, draft):
    """Plain-text version, sent alongside the HTML."""
    parts = [draft["headline"] or "", "", draft["intro"] or "", ""]
    parts += [f"• {b}" for b in lines(draft["bullets"])]
    if draft["audience"] == "buyer":
        products = featured_products(db, draft)
        if products:
            parts.append("")
            for p in products:
                price = "Price on request" if p["quote_only"] else f"{fmt_money(p['price_cents'])} / {p['unit']}"
                parts.append(f"- {p['name']} ({p['sku']}): {price}")
        url = _tracked(url_for("store.home", _external=True), draft["campaign"])
    else:
        from .seller import landing_url

        url = landing_url(draft["campaign"])
    parts += ["", draft["closing"] or "", "", f"{draft['cta_label']}: {url}"]
    return "\n".join(parts).strip() + "\n"


# ---------------------------------------------------------------- scheduled drafting (cron)


@click.command("edm-draft")
@click.option("--audience", type=click.Choice(sorted(AUDIENCES)), default="buyer", show_default=True)
@click.option("--campaign", default=None, help="Tracking name for the links (default: <audience>-<YYYYMMDD>).")
@click.option("--brief", default="", help="What the email should do.")
@click.option("--site-url", envvar="SITE_URL", default="",
              help="The shop's address, e.g. https://shop.liquidatorusa.com, for the review link in the alert email.")
@with_appcontext
def draft_command(audience, campaign, brief, site_url):
    """Have the AI write a marketing email and queue it for review (nothing is sent to customers)."""
    from datetime import date

    from .db import get_db
    from .notify import admin_address, send_email

    try:
        draft_id = create_draft(get_db(), audience, campaign or f"{audience}-{date.today():%Y%m%d}", brief)
    except AIError as exc:
        raise click.ClickException(str(exc)) from exc
    path = f"/admin/campaigns/drafts/{draft_id}"
    link = site_url.rstrip("/") + path if site_url else path
    draft = get(get_db(), draft_id)
    send_email(admin_address(), f"AI email draft to review: {draft['subject']}",
               f"The AI wrote a new marketing email for {AUDIENCES[audience]}.\n\n"
               f"Subject: {draft['subject']}\n\nNothing has been sent. Review, edit and approve it here:\n{link}\n")
    click.echo(f"Draft {draft_id} is waiting for review: {link}")
