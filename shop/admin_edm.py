"""Admin → Campaigns → AI drafts: the review queue for AI-written marketing emails (see edm.py)."""
from datetime import date

from flask import Response, abort, current_app, flash, redirect, render_template, request, url_for

from . import edm, notify
from .admin import bp
from .db import get_db


def _draft_or_404(db, draft_id):
    draft = edm.get(db, draft_id)
    if draft is None:
        abort(404)
    return draft


@bp.route("/campaigns/drafts", methods=["GET", "POST"])
def campaign_drafts():
    db = get_db()
    form = {"audience": request.form.get("audience", "buyer"),
            "campaign": request.form.get("campaign", ""), "brief": request.form.get("brief", "")}
    if request.method == "POST":
        try:
            draft_id = edm.create_draft(db, form["audience"], form["campaign"], form["brief"])
            flash("The AI wrote a draft. Review it below before it can be used.", "success")
            return redirect(url_for("admin.campaign_draft", draft_id=draft_id))
        except edm.AIError as exc:
            flash(str(exc), "error")
    status = request.args.get("status", "")
    where, params = ("WHERE status = ?", (status,)) if status in edm.STATUSES else ("", ())
    drafts = db.execute(f"SELECT * FROM edm_drafts {where} ORDER BY (status = 'review') DESC, updated_at DESC, id DESC",
                        params).fetchall()
    counts = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) AS n FROM edm_drafts GROUP BY status")}
    if not form["campaign"]:
        form["campaign"] = f"newsletter-{date.today():%Y%m}"
    return render_template("admin/edm_drafts.html", drafts=drafts, counts=counts, status=status, form=form,
                           audiences=edm.AUDIENCES, statuses=edm.STATUSES, ai_enabled=edm.ai_enabled(),
                           model=current_app.config.get("EDM_AI_MODEL") or edm.DEFAULT_MODEL)


@bp.route("/campaigns/drafts/<int:draft_id>", methods=["GET", "POST"])
def campaign_draft(draft_id):
    """Review page: edit the copy, see the checks and the preview; save sends an approved draft back to review."""
    db = get_db()
    draft = _draft_or_404(db, draft_id)
    if request.method == "POST":
        if draft["status"] == "sent":
            flash("This email was already sent and can't be changed.", "error")
            return redirect(url_for("admin.campaign_draft", draft_id=draft_id))
        new = {k: request.form.get(k, "").strip() for k in edm.FIELDS}
        new["bullets"] = "\n".join(edm.lines(new["bullets"]))
        new["product_skus"] = "\n".join(edm.lines(new["product_skus"].replace(",", "\n")))
        changed = [k for k in edm.FIELDS if (draft[k] or "") != new[k]]
        if changed:
            back_to_review = draft["status"] in ("approved", "rejected")
            db.execute(f"UPDATE edm_drafts SET {', '.join(f'{k} = ?' for k in changed)}, status = 'review', "
                       "approved_at = NULL, updated_at = datetime('now') WHERE id = ?",
                       [*(new[k] for k in changed), draft_id])
            edm.log(db, draft_id, "edited", "Changed: " + ", ".join(k.replace("_", " ") for k in changed)
                    + (" (needs approval again)" if back_to_review else ""))
            db.commit()
            flash("Saved." + (" It needs approving again." if back_to_review else ""), "success")
        else:
            flash("Nothing changed.", "message")
        return redirect(url_for("admin.campaign_draft", draft_id=draft_id))

    results = edm.checks(db, draft)
    return render_template(
        "admin/edm_draft.html", draft=draft, checks=results, blocking=edm.blocking(results),
        events=edm.events(db, draft_id), email_html=edm.render_html(db, draft), audiences=edm.AUDIENCES,
        statuses=edm.STATUSES, notes=edm.lines(draft["ai_notes"]), ai_enabled=edm.ai_enabled(),
        subject_options=edm.lines(draft["subject_options"]), default_to=notify.admin_address(),
        email_enabled=notify.email_enabled(),
    )


@bp.post("/campaigns/drafts/<int:draft_id>/revise")
def campaign_draft_revise(draft_id):
    db = get_db()
    draft = _draft_or_404(db, draft_id)
    if draft["status"] == "sent":
        abort(400, "This email was already sent.")
    try:
        edm.revise_draft(db, draft, request.form.get("feedback"))
        flash("The AI rewrote the draft. Review the changes.", "success")
    except edm.AIError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.campaign_draft", draft_id=draft_id))


@bp.post("/campaigns/drafts/<int:draft_id>/approve")
def campaign_draft_approve(draft_id):
    db = get_db()
    draft = _draft_or_404(db, draft_id)
    reviewer = request.form.get("reviewed_by", "").strip()[:80]
    problems = edm.blocking(edm.checks(db, draft))
    if draft["status"] != "review":
        flash("Only a draft that is waiting for review can be approved.", "error")
    elif problems:
        flash("Fix these first: " + " ".join(problems), "error")
    elif not reviewer:
        flash("Enter your name so the approval is recorded.", "error")
    elif not request.form.get("confirm"):
        flash("Tick the box to confirm you've read the email and checked its facts and prices.", "error")
    else:
        note = request.form.get("review_note", "").strip() or None
        db.execute("UPDATE edm_drafts SET status = 'approved', reviewed_by = ?, review_note = ?, "
                   "approved_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                   (reviewer, note, draft_id))
        edm.log(db, draft_id, "approved", f"by {reviewer}" + (f": {note}" if note else ""))
        db.commit()
        flash("Approved. You can now copy or download the email for your email tool.", "success")
    return redirect(url_for("admin.campaign_draft", draft_id=draft_id))


@bp.post("/campaigns/drafts/<int:draft_id>/reject")
def campaign_draft_reject(draft_id):
    db = get_db()
    draft = _draft_or_404(db, draft_id)
    if draft["status"] == "sent":
        abort(400, "This email was already sent.")
    reviewer = request.form.get("reviewed_by", "").strip()[:80] or None
    note = request.form.get("review_note", "").strip() or None
    db.execute("UPDATE edm_drafts SET status = 'rejected', reviewed_by = ?, review_note = ?, approved_at = NULL, "
               "updated_at = datetime('now') WHERE id = ?", (reviewer, note, draft_id))
    edm.log(db, draft_id, "rejected", " — ".join(x for x in (f"by {reviewer}" if reviewer else None, note) if x))
    db.commit()
    flash("Rejected. Edit it or ask the AI for a rewrite to send it back for review.", "message")
    return redirect(url_for("admin.campaign_draft", draft_id=draft_id))


@bp.post("/campaigns/drafts/<int:draft_id>/test")
def campaign_draft_test(draft_id):
    """Send the email, marked [TEST], to a staff address. Allowed before approval: that's what it's for."""
    db = get_db()
    draft = _draft_or_404(db, draft_id)
    to = request.form.get("to", "").strip()
    if "@" not in to:
        flash("Enter the address to send the test to.", "error")
        return redirect(url_for("admin.campaign_draft", draft_id=draft_id))
    error = notify.deliver(to, f"[TEST] {draft['subject']}", edm.render_text(db, draft),
                           html=edm.render_html(db, draft))
    if error:
        flash(error, "error")
    else:
        edm.log(db, draft_id, "test sent", to)
        db.commit()
        flash(f"Test sent to {to}.", "success")
    return redirect(url_for("admin.campaign_draft", draft_id=draft_id))


@bp.post("/campaigns/drafts/<int:draft_id>/sent")
def campaign_draft_sent(draft_id):
    db = get_db()
    draft = _draft_or_404(db, draft_id)
    if draft["status"] != "approved":
        flash("Only an approved email can be marked as sent.", "error")
    else:
        note = request.form.get("sent_note", "").strip() or None
        db.execute("UPDATE edm_drafts SET status = 'sent', sent_at = datetime('now'), updated_at = datetime('now') "
                   "WHERE id = ?", (draft_id,))
        edm.log(db, draft_id, "sent", note)
        db.commit()
        flash("Marked as sent.", "success")
    return redirect(url_for("admin.campaign_draft", draft_id=draft_id))


@bp.route("/campaigns/drafts/<int:draft_id>/email.html")
def campaign_draft_export(draft_id):
    """Download the approved email for an email tool. Unapproved drafts can't leave the review queue."""
    db = get_db()
    draft = _draft_or_404(db, draft_id)
    if draft["status"] not in ("approved", "sent"):
        abort(403, "Approve this draft before exporting it.")
    return Response(edm.render_html(db, draft), mimetype="text/html", headers={
        "Content-Disposition": f"attachment; filename={draft['campaign']}-{draft_id}.html"})


@bp.post("/campaigns/drafts/<int:draft_id>/delete")
def campaign_draft_delete(draft_id):
    db = get_db()
    draft = _draft_or_404(db, draft_id)
    if draft["status"] == "sent":
        flash("Sent emails are kept as a record.", "error")
        return redirect(url_for("admin.campaign_draft", draft_id=draft_id))
    db.execute("DELETE FROM edm_events WHERE draft_id = ?", (draft_id,))
    db.execute("DELETE FROM edm_drafts WHERE id = ?", (draft_id,))
    db.commit()
    flash("Draft deleted.", "message")
    return redirect(url_for("admin.campaign_drafts"))
