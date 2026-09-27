"""Read-only page each factory can open (private link) to see its stock, sales and payouts."""
from flask import Blueprint, abort, render_template

from .db import get_db
from .reports import supplier_statement

bp = Blueprint("supplier", __name__, url_prefix="/supplier")


@bp.route("/<token>")
def portal(token):
    db = get_db()
    supplier = db.execute("SELECT * FROM suppliers WHERE portal_token = ?", (token,)).fetchone()
    if supplier is None:
        abort(404)
    return render_template("supplier/portal.html", supplier=supplier, st=supplier_statement(db, supplier["id"]), zh=True)
