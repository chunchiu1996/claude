"""AI-drafted marketing emails: the AI writes, a person reviews and approves before anything can be used."""
import json

import pytest

from shop import edm


def ai_answer(**overrides):
    out = {
        "subject_options": ["Pro faucets from $68, in stock in Ontario", "Faucets your crews will like", "Stock up"],
        "preheader": "Volume pricing on brushed nickel and matte black.",
        "headline": "Faucets in stock, ready for pickup",
        "intro": "Our pull-down kitchen faucet is $68.00 and drops to $55.00 at 50+.",
        "bullets": ["Solid brass bodies", "Free warehouse pickup", "Pay by card or invoice"],
        "closing": "Need a bigger quantity? Ask for a quote.",
        "cta_label": "Shop faucets",
        "product_skus": ["FAU-KIT-PD-BN", "fau-bth-ws-mb", "NOT-A-SKU"],
        "reviewer_notes": ["Confirm the 50+ price is still current."],
    }
    out.update(overrides)
    return out


@pytest.fixture
def fake_ai(app, monkeypatch):
    """Replace the HTTP call to Claude; record what was sent."""
    app.config["ANTHROPIC_API_KEY"] = "test-key"
    calls, answers = [], []

    def call(body):
        calls.append(body)
        out = answers.pop(0) if answers else ai_answer()
        return {"model": body["model"], "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(out)}]}

    monkeypatch.setattr(edm, "_call_claude", call)
    return calls, answers


def new_draft(admin, audience="buyer", campaign="Spring Faucets", brief="Push faucets to contractors"):
    resp = admin.post("/admin/campaigns/drafts", data={"audience": audience, "campaign": campaign, "brief": brief})
    assert resp.status_code == 302, resp.data
    return int(resp.location.rsplit("/", 1)[1])


def draft(db, draft_id):
    return db.execute("SELECT * FROM edm_drafts WHERE id = ?", (draft_id,)).fetchone()


def approve(admin, draft_id, **data):
    return admin.post(f"/admin/campaigns/drafts/{draft_id}/approve",
                      data={"reviewed_by": "Kim", "confirm": "1", **data}, follow_redirects=True)


def test_ai_draft_waits_for_review_and_uses_real_catalog(admin, db, fake_ai):
    calls, _ = fake_ai
    draft_id = new_draft(admin)
    d = draft(db, draft_id)
    assert (d["status"], d["campaign"], d["subject"]) == ("review", "spring-faucets", "Pro faucets from $68, in stock in Ontario")
    assert d["product_skus"] == "FAU-KIT-PD-BN\nFAU-BTH-WS-MB"  # unknown SKU dropped, case fixed
    assert "NOT-A-SKU" in d["ai_notes"] and "50+ price" in d["ai_notes"]

    body = calls[0]
    assert body["model"] == "claude-opus-5-5" and body["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in body and "FAU-KIT-PD-BN" in body["messages"][0]["content"]
    assert "$68.00 per piece" in body["messages"][0]["content"]  # prices come from the database
    assert "price on request" in body["messages"][0]["content"]  # quote-only products stay price-less

    page = admin.get(f"/admin/campaigns/drafts/{draft_id}").data.decode()
    assert "Needs review" in page and "Faucets in stock" in page and "Confirm the 50+ price" in page
    assert "utm_campaign=spring-faucets" in page and "Price on request" not in page


def test_export_is_blocked_until_approved(admin, db, fake_ai):
    draft_id = new_draft(admin)
    assert admin.get(f"/admin/campaigns/drafts/{draft_id}/email.html").status_code == 403
    resp = admin.post(f"/admin/campaigns/drafts/{draft_id}/sent")
    assert draft(db, draft_id)["status"] == "review" and resp.status_code == 302

    # Approval needs a name and the confirmation box
    assert b"Enter your name" in approve(admin, draft_id, reviewed_by="").data
    assert b"Tick the box" in approve(admin, draft_id, confirm="").data
    assert draft(db, draft_id)["status"] == "review"

    approve(admin, draft_id, review_note="Prices checked")
    d = draft(db, draft_id)
    assert (d["status"], d["reviewed_by"], d["review_note"]) == ("approved", "Kim", "Prices checked")
    export = admin.get(f"/admin/campaigns/drafts/{draft_id}/email.html")
    assert export.status_code == 200 and "attachment" in export.headers["Content-Disposition"]
    html = export.data.decode()
    assert "Faucets in stock" in html and "unsubscribe" in html and "utm_source=edm" in html

    admin.post(f"/admin/campaigns/drafts/{draft_id}/sent", data={"sent_note": "Brevo, 300 contractors"})
    assert draft(db, draft_id)["status"] == "sent"
    actions = [r["action"] for r in db.execute("SELECT action FROM edm_events WHERE draft_id = ? ORDER BY id",
                                               (draft_id,))]
    assert actions == ["generated", "approved", "sent"]
    # Sent emails are a record: no more edits
    admin.post(f"/admin/campaigns/drafts/{draft_id}", data={"subject": "changed"})
    assert draft(db, draft_id)["subject"] != "changed"


def test_editing_an_approved_draft_needs_approval_again(admin, db, fake_ai):
    draft_id = new_draft(admin)
    approve(admin, draft_id)
    current = dict(draft(db, draft_id))
    form = {k: current[k] or "" for k in edm.FIELDS}
    admin.post(f"/admin/campaigns/drafts/{draft_id}", data={**form, "headline": "New headline"})
    d = draft(db, draft_id)
    assert (d["status"], d["headline"], d["approved_at"]) == ("review", "New headline", None)
    assert admin.get(f"/admin/campaigns/drafts/{draft_id}/email.html").status_code == 403


def test_price_that_is_not_in_the_catalog_blocks_approval(admin, db, fake_ai):
    _, answers = fake_ai
    answers.append(ai_answer(intro="Kitchen faucets now just $49.99!"))
    draft_id = new_draft(admin)
    page = admin.get(f"/admin/campaigns/drafts/{draft_id}").data.decode()
    assert "$49.99 in the copy doesn" in page
    assert b"Fix these first" in approve(admin, draft_id).data
    assert draft(db, draft_id)["status"] == "review"

    # The reviewer corrects it to the real price: now it can be approved
    form = {k: draft(db, draft_id)[k] or "" for k in edm.FIELDS}
    admin.post(f"/admin/campaigns/drafts/{draft_id}", data={**form, "intro": "Kitchen faucets from $68.00."})
    approve(admin, draft_id)
    assert draft(db, draft_id)["status"] == "approved"


def test_checks_catch_placeholders_spam_and_hidden_products(app, db, fake_ai):
    with app.test_request_context():
        draft_id = edm.create_draft(db, "buyer", "x", "")
        db.execute("UPDATE products SET active = 0 WHERE sku = 'FAU-BTH-WS-MB'")
        db.execute("UPDATE edm_drafts SET intro = 'Hi [First name], act now: HUGE GUARANTEED savings!!!' WHERE id = ?",
                   (draft_id,))
        results = edm.checks(db, draft(db, draft_id))
    errors = " ".join(edm.blocking(results))
    warnings = " ".join(m for level, m in results if level == "warn")
    assert "Placeholder" in errors and "hidden in the shop" in errors
    assert "act now" in warnings and "ALL-CAPS" in warnings and "exclamation" in warnings


def test_ask_ai_to_revise_sends_feedback_and_returns_to_review(admin, db, fake_ai):
    calls, answers = fake_ai
    draft_id = new_draft(admin)
    admin.post(f"/admin/campaigns/drafts/{draft_id}/reject", data={"review_note": "Too long"})
    assert draft(db, draft_id)["status"] == "rejected"

    answers.append(ai_answer(headline="Short and sweet"))
    admin.post(f"/admin/campaigns/drafts/{draft_id}/revise", data={"feedback": "Make it shorter"})
    d = draft(db, draft_id)
    assert (d["status"], d["headline"]) == ("review", "Short and sweet")
    content = calls[-1]["messages"][0]["content"]
    assert "<reviewer_feedback>\nMake it shorter" in content and "Faucets in stock, ready for pickup" in content


def test_chinese_seller_email(admin, db, fake_ai):
    _, answers = fake_ai
    answers.append(ai_answer(subject_options=["美国仓库存，我们帮您卖"], headline="寄售合作，按月结算",
                             intro="您好，", bullets=["中文沟通"], cta_label="了解合作详情",
                             product_skus=["FAU-KIT-PD-BN"], reviewer_notes=[]))
    draft_id = new_draft(admin, audience="seller", campaign="factory-nov")
    d = draft(db, draft_id)
    assert d["product_skus"] == ""  # no products in factory emails
    page = admin.get(f"/admin/campaigns/drafts/{draft_id}").data.decode()
    assert "退订" in page and "utm_campaign=factory-nov" in page and "All automatic checks passed" in page


def test_ai_errors_are_shown_and_nothing_is_saved(admin, db, app, monkeypatch):
    app.config["ANTHROPIC_API_KEY"] = ""
    resp = admin.post("/admin/campaigns/drafts", data={"audience": "buyer", "campaign": "x", "brief": ""},
                      follow_redirects=True)
    assert b"ANTHROPIC_API_KEY" in resp.data

    app.config["ANTHROPIC_API_KEY"] = "k"
    monkeypatch.setattr(edm, "_call_claude", lambda body: {"stop_reason": "refusal", "content": [],
                                                           "stop_details": {"explanation": "Declined."}})
    resp = admin.post("/admin/campaigns/drafts", data={"audience": "buyer", "campaign": "x", "brief": ""},
                      follow_redirects=True)
    assert b"declined" in resp.data
    assert db.execute("SELECT COUNT(*) FROM edm_drafts").fetchone()[0] == 0


def test_test_send_and_pages_render(admin, db, app, fake_ai, monkeypatch):
    sent = []
    monkeypatch.setattr("shop.notify.deliver", lambda to, subject, body, reply_to=None, html=None:
                        sent.append((to, subject, body, html)))
    draft_id = new_draft(admin)
    admin.post(f"/admin/campaigns/drafts/{draft_id}/test", data={"to": "me@example.com"})
    to, subject, body, html = sent[0]
    assert to == "me@example.com" and subject.startswith("[TEST] ") and "Shop faucets: http" in body
    assert "<html" in html
    assert draft(db, draft_id)["status"] == "review"  # a test send doesn't approve anything

    for url in ("/admin/campaigns", "/admin/campaigns/drafts", "/admin/campaigns/drafts?status=review"):
        assert admin.get(url).status_code == 200
    assert b"1 to review" in admin.get("/admin/campaigns").data
    admin.post(f"/admin/campaigns/drafts/{draft_id}/delete")
    assert draft(db, draft_id) is None


def test_cli_queues_a_draft_for_review(app, db, fake_ai):
    result = app.test_cli_runner().invoke(args=["edm-draft", "--audience", "buyer", "--brief", "Faucets",
                                                "--site-url", "https://shop.example.com"])
    assert result.exit_code == 0, result.output
    assert "https://shop.example.com/admin/campaigns/drafts/1" in result.output
    assert draft(db, 1)["status"] == "review"
