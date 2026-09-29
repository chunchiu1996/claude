"""Chinese site for recruiting factories and stock owners (中文招商站), at /zh/.

It is deliberately separate from the English store: its own layout, look and navigation, and no
links between the two. It is the landing page for email (EDM) campaigns: utm_ parameters on the
incoming link are remembered and saved with the application so each campaign can be measured.
"""
from flask import Blueprint, Response, abort, current_app, redirect, render_template, request, session, url_for

from . import inquiries as inq
from . import notify
from .db import get_db
from .importer import template_xlsx
from .reports import supplier_statement

bp = Blueprint("seller", __name__, url_prefix="/zh")

CATEGORIES = ["地板", "瓷砖 / 石材", "橱柜 / 浴室柜", "水龙头 / 卫浴", "灯具照明", "门 / 五金", "台面（石英石、岩板）", "其他建材"]
STOCK_STATUS = ["已在美国仓库", "已发货，在途中", "在国内，计划发往美国"]
CONTACT_PREFS = {"wechat": "微信", "whatsapp": "WhatsApp", "email": "邮箱", "phone": "电话"}
UNIT_ZH = {"box": "箱", "piece": "件", "set": "套", "sheet": "片", "slab": "块", "carton": "箱", "pallet": "托",
           "roll": "卷", "pair": "对", "bag": "袋", "4-pack": "盒"}
MOVEMENT_ZH = {"receive": "入库", "sale": "售出", "cancel": "取消退回", "adjust": "调整"}


def seller_site():
    cfg = current_app.config
    return {
        "name": cfg.get("SELLER_SITE_NAME") or cfg["SITE_NAME"],
        "email": cfg["CONTACT_EMAIL"],
        "phone": cfg["CONTACT_PHONE"],
        "warehouse": cfg["WAREHOUSE_ADDRESS"],
        "wechat": cfg.get("WECHAT_ID") or "",
        "whatsapp": cfg.get("WHATSAPP_NUMBER") or "",
    }


@bp.app_template_filter("unit_zh")
def unit_zh(unit):
    return UNIT_ZH.get((unit or "").lower(), unit or "")


def seller_url(endpoint, **values):
    """Absolute link to a Chinese-site page. With SELLER_SITE_DOMAIN set it always uses that domain
    (e.g. https://partner.liquidatorusa.com/apply), so links never reveal the English store's address."""
    domain = current_app.config.get("SELLER_SITE_DOMAIN")
    if not domain:
        return url_for(endpoint, _external=True, **values)
    path = url_for(endpoint, **values)
    path = "/" + path[len("/zh/"):] if path.startswith("/zh/") else path
    return f"{request.scheme}://{domain}{path}"


@bp.app_context_processor
def inject_seller_url():
    return {"seller_url": seller_url}


@bp.before_request
def stay_on_seller_domain():
    """With its own domain configured, the Chinese site is only served there: redirect other hosts."""
    domain = current_app.config.get("SELLER_SITE_DOMAIN")
    if (domain and request.method in ("GET", "HEAD") and request.endpoint
            and request.host.split(":")[0].lower() != domain.lower()):
        return redirect(seller_url(request.endpoint, **(request.view_args or {}), **request.args.to_dict()), 301)


@bp.before_request
def remember_campaign():
    """Keep the campaign a visitor arrived from (e.g. ?utm_source=edm&utm_campaign=oct-invite)."""
    source = " / ".join(v for v in (request.args.get("utm_source"), request.args.get("utm_campaign")) if v)
    if source:
        session["seller_source"] = source[:120]


@bp.context_processor
def inject_site():
    return {"seller": seller_site()}


@bp.route("/")
def home():
    return render_template("seller/home.html", categories=CATEGORIES)


@bp.route("/apply", methods=["GET", "POST"])
def apply():
    data, errors = {"contact_pref": "wechat", "stock_status": STOCK_STATUS[0]}, {}
    if request.method == "POST":
        f = request.form
        data = {k: f.get(k, "").strip() for k in
                ("company", "name", "email", "phone", "contact_pref", "stock_status", "location", "items", "message")}
        data["categories"] = [c for c in f.getlist("categories") if c in CATEGORIES]
        for key, label in (("company", "公司名称"), ("name", "联系人"), ("items", "产品和数量")):
            if not data[key]:
                errors[key] = f"请填写{label}。"
        if not data["email"] and not data["phone"]:
            errors["email"] = "请至少留下邮箱或微信 / 电话，方便我们联系您。"
        elif data["email"] and ("@" not in data["email"] or "." not in data["email"].split("@")[-1]):
            errors["email"] = "邮箱格式不正确。"
        if data["contact_pref"] not in CONTACT_PREFS:
            data["contact_pref"] = "wechat"
        if data["contact_pref"] != "email" and not data["phone"]:
            errors["phone"] = f"请填写您的{CONTACT_PREFS[data['contact_pref']]}号码。"
        if not errors:
            where = data["stock_status"] if data["stock_status"] in STOCK_STATUS else ""
            items = data["items"]
            if data["categories"]:
                items = f"品类：{'、'.join(data['categories'])}\n{items}"
            inquiry = inq.create(get_db(), "supplier", {
                **data,
                "role": "Factory / stock owner",
                "location": " · ".join(v for v in (where, data["location"]) if v),
                "items": items,
                "source": session.get("seller_source") or "direct",
            })
            notify.new_inquiry(inquiry, [])
            return render_template("seller/thanks.html", inquiry=inquiry)
    return render_template("seller/apply.html", data=data, errors=errors, categories=CATEGORIES,
                           stock_status=STOCK_STATUS, contact_prefs=CONTACT_PREFS)


@bp.route("/template.xlsx")
def product_template():
    """The bilingual product spreadsheet factories fill in (same file as Admin → Import)."""
    return Response(
        template_xlsx(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=product-list-template.xlsx"},
    )


@bp.route("/partner/<token>")
def portal(token):
    """A factory's private consignment report: stock, sales, earnings and payouts."""
    db = get_db()
    supplier = db.execute("SELECT * FROM suppliers WHERE portal_token = ?", (token,)).fetchone()
    if supplier is None:
        abort(404)
    return render_template("seller/portal.html", supplier=supplier, st=supplier_statement(db, supplier["id"]),
                           movement_zh=MOVEMENT_ZH)


@bp.app_errorhandler(404)
def not_found(exc):
    """404 page for both sites, in the language of the site that was requested."""
    if request.path.startswith("/zh/") or request.path == "/zh":
        return render_template("seller/error.html", seller=seller_site()), 404
    return render_template("store/error.html", code=404, message="We couldn't find that page."), 404


class SellerDomainMiddleware:
    """Serve the Chinese site at the root of its own domain (SELLER_SITE_DOMAIN), e.g. partner.example.com/."""

    PASSTHROUGH = ("/zh/", "/static/", "/media/")

    def __init__(self, app, domain):
        self.app = app
        self.domain = domain.lower().strip()

    def __call__(self, environ, start_response):
        host = (environ.get("HTTP_HOST") or "").split(":")[0].lower()
        path = environ.get("PATH_INFO") or "/"
        if host == self.domain and path != "/zh" and not path.startswith(self.PASSTHROUGH):
            environ["PATH_INFO"] = "/zh" + path
        return self.app(environ, start_response)


def landing_url(campaign, source="edm"):
    """Absolute link to the Chinese landing page, tagged for campaign tracking."""
    return seller_url("seller.home", utm_source=source, utm_campaign=campaign)
