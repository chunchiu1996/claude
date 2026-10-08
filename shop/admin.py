import csv
import io
import secrets

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import inquiries as inq
from . import notify, orders, photos, seller
from .catalog import (
    PRODUCT_SELECT,
    categories_with_counts,
    get_or_create_category,
    get_tiers,
    normalize_specs,
    record_movement,
    set_tiers,
    slugify,
    unique_slug,
)
from .db import get_db
from .importer import COLUMNS, import_rows, read_upload, template_csv, template_xlsx
from .pricing import format_tiers, parse_money, parse_tiers
from .reports import dashboard_stats, supplier_statement

bp = Blueprint("admin", __name__, url_prefix="/admin")

LOW_STOCK = 10
PER_PAGE = 50
# Password guessing: after this many wrong passwords from one IP, logins from it are refused for a while.
LOGIN_MAX_FAILURES, LOGIN_WINDOW_MINUTES = 5, 15


@bp.before_request
def require_login():
    if request.endpoint != "admin.login" and not session.get("admin"):
        return redirect(url_for("admin.login", next=request.path))


def _client_ip():
    header = current_app.config.get("CLIENT_IP_HEADER")
    return (header and request.headers.get(header, "").strip()) or request.remote_addr or "?"


def _recent_failures(db, ip):
    window = f"-{LOGIN_WINDOW_MINUTES} minutes"
    db.execute("DELETE FROM login_attempts WHERE created_at < datetime('now', ?)", (window,))
    return db.execute("SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND created_at >= datetime('now', ?)",
                      (ip, window)).fetchone()[0]


@bp.route("/login", methods=["GET", "POST"])
def login():
    password = current_app.config.get("ADMIN_PASSWORD")
    if request.method == "POST" and password:
        db = get_db()
        ip = _client_ip()
        if _recent_failures(db, ip) >= LOGIN_MAX_FAILURES:
            db.commit()
            flash(f"Too many wrong passwords. Try again in {LOGIN_WINDOW_MINUTES} minutes.", "error")
            return render_template("admin/login.html", enabled=True), 429
        if secrets.compare_digest(request.form.get("password", "").encode(), password.encode()):
            db.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
            db.commit()
            csrf = session.get("_csrf")
            session.clear()
            session.update(admin=True, _csrf=csrf)
            target = request.args.get("next", "")
            return redirect(target if target.startswith("/admin") else url_for("admin.dashboard"))
        db.execute("INSERT INTO login_attempts (ip) VALUES (?)", (ip,))
        db.commit()
        current_app.logger.warning("Wrong admin password from %s", ip)
        flash("Wrong password.", "error")
    return render_template("admin/login.html", enabled=bool(password))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("store.home"))


@bp.route("/")
def dashboard():
    db = get_db()
    orders.release_stale_card_orders(db)
    return render_template(
        "admin/dashboard.html",
        stats=dashboard_stats(db),
        recent=db.execute("SELECT * FROM orders ORDER BY created_at DESC, id DESC LIMIT 8").fetchall(),
        low_stock=db.execute(
            PRODUCT_SELECT + " WHERE p.active = 1 AND p.stock_qty <= ? ORDER BY p.stock_qty LIMIT 10", (LOW_STOCK,)
        ).fetchall(),
        inquiries=db.execute("SELECT * FROM inquiries WHERE status = 'new' ORDER BY created_at DESC LIMIT 5").fetchall(),
    )


# ---------------------------------------------------------------- products


@bp.route("/products")
def products():
    db = get_db()
    q = request.args.get("q", "").strip()
    supplier_id = request.args.get("supplier", type=int)
    where, params = ["1 = 1"], []
    if q:
        where.append("(p.name LIKE ? OR p.sku LIKE ?)")
        params += [f"%{q}%"] * 2
    if supplier_id:
        where.append("p.supplier_id = ?")
        params.append(supplier_id)
    if request.args.get("low") == "1":
        where.append(f"p.stock_qty <= {LOW_STOCK}")
    if request.args.get("nophoto") == "1":
        where.append("COALESCE(p.image_url, '') = ''")
    where_sql = " WHERE " + " AND ".join(where)
    total = db.execute(
        "SELECT COUNT(*) FROM products p LEFT JOIN categories c ON c.id = p.category_id" + where_sql, params
    ).fetchone()[0]
    pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    page = min(max(request.args.get("page", 1, type=int), 1), pages)
    rows = db.execute(
        PRODUCT_SELECT + where_sql + " ORDER BY p.active DESC, c.sort_order, p.name LIMIT ? OFFSET ?",
        [*params, PER_PAGE, (page - 1) * PER_PAGE],
    ).fetchall()
    return render_template(
        "admin/products.html", products=rows, q=q, supplier_id=supplier_id, suppliers=_suppliers(db),
        low_stock=LOW_STOCK, total=total, page=page, pages=pages,
        page_url=lambda n: url_for("admin.products", **{**request.args.to_dict(), "page": n}),
    )


def _suppliers(db):
    return db.execute("SELECT id, name FROM suppliers ORDER BY name").fetchall()


def _form_from_product(p, tiers):
    money = lambda c: "" if c is None else f"{c / 100:.2f}"  # noqa: E731
    return {
        "sku": p["sku"], "name": p["name"], "category_id": str(p["category_id"] or ""),
        "supplier_id": str(p["supplier_id"] or ""), "description": p["description"] or "",
        "specs": p["specs"] or "", "unit": p["unit"], "unit_note": p["unit_note"] or "",
        "price": "" if p["quote_only"] and not p["price_cents"] else money(p["price_cents"]), "compare_at": money(p["compare_at_cents"]), "moq": str(p["moq"]),
        "warehouse": p["warehouse"] or "", "image_url": p["image_url"] or "", "tiers": format_tiers(tiers),
        "active": "1" if p["active"] else "", "featured": "1" if p["featured"] else "",
        "quote_only": "1" if p["quote_only"] else "",
    }


def _parse_product_form(db, form, product_id=None):
    errors, data, tiers = {}, {}, []
    data["sku"] = form.get("sku", "").strip()
    data["name"] = form.get("name", "").strip()
    if not data["sku"]:
        errors["sku"] = "Required."
    elif db.execute(
        "SELECT 1 FROM products WHERE sku = ? COLLATE NOCASE AND id IS NOT ?", (data["sku"], product_id)
    ).fetchone():
        errors["sku"] = "Another product already uses this SKU."
    if not data["name"]:
        errors["name"] = "Required."
    for field in ("description", "unit_note", "warehouse", "image_url"):
        data[field] = form.get(field, "").strip() or None
    data["specs"] = normalize_specs(form.get("specs", "")) or None
    data["unit"] = form.get("unit", "").strip() or "piece"
    data["quote_only"] = 1 if form.get("quote_only") else 0
    try:
        data["price_cents"] = parse_money(form.get("price")) or 0
        if not data["price_cents"] and not data["quote_only"]:
            errors["price"] = "Enter a price, or tick “Quote only” to hide the price."
    except ValueError as exc:
        errors["price"] = str(exc)
    try:
        data["compare_at_cents"] = parse_money(form.get("compare_at"))
    except ValueError as exc:
        errors["compare_at"] = str(exc)
    try:
        data["moq"] = max(int(form.get("moq") or 1), 1)
    except ValueError:
        errors["moq"] = "Must be a whole number."
    try:
        tiers = parse_tiers(form.get("tiers", ""))
    except ValueError as exc:
        errors["tiers"] = str(exc)
    data["active"] = 1 if form.get("active") else 0
    data["featured"] = 1 if form.get("featured") else 0
    data["supplier_id"] = form.get("supplier_id", type=int)
    if form.get("new_category", "").strip():
        data["category_id"] = get_or_create_category(db, form["new_category"])
    else:
        data["category_id"] = form.get("category_id", type=int)

    if data["image_url"] and not data["image_url"].startswith(("https://", "http://", "/media/")):
        errors["image_url"] = "Use a full web address starting with https://"
    return data, tiers, errors


def _save_uploads(errors):
    """Main photo + extra gallery photos from the product form. Only called once the form is valid."""
    saved = {"main": None, "extra": []}
    main = request.files.get("image")
    try:
        if main and main.filename:
            saved["main"] = photos.save_image(main.read())
        for upload in request.files.getlist("extra_images"):
            if upload and upload.filename:
                saved["extra"].append(photos.save_image(upload.read()))
    except ValueError as exc:
        errors["image"] = str(exc)
        for url in [saved["main"], *saved["extra"]]:
            photos.delete_media(url)
    return saved


def _store_uploads(db, product_id, saved):
    if saved["main"]:
        photos.set_photo(db, product_id, 1, saved["main"])
    if saved["extra"]:
        start = (db.execute("SELECT MAX(position) FROM product_images WHERE product_id = ?",
                            (product_id,)).fetchone()[0] or 1) + 1
        for position, url in enumerate(saved["extra"], start=start):
            photos.set_photo(db, product_id, position, url)


def _product_form_context(db):
    return {"categories": categories_with_counts(db), "suppliers": _suppliers(db)}


@bp.route("/products/new", methods=["GET", "POST"])
def product_new():
    db = get_db()
    form = {"unit": "piece", "moq": "1", "active": "1"}
    errors = {}
    if request.method == "POST":
        form = request.form.to_dict()
        data, tiers, errors = _parse_product_form(db, request.form)
        try:
            initial_stock = int(request.form.get("initial_stock") or 0)
            if initial_stock < 0:
                raise ValueError
        except ValueError:
            errors["initial_stock"] = "Must be a whole number of 0 or more."
        saved = _save_uploads(errors) if not errors else None
        if not errors:
            data["slug"] = unique_slug(db, "products", slugify(data["name"]), alt=slugify(data["sku"]))
            cols = ", ".join(data)
            product_id = db.execute(
                f"INSERT INTO products ({cols}) VALUES ({', '.join('?' for _ in data)})", list(data.values())
            ).lastrowid
            set_tiers(db, product_id, tiers)
            _store_uploads(db, product_id, saved)
            if initial_stock:
                record_movement(db, product_id, initial_stock, "receive", request.form.get("reference", "").strip(),
                                "Initial stock")
            db.commit()
            flash(f"Created {data['name']}.", "success")
            return redirect(url_for("admin.product_edit", product_id=product_id))
        db.rollback()
    return render_template("admin/product_form.html", form=form, errors=errors, product=None, **_product_form_context(db))


@bp.route("/products/<int:product_id>", methods=["GET", "POST"])
def product_edit(product_id):
    db = get_db()
    product = db.execute(PRODUCT_SELECT + " WHERE p.id = ?", (product_id,)).fetchone()
    if product is None:
        return redirect(url_for("admin.products"))
    errors = {}
    form = _form_from_product(product, get_tiers(db, product_id))
    if request.method == "POST":
        form = request.form.to_dict()
        data, tiers, errors = _parse_product_form(db, request.form, product_id)
        saved = _save_uploads(errors) if not errors else None
        if not errors:
            old_image = product["image_url"]
            if not data["image_url"] and old_image and not request.form.get("remove_image"):
                data["image_url"] = old_image
            if data["name"] != product["name"]:
                data["slug"] = unique_slug(db, "products", slugify(data["name"]), exclude_id=product_id,
                                          alt=slugify(data["sku"]))
            assignments = ", ".join(f"{col} = ?" for col in data)
            db.execute(
                f"UPDATE products SET {assignments}, updated_at = datetime('now') WHERE id = ?",
                [*data.values(), product_id],
            )
            if old_image != data["image_url"]:
                photos.delete_media(old_image)
            for position in request.form.getlist("remove_photo", type=int):
                photos.remove_photo(db, product_id, position)
            set_tiers(db, product_id, tiers)
            _store_uploads(db, product_id, saved)
            db.commit()
            flash("Saved.", "success")
            return redirect(url_for("admin.product_edit", product_id=product_id))
        db.rollback()
    movements = db.execute(
        "SELECT * FROM stock_movements WHERE product_id = ? ORDER BY created_at DESC, id DESC LIMIT 30", (product_id,)
    ).fetchall()
    return render_template(
        "admin/product_form.html", form=form, errors=errors, product=product, movements=movements,
        gallery=photos.gallery(db, product_id), **_product_form_context(db)
    )


@bp.post("/products/<int:product_id>/stock")
def product_stock(product_id):
    db = get_db()
    kind = request.form.get("kind")
    reference = request.form.get("reference", "").strip()
    note = request.form.get("note", "").strip()
    try:
        qty = request.form.get("qty", type=int)
        if qty is None:
            raise ValueError("Enter a whole-number quantity.")
        if kind == "receive" and qty <= 0:
            raise ValueError("Received quantity must be positive.")
        if kind == "adjust" and (qty == 0 or not note):
            raise ValueError("Adjustments need a non-zero quantity and a reason (damaged, recount, …).")
        if kind not in ("receive", "adjust"):
            raise ValueError("Unknown stock action.")
        record_movement(db, product_id, qty, kind, reference, note)
        db.commit()
        flash(f"Stock updated ({qty:+d}).", "success")
    except ValueError as exc:
        db.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.product_edit", product_id=product_id) + "#stock")


@bp.route("/import", methods=["GET", "POST"])
def import_view():
    result, picture_results = None, []
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            flash("Choose an Excel or CSV file to upload.", "error")
        else:
            raw = upload.read()
            try:
                rows = read_upload(upload.filename, raw)
            except ValueError as exc:
                flash(str(exc), "error")
            else:
                db = get_db()
                result = import_rows(
                    db,
                    rows,
                    reference=request.form.get("reference", "").strip() or None,
                    stock_mode="set" if request.form.get("stock_mode") == "set" else "add",
                )
                if result.ok and upload.filename.lower().endswith((".xlsx", ".xlsm")):
                    picture_results = photos.excel_photos(db, raw, rows)
    return render_template("admin/import.html", result=result, columns=COLUMNS, pictures=picture_results)


# ---------------------------------------------------------------- photos


@bp.route("/photos")
def photos_view():
    db = get_db()
    missing = db.execute(
        "SELECT id, sku, name FROM products WHERE active = 1 AND COALESCE(image_url, '') = '' ORDER BY sku"
    ).fetchall()
    total = db.execute("SELECT COUNT(*) FROM products WHERE active = 1").fetchone()[0]
    return render_template("admin/photos.html", missing=missing[:200], missing_count=len(missing), total=total,
                           max_mb=current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024))


@bp.post("/photos/upload")
def photos_upload():
    """Bulk photo upload. The page sends one file per request (so big folders never hit size limits);
    a .zip of photos works too. Each photo goes to the product whose SKU matches its file name."""
    db = get_db()
    results = []
    for upload in request.files.getlist("files"):
        if not upload or not upload.filename:
            continue
        raw = upload.read()
        if upload.filename.lower().endswith(".zip"):
            results += photos.attach_zip(db, raw)
        else:
            results.append(photos.attach_file(db, upload.filename, raw))
    if request.accept_mimetypes.best == "application/json" or request.form.get("format") == "json":
        return jsonify(results=results)
    ok = sum(r["status"] == "ok" for r in results)
    flash(f"{ok} of {len(results)} photos saved.", "success" if ok == len(results) else "error")
    for r in results:
        if r["status"] != "ok":
            flash(f"{r['file']}: {r['message']}", "error")
    return redirect(url_for("admin.photos_view"))


@bp.route("/import/template.csv")
def import_template():
    return Response(template_csv(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=product-import-template.csv"})


@bp.route("/import/template.xlsx")
def import_template_xlsx():
    return Response(
        template_xlsx(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=product-import-template.xlsx"},
    )


@bp.route("/export/products.csv")
def export_products():
    db = get_db()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    for p in db.execute(PRODUCT_SELECT + " ORDER BY p.sku").fetchall():
        writer.writerow([
            p["sku"], p["name"], p["category_name"] or "", p["supplier_name"] or "",
            "" if p["quote_only"] and not p["price_cents"] else f"{p['price_cents'] / 100:.2f}",
            "" if p["compare_at_cents"] is None else f"{p['compare_at_cents'] / 100:.2f}", p["unit"],
            p["unit_note"] or "", p["moq"], p["stock_qty"], p["warehouse"] or "",
            format_tiers(get_tiers(db, p["id"])), "yes" if p["quote_only"] else "no", p["description"] or "", (p["specs"] or "").replace("\n", "; "),
            p["image_url"] or "", "yes" if p["featured"] else "no", "yes" if p["active"] else "no",
        ])
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=products.csv"})


# ---------------------------------------------------------------- categories


@bp.route("/categories", methods=["GET", "POST"])
def categories():
    db = get_db()
    if request.method == "POST":
        cat_id = request.form.get("id", type=int)
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        sort_order = request.form.get("sort_order", 100, type=int)
        if not name:
            flash("Category name is required.", "error")
        elif db.execute("SELECT 1 FROM categories WHERE name = ? COLLATE NOCASE AND id IS NOT ?", (name, cat_id)).fetchone():
            flash("A category with that name already exists.", "error")
        elif cat_id:
            db.execute("UPDATE categories SET name = ?, description = ?, sort_order = ? WHERE id = ?",
                       (name, description, sort_order, cat_id))
            db.commit()
            flash("Category saved.", "success")
        else:
            db.execute("INSERT INTO categories (name, slug, description, sort_order) VALUES (?, ?, ?, ?)",
                       (name, unique_slug(db, "categories", slugify(name)), description, sort_order))
            db.commit()
            flash("Category added.", "success")
        return redirect(url_for("admin.categories"))
    return render_template("admin/categories.html", categories=categories_with_counts(db))


# ---------------------------------------------------------------- orders


@bp.route("/orders")
def orders_list():
    db = get_db()
    orders.release_stale_card_orders(db)
    status = request.args.get("status", "")
    if status in orders.STATUSES:
        rows = db.execute("SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC, id DESC", (status,)).fetchall()
    else:
        status = ""
        rows = db.execute("SELECT * FROM orders ORDER BY created_at DESC, id DESC LIMIT 200").fetchall()
    return render_template("admin/orders.html", orders=rows, status=status, statuses=orders.STATUSES)


@bp.route("/orders/<int:order_id>", methods=["GET", "POST"])
def order_detail(order_id):
    db = get_db()
    order = orders.get_order(db, order_id)
    if order is None:
        return redirect(url_for("admin.orders_list"))
    if request.method == "POST":
        if "admin_note" in request.form:
            db.execute("UPDATE orders SET admin_note = ?, updated_at = datetime('now') WHERE id = ?",
                       (request.form["admin_note"].strip() or None, order_id))
            db.commit()
            flash("Note saved.", "success")
        if request.form.get("status"):
            try:
                orders.set_status(db, order_id, request.form["status"])
                flash(f"Order marked {orders.STATUSES[request.form['status']].lower()}.", "success")
            except ValueError as exc:
                flash(str(exc), "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))
    return render_template(
        "admin/order_detail.html", order=order, items=orders.get_items(db, order_id), statuses=orders.STATUSES,
        email_ready=notify.email_enabled(),
    )


@bp.route("/orders/<int:order_id>/invoice")
def order_invoice(order_id):
    db = get_db()
    order = orders.get_order(db, order_id)
    if order is None:
        abort(404)
    return render_template("invoice.html", order=order, items=orders.get_items(db, order_id),
                           payment_instructions=current_app.config["PAYMENT_INSTRUCTIONS"], admin_view=True)


@bp.post("/orders/<int:order_id>/email-invoice")
def order_email_invoice(order_id):
    db = get_db()
    order = orders.get_order(db, order_id)
    if order is None:
        abort(404)
    error = notify.send_invoice(order, orders.get_items(db, order_id))
    if error:
        flash(f"Invoice not sent: {error}", "error")
    else:
        db.execute("UPDATE orders SET invoice_sent_at = datetime('now') WHERE id = ?", (order_id,))
        db.commit()
        flash(f"Invoice emailed to {order['email']}.", "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


# ---------------------------------------------------------------- email settings


@bp.route("/email", methods=["GET", "POST"])
def email_settings():
    cfg = current_app.config
    if request.method == "POST":
        to = request.form.get("to", "").strip()
        if "@" not in to:
            flash("Enter the address to send the test email to.", "error")
        else:
            error = notify.send_test(to)
            if error:
                flash(f"Test email failed: {error}", "error")
            else:
                flash(f"Test email sent to {to}. Check the inbox (and spam folder).", "success")
        return redirect(url_for("admin.email_settings"))
    settings = {
        "SMTP_HOST": cfg.get("SMTP_HOST") or "",
        "SMTP_PORT": cfg.get("SMTP_PORT"),
        "SMTP_USER": cfg.get("SMTP_USER") or "",
        "SMTP_PASSWORD": "set" if cfg.get("SMTP_PASSWORD") else "",
        "SMTP_FROM": notify.from_address(),
        "NOTIFY_EMAIL": notify.admin_address(),
        "CONTACT_EMAIL": cfg.get("CONTACT_EMAIL"),
        "PAYMENT_INSTRUCTIONS": cfg.get("PAYMENT_INSTRUCTIONS") or "",
    }
    return render_template("admin/email.html", settings=settings, enabled=notify.email_enabled(),
                           default_to=notify.admin_address())


# ---------------------------------------------------------------- inquiries


@bp.route("/inquiries", methods=["GET", "POST"])
def inquiries():
    db = get_db()
    if request.method == "POST":
        _set_inquiry_status(db, request.form.get("id", type=int))
        return redirect(request.full_path if request.args else url_for("admin.inquiries"))
    kind = request.args.get("kind", "")
    sql = """SELECT i.*, (SELECT COUNT(*) FROM inquiry_items ii WHERE ii.inquiry_id = i.id) AS item_count
             FROM inquiries i"""
    if kind in ("quote", "supplier"):
        rows = db.execute(sql + " WHERE i.kind = ? ORDER BY i.created_at DESC, i.id DESC", (kind,)).fetchall()
    else:
        kind = ""
        rows = db.execute(sql + " ORDER BY i.created_at DESC, i.id DESC LIMIT 200").fetchall()
    return render_template("admin/inquiries.html", inquiries=rows, kind=kind, contact_prefs=inq.CONTACT_PREFS)


def _set_inquiry_status(db, inquiry_id):
    status = request.form.get("status")
    if status in inq.STATUSES:
        db.execute("UPDATE inquiries SET status = ? WHERE id = ?", (status, inquiry_id))
        db.commit()


@bp.route("/inquiries/<int:inquiry_id>", methods=["GET", "POST"])
def inquiry_detail(inquiry_id):
    db = get_db()
    inquiry = inq.get(db, inquiry_id)
    if inquiry is None:
        return redirect(url_for("admin.inquiries"))
    if request.method == "POST":
        _set_inquiry_status(db, inquiry_id)
        flash("Status updated.", "success")
        return redirect(url_for("admin.inquiry_detail", inquiry_id=inquiry_id))
    items = inq.get_items(db, inquiry_id)
    if inquiry["kind"] == "quote":
        draft = inq.reply_draft(db, notify.site_info(), inquiry, items)
        subject = f"Your quote {inquiry['reference']} – {current_app.config['SITE_NAME']}"
    else:  # factory application from the Chinese seller site: reply in Chinese
        site = seller.seller_site()
        draft = inq.seller_reply_draft(site, inquiry, seller.seller_url("seller.product_template"))
        subject = f"合作申请 {inquiry['reference']} – {site['name']}"
    return render_template(
        "admin/inquiry_detail.html", inquiry=inquiry, items=items, draft=draft, statuses=inq.STATUSES,
        contact_prefs=inq.CONTACT_PREFS, subject=subject,
        whatsapp_base=notify.whatsapp_link(inquiry["phone"], draft),
        mailto_base=notify.mailto_link(inquiry["email"], subject, draft),
        public_url=url_for("store.quote_request", reference=inquiry["reference"], token=inquiry["access_token"])
        if inquiry["reference"] and inquiry["access_token"] and inquiry["kind"] == "quote" else None,
    )


# ---------------------------------------------------------------- seller campaigns (EDM)


@bp.route("/campaigns")
def campaigns():
    """Tracked links and a ready-to-send Chinese email for recruiting factories / stock owners."""
    from datetime import date

    campaign = request.args.get("campaign", "").strip() or f"factory-invite-{date.today():%Y%m}"
    campaign = "".join(ch for ch in campaign if ch.isalnum() or ch in "-_")[:60]
    landing = seller.landing_url(campaign)
    template_link = seller.seller_url("seller.product_template")
    email_html = render_template("seller/edm_email.html", seller=seller.seller_site(), landing_url=landing,
                                 template_url=template_link, apply_url=seller.seller_url(
                                     "seller.apply", utm_source="edm", utm_campaign=campaign))
    db = get_db()
    sources = db.execute(
        """SELECT COALESCE(source, 'direct') AS source, COUNT(*) AS applications,
                  SUM(status IN ('won')) AS signed, MAX(created_at) AS latest
           FROM inquiries WHERE kind = 'supplier' GROUP BY COALESCE(source, 'direct') ORDER BY latest DESC"""
    ).fetchall()
    return render_template("admin/campaigns.html", campaign=campaign, landing=landing, email_html=email_html,
                           seller_home=seller.seller_url("seller.home"), sources=sources)


# ---------------------------------------------------------------- suppliers


@bp.route("/suppliers")
def suppliers():
    db = get_db()
    rows = []
    for s in db.execute("SELECT * FROM suppliers ORDER BY name").fetchall():
        st = supplier_statement(db, s["id"])
        rows.append({"s": s, "st": st, "sku_count": len(st["products"]),
                     "units": sum(p["stock_qty"] for p in st["products"])})
    return render_template("admin/suppliers.html", rows=rows)


def _parse_supplier_form(db, supplier_id=None):
    f = request.form
    data = {k: f.get(k, "").strip() or None
            for k in ("name", "location", "contact_name", "contact_email", "contact_phone", "notes")}
    errors = {}
    if not data["name"]:
        errors["name"] = "Required."
    elif db.execute("SELECT 1 FROM suppliers WHERE name = ? COLLATE NOCASE AND id IS NOT ?", (data["name"], supplier_id)).fetchone():
        errors["name"] = "A supplier with this name already exists."
    try:
        rate = float(f.get("commission_pct", "20")) / 100
        if not 0 <= rate < 1:
            raise ValueError
        data["commission_rate"] = rate
    except ValueError:
        errors["commission_pct"] = "Enter a percentage between 0 and 99."
    return data, errors


@bp.route("/suppliers/new", methods=["GET", "POST"])
@bp.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
def supplier_form(supplier_id=None):
    db = get_db()
    supplier = db.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone() if supplier_id else None
    errors = {}
    form = dict(supplier) if supplier else {"commission_rate": 0.2}
    form["commission_pct"] = f"{form['commission_rate'] * 100:g}"
    if request.method == "POST":
        form = request.form.to_dict()
        data, errors = _parse_supplier_form(db, supplier_id)
        if not errors:
            if supplier:
                if request.form.get("new_link"):
                    data["portal_token"] = secrets.token_urlsafe(18)
                db.execute(f"UPDATE suppliers SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?",
                           [*data.values(), supplier_id])
            else:
                data["portal_token"] = secrets.token_urlsafe(18)
                supplier_id = db.execute(
                    f"INSERT INTO suppliers ({', '.join(data)}) VALUES ({', '.join('?' for _ in data)})",
                    list(data.values()),
                ).lastrowid
            db.commit()
            flash("Supplier saved.", "success")
            return redirect(url_for("admin.supplier_detail", supplier_id=supplier_id))
    return render_template("admin/supplier_form.html", form=form, errors=errors, supplier=supplier)


@bp.route("/suppliers/<int:supplier_id>", methods=["GET", "POST"])
def supplier_detail(supplier_id):
    db = get_db()
    supplier = db.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
    if supplier is None:
        return redirect(url_for("admin.suppliers"))
    if request.method == "POST":
        try:
            amount = parse_money(request.form.get("amount"))
            if not amount:
                raise ValueError("Enter a payout amount.")
            db.execute("INSERT INTO payouts (supplier_id, amount_cents, reference, note) VALUES (?, ?, ?, ?)",
                       (supplier_id, amount, request.form.get("reference", "").strip() or None,
                        request.form.get("note", "").strip() or None))
            db.commit()
            flash("Payout recorded.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin.supplier_detail", supplier_id=supplier_id))
    return render_template(
        "admin/supplier_detail.html",
        supplier=supplier,
        st=supplier_statement(db, supplier_id),
        portal_url=seller.seller_url("seller.portal", token=supplier["portal_token"]),
    )
