"""The English store (US customers) and the Chinese seller site (factories / stock owners) are separate."""
import re

from shop import notify

from .conftest import Client, product

CJK = re.compile(r"[一-鿿]")
STORE_LINK = re.compile(r'href="/(shop|p/|cart|quote|checkout)')


def test_english_store_is_english_only_and_does_not_link_to_seller_site(client, db):
    slug = product(db, "SPC-7MM-OAK")["slug"]
    for url in ["/", "/shop", "/shop/flooring", f"/p/{slug}", "/cart", "/quote", "/checkout", "/nope"]:
        html = client.get(url).data.decode()
        assert not CJK.search(html), f"Chinese text on English page {url}: {CJK.search(html).group()}"
        assert 'href="/zh' not in html, url
    assert client.get("/sell-with-us").status_code == 404  # supplier recruitment lives on the Chinese site


def test_seller_site_is_chinese_and_does_not_link_to_store(client):
    for url in ["/zh/", "/zh/apply", "/zh/nope"]:
        resp = client.get(url)
        html = resp.data.decode()
        assert '<html lang="zh-Hans">' in html and "seller.css" in html, url
        assert not STORE_LINK.search(html), f"{url} links to the English store"
        assert "style.css" not in html  # its own look
    home = client.get("/zh/").data.decode()
    assert "交给我们来卖" in home and "申请合作" in home and "/zh/template.xlsx" in home
    assert client.get("/zh/nope").status_code == 404


def test_application_saves_campaign_source_and_notifies(app, client, db, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body, reply_to=None: sent.append((to, subject, body)))
    client.get("/zh/?utm_source=edm&utm_campaign=oct-invite")  # arrived from the email campaign

    resp = client.post("/zh/apply", data={"company": "", "name": "", "items": ""})
    html = resp.data.decode()
    assert "请填写公司名称" in html and "请至少留下邮箱或微信" in html

    resp = client.post("/zh/apply", data={
        "company": "佛山某陶瓷有限公司", "name": "李经理", "phone": "wx_li888", "contact_pref": "wechat",
        "stock_status": "已在美国仓库", "location": "加州 Ontario", "categories": ["瓷砖 / 石材", "地板"],
        "items": "瓷砖 600x1200，2 个货柜",
    })
    assert "提交成功" in resp.data.decode()
    row = db.execute("SELECT * FROM inquiries WHERE name = '李经理'").fetchone()
    assert row["kind"] == "supplier" and row["reference"].startswith("F-")
    assert row["source"] == "edm / oct-invite"
    assert row["location"] == "已在美国仓库 · 加州 Ontario" and row["items"].startswith("品类：瓷砖 / 石材、地板")
    assert any("Factory application" in subject and "Source: edm / oct-invite" in body for _, subject, body in sent)


def test_direct_application_is_marked_direct(client, db):
    client.post("/zh/apply", data={"company": "X", "name": "Wang", "email": "w@example.com", "contact_pref": "email",
                                   "items": "Cabinets"})
    assert db.execute("SELECT source FROM inquiries WHERE name = 'Wang'").fetchone()[0] == "direct"


def test_template_download_on_seller_site(client):
    resp = client.get("/zh/template.xlsx")
    assert resp.status_code == 200 and resp.data[:2] == b"PK"


def test_partner_dashboard_is_chinese(app, db):
    token = db.execute("SELECT portal_token FROM suppliers WHERE id = 2").fetchone()[0]
    html = Client(app.test_client()).get(f"/zh/partner/{token}").data.decode()
    assert "待结算余额" in html and "销售记录" in html and "/箱" in html
    assert "Balance owed" not in html


def test_admin_reply_to_factory_is_chinese(admin, db):
    admin.post("/zh/apply", data={"company": "Z Co", "name": "张总", "phone": "13800138000", "contact_pref": "whatsapp",
                                  "items": "橱柜 300 套"})
    inquiry = db.execute("SELECT id FROM inquiries WHERE name = '张总'").fetchone()
    html = admin.get(f"/admin/inquiries/{inquiry['id']}").data.decode()
    assert "张总 您好" in html and "/zh/template.xlsx" in html


def test_admin_campaign_page_builds_tracked_email(admin):
    html = admin.get("/admin/campaigns?campaign=nov-flooring").data.decode()
    assert "utm_campaign=nov-flooring" in html and "utm_source=edm" in html
    assert "我们帮您卖" in html and "退订" in html  # email body + opt-out line


def test_seller_site_on_its_own_domain(tmp_path):
    from shop import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "t", "DATABASE": str(tmp_path / "d.db"),
                      "UPLOAD_FOLDER": str(tmp_path / "u"), "SELLER_SITE_DOMAIN": "partner.example.com"})
    client = app.test_client()
    seller_home = client.get("/", headers={"Host": "partner.example.com"}).data.decode()
    assert "交给我们来卖" in seller_home
    assert client.get("/apply", headers={"Host": "partner.example.com"}).status_code == 200
    assert client.get("/static/seller.css", headers={"Host": "partner.example.com"}).status_code == 200
    store_home = client.get("/", headers={"Host": "shop.example.com"}).data.decode()
    assert "Factory-direct" in store_home and not CJK.search(store_home)
