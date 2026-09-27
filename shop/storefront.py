import secrets

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from . import inquiries as inq
from . import notify, orders, payments
from .cart import (
    build_lines,
    build_quote_lines,
    clear_cart,
    get_cart,
    get_quote_list,
    save_cart,
    save_quote_list,
)
from .catalog import CUSTOMER_TYPES, PRODUCT_SELECT, categories_with_counts, get_tiers, parse_specs
from .db import get_db
from .pricing import pluralize

bp = Blueprint("store", __name__)

PER_PAGE = 24
SORTS = {
    "featured": ("Featured", "p.featured DESC, (p.stock_qty > 0) DESC, p.name"),
    "price_asc": ("Price: low to high", "p.price_cents ASC"),
    "price_desc": ("Price: high to low", "p.price_cents DESC"),
    "newest": ("Newest arrivals", "p.created_at DESC, p.id DESC"),
}


@bp.route("/")
def home():
    db = get_db()
    featured = db.execute(
        PRODUCT_SELECT + " WHERE p.active = 1 AND p.stock_qty > 0 ORDER BY p.featured DESC, p.created_at DESC LIMIT 8"
    ).fetchall()
    return render_template("store/home.html", categories=categories_with_counts(db), featured=featured)


@bp.route("/shop")
@bp.route("/shop/<slug>")
def catalog(slug=None):
    db = get_db()
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "featured")
    if sort not in SORTS:
        sort = "featured"
    in_stock = request.args.get("in_stock") == "1"
    page = max(request.args.get("page", 1, type=int), 1)

    where, params, category = ["p.active = 1"], [], None
    if slug:
        category = db.execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone()
        if category is None:
            abort(404)
        where.append("p.category_id = ?")
        params.append(category["id"])
    if q:
        where.append("(p.name LIKE ? OR p.sku LIKE ? OR p.description LIKE ? OR c.name LIKE ?)")
        params += [f"%{q}%"] * 4
    if in_stock:
        where.append("p.stock_qty > 0")
    where_sql = " WHERE " + " AND ".join(where)

    total = db.execute(
        "SELECT COUNT(*) FROM products p LEFT JOIN categories c ON c.id = p.category_id" + where_sql, params
    ).fetchone()[0]
    products = db.execute(
        PRODUCT_SELECT + where_sql + f" ORDER BY {SORTS[sort][1]} LIMIT ? OFFSET ?",
        [*params, PER_PAGE, (page - 1) * PER_PAGE],
    ).fetchall()
    return render_template(
        "store/catalog.html",
        products=products,
        category=category,
        q=q,
        sort=sort,
        sorts=SORTS,
        in_stock=in_stock,
        page=page,
        pages=max((total + PER_PAGE - 1) // PER_PAGE, 1),
        total=total,
        page_url=lambda n: url_for(request.endpoint, **{**(request.view_args or {}), **request.args.to_dict(), "page": n}),
    )


@bp.route("/p/<slug>")
def product(slug):
    db = get_db()
    item = db.execute(PRODUCT_SELECT + " WHERE p.slug = ? AND p.active = 1", (slug,)).fetchone()
    if item is None:
        abort(404)
    related = db.execute(
        PRODUCT_SELECT + " WHERE p.active = 1 AND p.category_id IS ? AND p.id != ? "
        "ORDER BY (p.stock_qty > 0) DESC, p.featured DESC LIMIT 4",
        (item["category_id"], item["id"]),
    ).fetchall()
    return render_template(
        "store/product.html",
        p=item,
        tiers=get_tiers(db, item["id"]),
        specs=parse_specs(item["specs"]),
        related=related,
    )


# ---------------------------------------------------------------- cart


@bp.route("/cart")
def cart():
    lines, subtotal, problems = build_lines(get_db(), get_cart())
    return render_template("store/cart.html", lines=lines, subtotal=subtotal, problems=problems)


@bp.post("/cart/add")
def cart_add():
    db = get_db()
    product_id = request.form.get("product_id", type=int)
    qty = max(request.form.get("qty", 1, type=int) or 1, 1)
    item = db.execute("SELECT * FROM products WHERE id = ? AND active = 1", (product_id,)).fetchone()
    if item is None:
        abort(404)
    if item["quote_only"]:
        flash(f"{item['name']} is priced by quote. Add it to your quote list and we'll send you our best price.", "error")
        return redirect(url_for("store.product", slug=item["slug"]))
    if item["stock_qty"] <= 0:
        flash(f"{item['name']} is out of stock. Request a quote and we'll tell you when more arrives.", "error")
        return redirect(url_for("store.product", slug=item["slug"]))

    cart = get_cart()
    new_qty = max(cart.get(str(item["id"]), 0) + qty, item["moq"])
    if new_qty > item["stock_qty"]:
        new_qty = item["stock_qty"]
        flash(f"Only {item['stock_qty']} {pluralize(item['unit'], item['stock_qty'])} available — quantity adjusted.", "error")
    cart[str(item["id"])] = new_qty
    save_cart(cart)
    flash(f"Added {item['name']} to your cart.", "success")
    return redirect(url_for("store.cart"))


@bp.post("/cart/update")
def cart_update():
    cart = get_cart()
    for key in list(cart):
        value = request.form.get(f"qty_{key}", type=int)
        if value is not None:
            cart[key] = max(value, 0)
    remove = request.form.get("remove")
    if remove in cart:
        del cart[remove]
    save_cart(cart)
    return redirect(url_for("store.cart"))


# ---------------------------------------------------------------- checkout


def _checkout_form():
    f = request.form
    data = {
        "customer_name": f.get("customer_name", "").strip(),
        "email": f.get("email", "").strip(),
        "phone": f.get("phone", "").strip(),
        "company": f.get("company", "").strip(),
        "customer_type": f.get("customer_type", "").strip(),
        "fulfillment": f.get("fulfillment", "pickup"),
        "address": f.get("address", "").strip(),
        "city": f.get("city", "").strip(),
        "state": f.get("state", "").strip(),
        "zip": f.get("zip", "").strip(),
        "notes": f.get("notes", "").strip(),
        "payment_method": f.get("payment_method", ""),
    }
    errors = {}
    if not data["customer_name"]:
        errors["customer_name"] = "Please enter your name."
    if "@" not in data["email"] or "." not in data["email"].split("@")[-1]:
        errors["email"] = "Please enter a valid email."
    if not data["phone"]:
        errors["phone"] = "We need a phone number to coordinate pickup or delivery."
    if data["fulfillment"] not in ("pickup", "delivery"):
        errors["fulfillment"] = "Choose pickup or delivery."
    if data["fulfillment"] == "delivery":
        for field in ("address", "city", "state", "zip"):
            if not data[field]:
                errors[field] = "Required for delivery."
    if data["payment_method"] not in _payment_methods():
        errors["payment_method"] = "Choose a payment method."
    return data, errors


def _payment_methods():
    methods = []
    if payments.enabled():
        methods.append("card")
    if current_app.config["ALLOW_INVOICE"] or not methods:
        methods.append("invoice")
    return methods


@bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    db = get_db()
    lines, subtotal, problems = build_lines(db, get_cart())
    if not lines:
        return redirect(url_for("store.cart"))
    if problems:
        for problem in problems:
            flash(problem, "error")
        return redirect(url_for("store.cart"))

    delivery_fee = current_app.config["DELIVERY_FEE_CENTS"] or 0
    data, errors = ({"fulfillment": "pickup"}, {}) if request.method == "GET" else _checkout_form()
    if request.method == "POST" and not errors:
        shipping = delivery_fee if data["fulfillment"] == "delivery" else 0
        try:
            order = orders.place_order(db, data, lines, shipping, data["payment_method"])
        except orders.StockError as exc:
            flash(str(exc), "error")
            return redirect(url_for("store.cart"))
        clear_cart()
        order_url = url_for("store.order_status", number=order["number"], token=order["access_token"], _external=True)
        if order["payment_method"] == "card":
            try:
                session_id, pay_url = payments.create_checkout_session(
                    order,
                    orders.get_items(db, order["id"]),
                    success_url=order_url,
                    cancel_url=url_for(
                        "store.order_cancel", number=order["number"], token=order["access_token"], _external=True
                    ),
                )
            except payments.PaymentError:
                current_app.logger.exception("Stripe checkout failed for order %s", order["number"])
                _restore_cart_from(db, order)
                flash("We couldn't start card payment. Please try again or choose pay-by-invoice.", "error")
                return redirect(url_for("store.checkout"))
            db.execute("UPDATE orders SET stripe_session_id = ? WHERE id = ?", (session_id, order["id"]))
            db.commit()
            return redirect(pay_url, code=303)
        notify.new_order(order, orders.get_items(db, order["id"]))
        return redirect(order_url)

    return render_template(
        "store/checkout.html",
        lines=lines,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        data=data,
        errors=errors,
        methods=_payment_methods(),
        customer_types=CUSTOMER_TYPES,
    )


def _order_or_404(number, token):
    order = get_db().execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone()
    if order is None or not secrets.compare_digest(token, order["access_token"]):
        abort(404)
    return order


def _restore_cart_from(db, order):
    """Cancel an unpaid card order and put its items back in the cart."""
    items = orders.get_items(db, order["id"])
    orders.cancel_order(db, order["id"], "Card payment not completed")
    save_cart({str(i["product_id"]): i["qty"] for i in items if i["product_id"]})


@bp.route("/order/<number>/<token>")
def order_status(number, token):
    db = get_db()
    order = _order_or_404(number, token)
    if (
        order["status"] == "pending_payment"
        and order["payment_method"] == "card"
        and order["stripe_session_id"]
        and payments.enabled()
    ):
        try:
            stripe_session = payments.retrieve_checkout_session(order["stripe_session_id"])
            if (stripe_session.get("payment_status") == "paid"
                    and stripe_session.get("client_reference_id") == order["number"]):
                orders.set_status(db, order["id"], "paid")
                order = orders.get_order(db, order["id"])
                notify.new_order(order, orders.get_items(db, order["id"]))
        except payments.PaymentError:
            current_app.logger.exception("Could not verify Stripe payment for %s", number)
    return render_template("store/order.html", order=order, items=orders.get_items(db, order["id"]))


@bp.route("/order/<number>/<token>/cancel")
def order_cancel(number, token):
    db = get_db()
    order = _order_or_404(number, token)
    if order["status"] == "pending_payment" and order["payment_method"] == "card":
        _restore_cart_from(db, order)
        flash("Payment was cancelled — your items are back in your cart.", "error")
    return redirect(url_for("store.cart"))


# ---------------------------------------------------------------- quote requests


@bp.post("/quote/add")
def quote_add():
    db = get_db()
    product_id = request.form.get("product_id", type=int)
    qty = max(request.form.get("qty", 1, type=int) or 1, 1)
    item = db.execute("SELECT * FROM products WHERE id = ? AND active = 1", (product_id,)).fetchone()
    if item is None:
        abort(404)
    quote_list = get_quote_list()
    quote_list[str(item["id"])] = quote_list.get(str(item["id"]), 0) + qty
    save_quote_list(quote_list)
    flash(f"Added {item['name']} to your quote list.", "success")
    return redirect(url_for("store.quote"))


def _apply_quote_edits(quote_list):
    for key in list(quote_list):
        value = request.form.get(f"qty_{key}", type=int)
        if value is not None:
            quote_list[key] = max(value, 0)
    if request.form.get("remove") in quote_list:
        del quote_list[request.form["remove"]]
    save_quote_list(quote_list)
    return get_quote_list()


@bp.post("/quote/update")
def quote_update():
    _apply_quote_edits(get_quote_list())
    return redirect(url_for("store.quote"))


@bp.post("/quote/from-cart")
def quote_from_cart():
    quote_list = get_quote_list()
    for key, qty in get_cart().items():
        quote_list[key] = quote_list.get(key, 0) + qty
    save_quote_list(quote_list)
    clear_cart()
    flash("Your cart is now a quote request. Add your details and we'll reply with our best price.", "success")
    return redirect(url_for("store.quote"))


def _quote_form():
    f = request.form
    data = {k: f.get(k, "").strip()
            for k in ("name", "email", "phone", "company", "role", "location", "items", "message", "contact_pref")}
    if data["contact_pref"] not in inq.CONTACT_PREFS:
        data["contact_pref"] = "email"
    errors = {}
    if not data["name"]:
        errors["name"] = "Please enter your name."
    if data["email"] and ("@" not in data["email"] or "." not in data["email"].split("@")[-1]):
        errors["email"] = "Please enter a valid email."
    if data["contact_pref"] == "email" and not data["email"]:
        errors["email"] = "Enter your email so we can send your quote."
    if data["contact_pref"] != "email" and not data["phone"]:
        errors["phone"] = f"Enter your {inq.CONTACT_PREFS[data['contact_pref']]} number so we can reach you."
    return data, errors


@bp.route("/quote", methods=["GET", "POST"])
def quote():
    db = get_db()
    data, errors = {"contact_pref": "whatsapp" if current_app.config.get("WHATSAPP_NUMBER") else "email"}, {}
    quote_list = get_quote_list()
    if request.method == "POST":
        quote_list = _apply_quote_edits(quote_list)
        data, errors = _quote_form()
        lines = build_quote_lines(db, quote_list)
        if not lines and not data["items"]:
            errors["items"] = "Add products to your quote list, or describe what you need here."
        if not errors:
            inquiry = inq.create(db, "quote", data, lines)
            session.pop("quote", None)
            notify.new_inquiry(inquiry, inq.get_items(db, inquiry["id"]))
            return redirect(url_for("store.quote_request", reference=inquiry["reference"],
                                    token=inquiry["access_token"]))
    return render_template(
        "store/quote.html", lines=build_quote_lines(db, quote_list), data=data, errors=errors,
        customer_types=CUSTOMER_TYPES, contact_prefs=inq.CONTACT_PREFS,
    )


@bp.route("/quote/r/<reference>/<token>")
def quote_request(reference, token):
    """The customer's copy of their request: print/save as PDF, or send it to us on WhatsApp / email."""
    db = get_db()
    inquiry = db.execute("SELECT * FROM inquiries WHERE reference = ?", (reference,)).fetchone()
    if inquiry is None or not inquiry["access_token"] or not secrets.compare_digest(token, inquiry["access_token"]):
        abort(404)
    items = inq.get_items(db, inquiry["id"])
    site = notify.site_info()
    text = inq.request_text(site["name"], inquiry, items, url=request.url)
    return render_template(
        "store/quote_request.html", inquiry=inquiry, items=items, contact_prefs=inq.CONTACT_PREFS,
        whatsapp_url=notify.whatsapp_link(site["whatsapp"], text),
        mailto_url=notify.mailto_link(site["email"], f"Quote request {inquiry['reference']}", text),
        message_text=text,
        email_enabled=bool(current_app.config.get("SMTP_HOST")),
    )


@bp.route("/sell-with-us", methods=["GET", "POST"])
def sell_with_us():
    data, errors = {}, {}
    if request.method == "POST":
        f = request.form
        data = {k: f.get(k, "").strip() for k in ("name", "email", "phone", "company", "location", "items", "message")}
        errors = {k: "Required." for k in ("name", "email", "company", "items") if not data[k]}
        if data["email"] and "@" not in data["email"]:
            errors["email"] = "Please enter a valid email."
        if not errors:
            db = get_db()
            inquiry = inq.create(db, "supplier", {**data, "role": "Factory / supplier"})
            notify.new_inquiry(inquiry, [])
            return render_template("store/thanks.html", kind="supplier")
    return render_template("store/sell.html", data=data, errors=errors)


# ---------------------------------------------------------------- misc


@bp.route("/media/<path:filename>")
def media(filename):
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename, max_age=86400 * 30)


def _absolute(url):
    if url and url.startswith("/"):
        return request.host_url.rstrip("/") + url
    return url


@bp.route("/feed/products.xml")
def product_feed():
    """Google Merchant Center / Meta catalog feed: free listings on Google Shopping & Facebook."""
    # Google/Meta require a price, so quote-only products are left out of the feed.
    products = get_db().execute(PRODUCT_SELECT + " WHERE p.active = 1 AND p.quote_only = 0 ORDER BY p.id").fetchall()
    xml = render_template("store/feed.xml", products=products, absolute=_absolute)
    return xml, 200, {"Content-Type": "application/xml; charset=utf-8"}


@bp.route("/sitemap.xml")
def sitemap():
    db = get_db()
    products = db.execute("SELECT slug, updated_at FROM products WHERE active = 1").fetchall()
    categories = db.execute("SELECT slug FROM categories").fetchall()
    xml = render_template("store/sitemap.xml", products=products, categories=categories)
    return xml, 200, {"Content-Type": "application/xml; charset=utf-8"}


@bp.route("/robots.txt")
def robots():
    body = f"User-agent: *\nDisallow: /admin\nDisallow: /supplier\nDisallow: /order\nSitemap: {url_for('store.sitemap', _external=True)}\n"
    return body, 200, {"Content-Type": "text/plain"}


@bp.app_errorhandler(404)
def not_found(exc):
    return render_template("store/error.html", code=404, message="We couldn't find that page."), 404


@bp.app_errorhandler(400)
def bad_request(exc):
    return render_template("store/error.html", code=400, message=exc.description), 400
