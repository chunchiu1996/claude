# HomeSource Direct — factory-direct home improvement shop

An online store and back office for selling **home improvement products that Chinese factories have
already shipped to a US warehouse** (flooring, tile, cabinets, vanities, faucets, lighting, hardware).

Here's how it works: factories **consign** stock to you. You list it, sell it to homeowners,
contractors and retailers, keep a commission, and pay each factory its share. The app handles the
storefront and the bookkeeping behind that.

## What's included

**Storefront (for buyers)**
- Home page, category pages, search, sort, "in stock only" filter, mobile-friendly
- Product pages with specs, unit details ("23.64 sq ft per box"), typical-retail comparison, live stock
- **Volume pricing tiers**. Prices drop automatically at quantity breaks (e.g. 20+ boxes, 100+ boxes), and the cart shows "buy 100+ and pay $X"
- Minimum order quantities, and stock limits enforced in the cart
- Checkout with **warehouse pickup (free)** or **local delivery (flat fee)**
- Payment by **card via Stripe** (optional) or **reserve now, pay by invoice** (Zelle/ACH/check; common for B2B)
- **Contractor / bulk quote** form (can pre-fill from a product or the whole cart)
- **"Sell with us / 供应商合作"**: a bilingual page where more factories can apply to consign stock
- **Google Shopping / Facebook catalog feed** at `/feed/products.xml` (free product listings), plus `sitemap.xml`

**Back office (`/admin`)**
- Dashboard: 30-day sales, orders to fulfill, invoices to send, new inquiries, low stock, inventory value
- Products: create and edit, image upload, volume tiers, specs, hide/feature
- **Spreadsheet import**: upload the packing list a factory sends (CSV, including Chinese-Excel GBK files). New SKUs are created, existing ones updated, and received stock is logged against the container number. The import is all-or-nothing, so re-uploading a fixed file never double-counts stock
- CSV export (edit in Excel and re-import in "stocktake" mode)
- Stock receive/adjust with a full movement history per SKU
- Orders: mark paid → fulfilled, or cancel (the stock returns automatically). Abandoned card checkouts release their stock after 24 h
- Inquiries inbox: bulk quotes and factory applications, with a status for each
- **Factories**: commission % per factory, sales, commission, amount owed, record payouts

**Factory portal (`/supplier/<private-link>`)**
- Each factory gets a private, read-only link, in **English and Chinese**. It shows the stock received, sold,
  reserved and on hand, every sale, its earnings, payouts and the balance owed. You don't need to email them reports.

## Run it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export ADMIN_PASSWORD='choose-a-strong-password'
flask --app wsgi seed-demo        # optional: loads a fictional demo catalog
flask --app wsgi run --debug      # http://127.0.0.1:5000  — admin at /admin
pytest                             # run the tests
```

The demo suppliers and products are fictional and marked "(Demo)". Start real data from an empty
database: delete `instance/shop.db`, or skip `seed-demo`.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_PASSWORD` | *(unset: admin disabled)* | Staff login password |
| `SECRET_KEY` | auto-generated file | Session signing key. **Set this in production** |
| `SITE_NAME` | HomeSource Direct | Your store name |
| `SITE_TAGLINE` | … | Meta description / footer text |
| `CONTACT_EMAIL`, `CONTACT_PHONE` | sales@example.com | Shown in header, footer and order pages |
| `WAREHOUSE_ADDRESS` | Ontario, CA 91761 | Pickup location |
| `DELIVERY_FEE` | 150 | Flat local delivery fee in dollars |
| `STRIPE_SECRET_KEY` | *(unset)* | Enables card checkout. Without it, orders are pay-by-invoice |
| `ALLOW_INVOICE` | 1 | Set `0` to require card payment |
| `DATABASE`, `UPLOAD_FOLDER` | `instance/…` | Where the SQLite DB and uploaded images live |
| `TRUST_PROXY`, `SESSION_COOKIE_SECURE` | 0 | Set `1` behind an HTTPS host. The Dockerfile already does this |

## Deploy (about $7/month)

The app is one small Python process with a SQLite file, and it has no paid dependencies.

- **Render**: push this repo, then *New → Blueprint* to use `render.yaml`. That creates the web service and a 1 GB persistent disk, and asks for your admin password.
- **Anywhere with Docker** (Railway, Fly.io, a $5 VPS): `docker build -t shop . && docker run -p 8000:8000 -v shopdata:/data -e ADMIN_PASSWORD=… -e SECRET_KEY=… shop`

Back up `/data/shop.db` (plus `/data/uploads`) regularly. That file holds everything.

## Day-to-day workflow

1. **A factory ships a container**: add the factory under *Factories* and set its commission.
2. **The container arrives**: *Import* the factory's spreadsheet with the container number as the reference.
   Use the template (`sample_data/product-import-template.csv`) as the column guide.
3. **Send the factory its private dashboard link** from its factory page.
4. **Orders come in**: invoice orders show as *Awaiting payment*. Send the invoice, then *Mark paid*, then *Mark fulfilled* at pickup or delivery.
5. **Pay factories monthly**: each factory page shows the balance owed. Record the payout with the wire reference.

## Next steps worth considering

- Add real product photos. Placeholders are shown until then, and Google Shopping requires images.
- Submit `/feed/products.xml` to Google Merchant Center and Meta Commerce Manager (both free).
- Email notifications for new orders and quotes (e.g. Postmark or Resend).
- Sales tax: enable Stripe Tax, or add tax on invoices per your state's rules. Check your resale/consignment obligations with an accountant.
- A Stripe webhook, for payments where the buyer closes the tab before returning to the site. Today the order page confirms payment when the buyer returns, and staff can mark orders paid.
- A signed consignment agreement with each factory covering pricing authority, commission, payout schedule, insurance, and damaged/unsold stock.
