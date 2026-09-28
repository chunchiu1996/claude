import io

from shop import orders
from shop.importer import decode, import_products

from .conftest import Client, product


def test_admin_requires_login(client):
    resp = client.get("/admin/orders")
    assert resp.status_code == 302 and "/admin/login" in resp.location
    assert b"Wrong password" in client.post("/admin/login", data={"password": "nope"}, follow_redirects=True).data


def test_admin_disabled_without_password(app):
    app.config["ADMIN_PASSWORD"] = None
    client = Client(app.test_client())
    assert b"ADMIN_PASSWORD" in client.get("/admin/login").data
    assert client.post("/admin/login", data={"password": ""}).status_code == 200
    with client.session() as s:
        assert not s.get("admin")


def test_login_redirect_is_not_open(app):
    client = Client(app.test_client())
    resp = client.post("/admin/login?next=https://evil.example", data={"password": "letmein"})
    assert resp.location.endswith("/admin/")


def test_all_admin_pages_render(admin):
    for url in ["/admin/", "/admin/products", "/admin/products/new", "/admin/products/1", "/admin/import",
                "/admin/orders", "/admin/orders/1", "/admin/inquiries", "/admin/suppliers", "/admin/suppliers/1",
                "/admin/suppliers/new", "/admin/suppliers/1/edit", "/admin/categories"]:
        assert admin.get(url).status_code == 200, url


def test_create_and_edit_product(admin, db):
    resp = admin.post("/admin/products/new", data={
        "sku": "NEW-1", "name": "Test Grout 10 lb", "new_category": "Tools & Supplies", "supplier_id": "1",
        "price": "12.99", "moq": "1", "unit": "bag", "tiers": "10:11.50", "specs": "Color: Grey; Weight: 10 lb",
        "initial_stock": "200", "reference": "CONT-1", "active": "1",
    })
    assert resp.status_code == 302
    p = product(db, "NEW-1")
    assert (p["price_cents"], p["stock_qty"], p["specs"]) == (1299, 200, "Color: Grey\nWeight: 10 lb")
    assert db.execute("SELECT name FROM categories WHERE id = ?", (p["category_id"],)).fetchone()[0] == "Tools & Supplies"

    dup = admin.post("/admin/products/new", data={"sku": "new-1", "name": "Dup", "price": "1"})
    assert b"already uses this SKU" in dup.data

    admin.post(f"/admin/products/{p['id']}", data={"sku": "NEW-1", "name": "Test Grout 10 lb", "price": "abc"})
    assert product(db, "NEW-1")["price_cents"] == 1299  # invalid edit not saved

    admin.post(f"/admin/products/{p['id']}", data={"sku": "NEW-1", "name": "Sanded Grout 10 lb", "price": "13.49",
                                                    "unit": "bag", "moq": "1"})
    p = product(db, "NEW-1")
    assert (p["name"], p["price_cents"], p["active"], p["slug"]) == ("Sanded Grout 10 lb", 1349, 0, "sanded-grout-10-lb")


def test_stock_receive_and_adjust(admin, db):
    p = product(db, "HDW-BARN-6FT")
    admin.post(f"/admin/products/{p['id']}/stock", data={"kind": "receive", "qty": "50", "reference": "MSKU1"})
    admin.post(f"/admin/products/{p['id']}/stock", data={"kind": "adjust", "qty": "-3", "note": "Damaged"})
    admin.post(f"/admin/products/{p['id']}/stock", data={"kind": "adjust", "qty": "-99999", "note": "oops"})
    admin.post(f"/admin/products/{p['id']}/stock", data={"kind": "adjust", "qty": "-1"})  # missing reason
    assert product(db, "HDW-BARN-6FT")["stock_qty"] == p["stock_qty"] + 47


def test_order_status_flow_and_cancel_restock(admin, db):
    order = db.execute("SELECT * FROM orders WHERE status = 'pending_payment' LIMIT 1").fetchone()
    items = orders.get_items(db, order["id"])
    before = {i["product_id"]: db.execute("SELECT stock_qty FROM products WHERE id = ?", (i["product_id"],)).fetchone()[0]
              for i in items}
    admin.post(f"/admin/orders/{order['id']}", data={"status": "paid"})
    assert orders.get_order(db, order["id"])["status"] == "paid"
    admin.post(f"/admin/orders/{order['id']}", data={"status": "cancelled"})
    assert orders.get_order(db, order["id"])["status"] == "cancelled"
    for i in items:
        now = db.execute("SELECT stock_qty FROM products WHERE id = ?", (i["product_id"],)).fetchone()[0]
        assert now == before[i["product_id"]] + i["qty"]
    # cannot reopen a cancelled order (stock was already returned)
    resp = admin.post(f"/admin/orders/{order['id']}", data={"status": "paid"}, follow_redirects=True)
    assert b"can&#39;t be reopened" in resp.data
    assert orders.get_order(db, order["id"])["status"] == "cancelled"


def test_supplier_statement_and_payout(admin, app, db):
    # Flooring mill (id 2, 20% commission): demo order sold 120 boxes of SPC-7MM-OAK at $35.45 and got a $3,000 payout.
    html = admin.get("/admin/suppliers/2").data.decode()
    assert "$4,254.00" in html      # gross
    assert "$850.80" in html        # 20% commission
    assert "$3,403.20" in html      # net
    assert "$403.20" in html        # balance after $3,000 payout

    admin.post("/admin/suppliers/2", data={"amount": "403.20", "reference": "Wire 2"})
    from shop.reports import supplier_statement
    assert supplier_statement(db, 2)["balance_cents"] == 0

    token = db.execute("SELECT portal_token FROM suppliers WHERE id = 2").fetchone()[0]
    portal = Client(app.test_client()).get(f"/zh/partner/{token}").data.decode()
    assert "待结算余额" in portal and "$4,254.00" in portal
    assert Client(app.test_client()).get("/zh/partner/bad-token").status_code == 404


def test_supplier_create_and_rotate_link(admin, db):
    resp = admin.post("/admin/suppliers/new", data={"name": "Xiamen Stone Co", "commission_pct": "15"})
    s = db.execute("SELECT * FROM suppliers WHERE name = 'Xiamen Stone Co'").fetchone()
    assert resp.status_code == 302 and s["commission_rate"] == 0.15
    admin.post(f"/admin/suppliers/{s['id']}/edit", data={"name": "Xiamen Stone Co", "commission_pct": "15", "new_link": "1"})
    assert db.execute("SELECT portal_token FROM suppliers WHERE id = ?", (s["id"],)).fetchone()[0] != s["portal_token"]
    assert b"between 0 and 99" in admin.post("/admin/suppliers/new", data={"name": "Bad", "commission_pct": "150"}).data


def test_csv_import_creates_updates_and_receives(admin, db):
    before = product(db, "SPC-7MM-OAK")["stock_qty"]
    csv_text = (
        "SKU,Name,Category,Supplier,Price,Stock,Tiers,Specs,Unit\n"
        "SPC-7MM-OAK,,,,39.99,500,,,\n"
        "NEW-TILE-1,Terrazzo Tile 24x24,Tile,New Factory Ltd,5.50,1000,50:4.99 | 200:4.50,Size: 24x24; Finish: Honed,piece\n"
    )
    resp = admin.post("/admin/import", data={"file": (io.BytesIO(csv_text.encode()), "stock.csv"),
                                             "reference": "MSKU999", "stock_mode": "add"},
                      content_type="multipart/form-data")
    assert b"1 new, 1 updated" in resp.data
    oak = product(db, "SPC-7MM-OAK")
    assert (oak["stock_qty"], oak["price_cents"]) == (before + 500, 3999)
    new = product(db, "NEW-TILE-1")
    assert new["stock_qty"] == 1000 and new["specs"] == "Size: 24x24\nFinish: Honed"
    assert db.execute("SELECT COUNT(*) FROM price_tiers WHERE product_id = ?", (new["id"],)).fetchone()[0] == 2
    assert db.execute("SELECT reference FROM stock_movements WHERE product_id = ? AND kind = 'receive'",
                      (new["id"],)).fetchone()[0] == "MSKU999"


def test_csv_import_is_all_or_nothing(db):
    before = product(db, "SPC-7MM-OAK")["stock_qty"]
    result = import_products(db, "sku,stock,price\nSPC-7MM-OAK,100,\nBAD-NEW,5,\n")
    assert not result.ok and result.errors[0][0] == 3
    assert product(db, "SPC-7MM-OAK")["stock_qty"] == before
    assert product(db, "BAD-NEW") is None


def test_csv_import_set_mode_and_chinese_encoding(db):
    text = decode("sku,name,price,stock,category\nZH-1,瓷砖 Porcelain,9.99,40,瓷砖\n".encode("gb18030"))
    assert import_products(db, text).ok
    assert product(db, "ZH-1")["name"] == "瓷砖 Porcelain"
    assert import_products(db, "sku,stock\nZH-1,25\n", stock_mode="set").ok
    assert product(db, "ZH-1")["stock_qty"] == 25


def test_export_round_trips_through_import(admin, db):
    exported = admin.get("/admin/export/products.csv").data.decode("utf-8-sig")
    assert exported.startswith("sku,name,category")
    counts = db.execute("SELECT SUM(stock_qty) FROM products").fetchone()[0]
    result = import_products(db, exported, stock_mode="set")
    assert result.ok and result.created == 0
    assert db.execute("SELECT SUM(stock_qty) FROM products").fetchone()[0] == counts


def test_categories_admin(admin, db):
    admin.post("/admin/categories", data={"name": "Outdoor", "description": "Decking", "sort_order": "5"})
    row = db.execute("SELECT * FROM categories WHERE name = 'Outdoor'").fetchone()
    assert row["slug"] == "outdoor" and row["sort_order"] == 5
