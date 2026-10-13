"""End-to-end check of a running shop: pages, admin, photos, spreadsheet import, checkout, email, invoice.

    python3 deploy/smoke_test.py https://127.0.0.1:8787 --password <admin password> [--rows 2000]
    python3 deploy/smoke_test.py https://shop.liquidatorusa.com            (read-only checks only)

With --password it also creates test data (products named SMOKE-…), so use it on a test copy, not the
live shop. Needs Pillow and openpyxl to make the test photo and spreadsheet.
"""
import argparse
import http.cookiejar
import io
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

RESULTS = []


def check(ok, label):
    RESULTS.append(f"{'PASS' if ok else 'FAIL'}  {label}")
    print(RESULTS[-1], flush=True)
    return ok


class Browser:
    def __init__(self, base, insecure):
        self.base = base.rstrip("/")
        context = ssl.create_default_context()
        if insecure:  # local test server with a self-signed certificate
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies), urllib.request.HTTPSHandler(context=context),
            _NoRedirect())

    def request(self, path, data=None, headers=None, follow=True):
        url = path if path.startswith("http") else self.base + path
        req = urllib.request.Request(url, data=data, headers=headers or {})
        for _ in range(10):
            try:
                with self.opener.open(req, timeout=300) as resp:
                    return resp.status, dict(resp.headers), resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303) and follow:
                    req = urllib.request.Request(urllib.parse.urljoin(url, exc.headers["Location"]))
                    url = req.full_url
                    continue
                return exc.code, dict(exc.headers), exc.read()
            except urllib.error.URLError as exc:  # e.g. a redirect to an address this machine can't reach
                return 0, {}, str(exc.reason).encode()
        raise RuntimeError("Too many redirects")

    def get(self, path, **kw):
        return self.request(path, **kw)

    def text(self, path):
        status, _, body = self.get(path)
        return status, body.decode("utf-8", "replace")

    def csrf(self, path):
        _, html = self.text(path)
        match = re.search(r'name="_csrf" value="([^"]+)"', html)
        return match.group(1) if match else ""

    def post(self, path, fields, files=None, csrf_from=None, follow=True, headers=None):
        fields = {"_csrf": self.csrf(csrf_from or path), **fields}
        if files:
            boundary = "----smoke" + secrets.token_hex(8)
            body = io.BytesIO()
            for key, value in fields.items():
                body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
            for key, (filename, raw, ctype) in files.items():
                body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
                           f"Content-Type: {ctype}\r\n\r\n".encode())
                body.write(raw + b"\r\n")
            body.write(f"--{boundary}--\r\n".encode())
            data, ctype = body.getvalue(), f"multipart/form-data; boundary={boundary}"
        else:
            data, ctype = urllib.parse.urlencode(fields, doseq=True).encode(), "application/x-www-form-urlencoded"
        return self.request(path, data=data, headers={"Content-Type": ctype, **(headers or {})}, follow=follow)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def photo(size=(4032, 3024)):
    from PIL import Image

    out = io.BytesIO()
    Image.effect_noise(size, 40).convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()


def spreadsheet(rows):
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Products 产品")
    ws.append(["SKU", "Name", "Category", "Price", "Quantity", "Volume pricing"])
    for i in range(rows):
        ws.append([f"SMOKE-{i:05d}", f"Smoke Test Tile {i}", "Tile", 10 + i % 40 + 0.99, 100 + i % 50, "20:9.50"])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def read_only_checks(b):
    status, body = b.text("/healthz")
    check(status == 200 and body.strip() == "ok", f"/healthz says ok ({status})")
    status, html = b.text("/")
    check(status == 200 and "<html" in html, f"home page loads ({status})")
    status, _, css = b.get("/static/style.css")
    check(status == 200 and b"--brand" in css, f"stylesheet served ({status})")
    status, headers, body = b.get("/zh/", follow=False)
    html = body.decode("utf-8", "replace")
    location = headers.get("Location", "")
    check((status == 200 and "交给我们来卖" in html) or (status == 301 and "partner." in location),
          f"Chinese seller site responds ({status} {location})")
    status, robots = b.text("/robots.txt")
    check(status == 200 and "Disallow: /admin" in robots, "robots.txt")


def full_checks(b, password, rows):
    status, _, _ = b.post("/admin/login", {"password": password}, follow=False)
    check(status == 302, f"admin login ({status})")
    status, html = b.text("/admin/")
    check(status == 200 and "Dashboard" in html, f"admin dashboard ({status})")

    sku = "SMOKE-PHOTO-" + secrets.token_hex(3).upper()
    status, _, _ = b.post("/admin/products/new", {"sku": sku, "name": "Smoke Photo Tile", "price": "25.00",
                                                   "unit": "box", "moq": "1", "active": "1", "initial_stock": "50"},
                          follow=False)
    check(status == 302, f"product created ({status})")

    started = time.time()
    status, _, body = b.post("/admin/photos/upload", {"format": "json"},
                             files={"files": (f"{sku}.jpg", photo(), "image/jpeg")}, csrf_from="/admin/photos",
                             headers={"Accept": "application/json"})
    ok = status == 200 and b'"status":"ok"' in body.replace(b" ", b"")
    check(ok, f"12 MP phone photo resized and stored ({status}, {time.time() - started:.1f}s) {body[:160]!r}")

    status, html = b.text("/shop?q=" + sku)
    small = re.search(r'src="(/media/[0-9a-f]{16}-sm\.jpg)"', html)
    check(bool(small), "product card shows the small photo")
    if small:
        status, headers, img = b.get(small.group(1))
        check(status == 200 and img[:3] == b"\xff\xd8\xff", f"photo served from storage ({status}, {len(img)} bytes)")

    raw = spreadsheet(rows)
    started = time.time()
    status, _, body = b.post("/admin/import", {"reference": "SMOKE", "stock_mode": "add"},
                             files={"file": ("factory.xlsx", raw, "application/octet-stream")}, csrf_from="/admin/import")
    seconds = time.time() - started
    text = body.decode("utf-8", "replace")
    imported = re.search(r"Imported: ([\d,]+) new", text)
    check(status == 200 and imported is not None,
          f"Excel import of {rows:,} rows ({status}, {seconds:.1f}s): {imported.group(0) if imported else text[:300]}")

    status, html = b.text("/shop?q=" + sku)
    product = re.search(r'href="(/p/[^"]+)"', html)
    status, html = b.text(product.group(1)) if product else (0, "")
    product_id = re.search(r'name="product_id" value="(\d+)"', html)
    check(bool(product_id), "product page has an add-to-cart form")
    if product_id:
        b.post("/cart/add", {"product_id": product_id.group(1), "qty": "2"}, csrf_from=product.group(1))
        status, _, body = b.post("/checkout", {"customer_name": "Smoke Test", "email": "smoke@example.com",
                                               "phone": "555-0100", "fulfillment": "pickup",
                                               "payment_method": "invoice"})
        text = body.decode("utf-8", "replace")
        check(status == 200 and "is confirmed" in text, f"invoice order placed ({status})")
        invoice = re.search(r'href="(/order/[^"]+/invoice)"', text)
        if check(bool(invoice), "order page links to the invoice"):
            status, html = b.text(invoice.group(1))
            check(status == 200 and "INVOICE" in html and "Payment due" in html, f"invoice page ({status})")

    status, _, body = b.post("/admin/email", {"to": "owner@example.com"})
    text = body.decode("utf-8", "replace")
    flash = re.search(r'class="flash flash-(\w+)">([^<]+)', text)
    check(status == 200 and flash is not None and "sent" in flash.group(2),
          f"test email: {flash.group(2).strip() if flash else text[:200]}")

    status, html = b.text("/admin/products?q=SMOKE-0")
    check(status == 200 and "Page 1 of" in html, f"admin product list is paged ({status})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("--password")
    parser.add_argument("--rows", type=int, default=2000)
    parser.add_argument("--insecure", action="store_true", help="accept a self-signed certificate (local test)")
    args = parser.parse_args()
    b = Browser(args.base, args.insecure)
    read_only_checks(b)
    if args.password:
        full_checks(b, args.password, args.rows)
    failed = [r for r in RESULTS if r.startswith("FAIL")]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
