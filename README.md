# Liquidator USA — factory-direct home improvement store (liquidatorusa.com)

An online store and back office for selling **home improvement products that Chinese factories have
already shipped to a US warehouse** (flooring, tile, cabinets, vanities, faucets, lighting, hardware).

Here's how it works: factories **consign** stock to you. You list it, sell it to homeowners,
contractors and retailers, keep a commission, and pay each factory its share. The app handles the
storefront and the bookkeeping behind that.

There are **two separate websites** in one app, for two audiences, with no links between them:

| | English store | Chinese seller site (中文招商站) |
|---|---|---|
| Address | `shop.liquidatorusa.com` (the app's `/`) | `partner.liquidatorusa.com` (the app's `/zh/`, via `SELLER_SITE_DOMAIN`) |
| Audience | US contractors, shops, homeowners | Chinese factories and stock owners with goods in the US |
| Purpose | Sell products | Recruit consignment stock; landing page for email (EDM) campaigns |
| Look | Warm terracotta and cream, English | Walnut and persimmon, Chinese |

## What's included

**English store (for US buyers)**
- Home page, category pages, search, sort, "in stock only" filter, mobile-friendly
- Product pages with specs, unit details ("23.64 sq ft per box"), typical-retail comparison, live stock
- **Volume pricing tiers**. Prices drop automatically at quantity breaks (e.g. 20+ boxes, 100+ boxes), and the cart shows "buy 100+ and pay $X"
- Minimum order quantities, and stock limits enforced in the cart
- Checkout with **warehouse pickup (free)** or **local delivery (flat fee)**
- Payment by **card via Stripe** (optional) or **reserve now, pay by invoice** (Zelle/ACH/check; common for B2B)
- **Quote requests without prices**: buyers add any product to a *quote list* with the quantities they need. They can also move their whole cart to it, or describe items you don't list. On submit they get a printable request (reference `Q-…`) that they can **send to you on WhatsApp**, by email, or save as PDF, and choose how you should reply (Email / WhatsApp / Phone / WeChat)
- **Quote-only products**: tick *Quote only* (or put `yes` in the spreadsheet) and the price is hidden. The product shows "Price on request" and can only be added to a quote list
- **Google Shopping / Facebook catalog feed** at `/feed/products.xml` (free product listings), plus `sitemap.xml`

**Back office (`/admin`)**
- Dashboard: 30-day sales, orders to fulfill, invoices to send, new inquiries, low stock, inventory value
- Products: create and edit, main photo plus a photo gallery, volume tiers, specs, hide/feature. The list is paged (50 per page) and can be filtered by factory, low stock or "no photo"
- **Bulk photo upload** (*Admin → Photos*): select hundreds of photos, a whole folder, or a .zip. Each photo goes to the product whose SKU matches its file name (`TILE-600.jpg` is the main photo, `TILE-600-2.jpg` the 2nd…). Photos are rotated, resized and compressed automatically (phone photos and iPhone HEIC work), with a small version for product cards. A results table lists any file that didn't match a SKU
- **Spreadsheet import**: upload the file a factory sends, **Excel (.xlsx, .xls), OpenOffice (.ods) or CSV** (Chinese-Excel GBK CSVs too). **Photos pasted into the Excel file** are attached to the product on the same row. Tested with 20,000 rows in one file (about 9 seconds). English, Chinese or bilingual headers all work, and title rows above the header (as on a typical packing list) are skipped. New SKUs are created, existing ones updated, and received stock is logged against the container number. The import is all-or-nothing, so re-uploading a fixed file never double-counts stock
- **Bilingual Excel template for factories**: `sample_data/product-import-template.xlsx`, also downloadable from *Admin → Import*. It has a blank *Products 产品* sheet with dropdowns and header tooltips, an *Instructions 说明* sheet, and an *Example 示例* sheet
- CSV export (edit in Excel and re-import in "stocktake" mode)
- Stock receive/adjust with a full movement history per SKU
- Orders: mark paid → fulfilled, or cancel (the stock returns automatically). Abandoned card checkouts release their stock after 24 h
- **Invoices**: a printable invoice for every order (print or save as PDF) with your payment instructions, and an **Email invoice** button. Customers can open theirs from the order page too
- **Inquiries inbox**: each quote request shows the products, quantities, stock on hand and list prices. There's a **ready-to-send price quote**: list and volume prices are pre-filled, with blanks for quote-only items. Edit it, then click **Send on WhatsApp** (opens a chat with the customer's number) or **Send by email**
- **Email**: new quote requests, factory applications and orders are emailed to you. Customers get an **order confirmation** and a copy of their quote request. *Admin → Email* shows the settings and has a **Send test email** button. Emails are sent in the background, so checkout never waits on the mail server. This needs SMTP; see below
- **Login protection**: after 5 wrong passwords, that visitor can't try again for 15 minutes
- **Factories**: commission % per factory, sales, commission, amount owed, record payouts

**Chinese seller site (`/zh/`, for factories and stock owners)**
- **Landing page** (`/zh/`): the problems it solves, services, a 5-step process, a sample dashboard, accepted categories, terms and FAQ
- **Application form** (`/zh/apply`): company, contact (WeChat / WhatsApp / email / phone), where the stock is, categories and quantities. Applications land in *Admin → Inquiries → Factory applications* with a **Chinese reply draft** ready to send by WhatsApp or email
- **Excel template download** (`/zh/template.xlsx`), so factories can prepare their product list before you talk
- **Partner dashboard** (`/zh/partner/<private-link>`): each factory's private report **in Chinese**, showing stock received, sold, reserved and on hand, every sale, their earnings, payouts and the balance owed
- **Campaign tracking**: links with `utm_source` / `utm_campaign` (e.g. from an email) are remembered, so each application records which campaign it came from

**Email campaigns (Admin → Campaigns)**
- Name a campaign to get a tracked landing-page link and a **ready-made Chinese marketing email** in HTML (it works in Outlook, Gmail, QQ Mail and 163 Mail). Copy or download it for your email tool (Mailchimp, SendCloud, Brevo…)
- A table of applications and signed factories per campaign

> The terms wording on the Chinese site (commission, monthly settlement, fees) is placeholder text. Check it matches your actual agreement in `shop/templates/seller/home.html` before sending campaigns.

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
| `SITE_NAME` | Liquidator USA | Your store name |
| `SITE_TAGLINE` | … | Meta description / footer text |
| `CONTACT_EMAIL`, `CONTACT_PHONE` | sales@example.com | Shown in header, footer and order pages |
| `WAREHOUSE_ADDRESS` | Ontario, CA 91761 | Pickup location |
| `DELIVERY_FEE` | 150 | Flat local delivery fee in dollars |
| `STRIPE_SECRET_KEY` | *(unset)* | Enables card checkout. Without it, orders are pay-by-invoice |
| `WHATSAPP_NUMBER` | *(unset)* | Your business WhatsApp (e.g. `+1 626 555 0199`). Adds "WhatsApp us" links and the *Send on WhatsApp* button on quote requests |
| `WECHAT_ID` | *(unset)* | Shown prominently on the Chinese seller site and its emails, and on quote requests |
| `SELLER_SITE_NAME` | `SITE_NAME` | Company name shown on the Chinese seller site |
| `SELLER_SITE_DOMAIN` | *(unset)* | Serve the Chinese seller site at the root of its own domain (e.g. `partner.example.com`). Point that domain at the same server |
| `NOTIFY_EMAIL` | `CONTACT_EMAIL` | Where new quote requests, factory applications and orders are emailed |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | *(unset)*, 587 | Outgoing email (alerts, order confirmations, invoices). Port 465 uses SSL; others use STARTTLS (`SMTP_STARTTLS=0` turns it off). Without SMTP everything still lands in *Admin → Inquiries / Orders* |
| `PAYMENT_INSTRUCTIONS` | *(unset)* | How customers pay an invoice (Zelle, bank transfer, check…), printed on invoices. `\n` starts a new line |
| `MAX_UPLOAD_MB` | 95 | Largest single upload. Keep it under 100 behind Cloudflare |
| `CLIENT_IP_HEADER` | *(unset)* | Header with the visitor's real IP, for the login limit. `docker-compose.yml` sets `CF-Connecting-IP` |
| `WEB_WORKERS` | 2 | Gunicorn processes in Docker (each runs 4 threads) |
| `ALLOW_INVOICE` | 1 | Set `0` to require card payment |
| `DATABASE`, `UPLOAD_FOLDER` | `instance/…` | Where the SQLite DB and uploaded images live |
| `TRUST_PROXY`, `SESSION_COOKIE_SECURE` | 0 | Set `1` behind an HTTPS host. The Dockerfile already does this |

## Deploy (about $5–7/month)

The app is one small Python process with a SQLite file, and it has no paid dependencies.

- **Cloudflare + a small server (recommended)**: follow [`deploy/CLOUDFLARE.md`](deploy/CLOUDFLARE.md).
  `docker compose up -d` runs the shop, a Cloudflare Tunnel (HTTPS, no open ports) and a daily backup.
  The store is served at `shop.liquidatorusa.com` and the Chinese seller site at `partner.liquidatorusa.com`;
  the main `liquidatorusa.com` site is left as it is. `/healthz` returns `ok` for uptime monitors.
  Settings go in `.env` (copy `.env.example`).
- **Render**: *New → Blueprint* uses `render.yaml`. It creates the web service and a 1 GB persistent disk,
  and you point the domain at it from Cloudflare DNS.

Everything lives in the data folder (`/data` in Docker): `shop.db`, product photos in `uploads/`, and
snapshots from `flask --app wsgi backup-db` in `backups/`. Copy it off the server regularly.

## Going live with your real products

1. Deploy (see above) **without** running `seed-demo`, so the store starts empty. The admin dashboard shows a getting-started checklist.
2. Set `CONTACT_EMAIL`, `CONTACT_PHONE`, `WAREHOUSE_ADDRESS` and `WHATSAPP_NUMBER`. Set the SMTP variables if you want email alerts. For Gmail: `SMTP_HOST=smtp.gmail.com`, `SMTP_USER=you@gmail.com`, and an [App Password](https://myaccount.google.com/apppasswords) as `SMTP_PASSWORD`.
3. Add each factory under *Factories*, then send them `product-import-template.xlsx`.
4. Upload each returned file under *Import*. Use *Quote only = yes* for anything you'd rather price per customer (slabs, custom sizes, full-container deals).
5. Add photos: name them by SKU and drop them all on *Admin → Photos* (or a .zip). Factories can also paste photos into the Excel template, or put photo links in its *Image URL* column.
6. Set up email and press **Send test email** on *Admin → Email*. Set `PAYMENT_INSTRUCTIONS` so invoices say how to pay.
7. Place a test order and send a test quote request from your phone to check the WhatsApp/email flow end to end.

## Limits

| | Limit | Why |
|---|---|---|
| One spreadsheet | about 30,000 rows, up to 95 MB | Cloudflare ends requests after 100 seconds. 20,000 rows take about 9 s. Split bigger lists into several files |
| One upload (spreadsheet or .zip) | 95 MB | Cloudflare's free plan accepts up to 100 MB per request |
| One .zip of photos | 1,000 photos | Keeps each upload well under 100 seconds. Selecting photos or a folder has no limit: the page sends them one at a time |
| One photo | 30 MB | Stored at up to 1600 px, plus a 600 px version for product cards |

## Day-to-day workflow

1. **A factory ships a container**: add the factory under *Factories* and set its commission.
2. **The container arrives**: *Import* the factory's spreadsheet with the container number as the reference.
   Use the Excel template (`sample_data/product-import-template.xlsx`) as the column guide. Then drop the photos on *Photos*.
3. **Send the factory its private dashboard link** from its factory page.
4. **Quote requests come in**: open one under *Inquiries*, fill in the blank prices in the draft reply, click *Send on WhatsApp* or *Send by email*, then set the status to *quoted*.
5. **Orders come in**: the customer gets a confirmation email. Invoice orders show as *Awaiting payment*: press *Email invoice to customer*, then *Mark paid* when the money arrives, then *Mark fulfilled* at pickup or delivery.
6. **Pay factories monthly**: each factory page shows the balance owed. Record the payout with the wire reference.

## Next steps worth considering

- Add real product photos. Placeholders are shown until then, and Google Shopping requires images.
- Submit `/feed/products.xml` to Google Merchant Center and Meta Commerce Manager (both free).
- Automatic WhatsApp alerts to your phone (needs the paid WhatsApp Business API, e.g. through Twilio). Today customers send requests to your WhatsApp themselves with one tap, and alerts come by email.
- Sales tax: enable Stripe Tax, or add tax on invoices per your state's rules. Check your resale/consignment obligations with an accountant.
- A Stripe webhook, for payments where the buyer closes the tab before returning to the site. Today the order page confirms payment when the buyer returns, and staff can mark orders paid.
- A signed consignment agreement with each factory covering pricing authority, commission, payout schedule, insurance, and damaged/unsold stock.
