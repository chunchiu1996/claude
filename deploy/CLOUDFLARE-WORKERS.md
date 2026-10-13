# Running the whole shop on Cloudflare (no server)

Everything runs inside your Cloudflare account. There is no server to rent, update or back up.

| Part of the shop | Runs on |
|---|---|
| English store, admin, Chinese seller site | **Cloudflare Workers** (the shop's Python code) |
| Database (products, orders, quotes, factories) | A **Durable Object** with built-in storage. Cloudflare keeps 30 days of history |
| Product photos | An **R2** bucket called `liquidatorusa-media` |
| Order confirmations, invoices, alerts | **Cloudflare Email Service** |
| Addresses | `shop.liquidatorusa.com` and `partner.liquidatorusa.com` (your main site isn't touched) |

**Cost:** the **Workers Paid** plan, $5 a month. It includes the Workers usage, the database, and 3,000
emails a month ($0.35 per 1,000 after that). R2 is free up to 10 GB of photos (about 20,000 photos).

You publish from GitHub with one button. GitHub tests the shop first, then sends it to Cloudflare.

---

## One-time setup (about 20 minutes)

### 1. Cloudflare: turn on Workers Paid

Cloudflare dashboard → **Workers & Pages** → **Plans** → choose **Workers Paid** ($5/month).

### 2. Cloudflare: allow sending email from your domain

Dashboard → **Email** → **Email Sending** → **Add domain** → `liquidatorusa.com` → follow the steps.
Cloudflare adds the DNS records (SPF, DKIM) for you. The shop sends from `sales@liquidatorusa.com`.

### 3. Cloudflare: make an API token for GitHub

1. Dashboard → your profile icon (top right) → **My Profile** → **API Tokens** → **Create Token**.
2. Next to **Edit Cloudflare Workers**, click **Use template**.
3. Under **Account Resources**, pick your account. Under **Zone Resources**, pick `liquidatorusa.com`.
4. Add two more permissions (skip any already listed): **Account** → **Workers R2 Storage** → **Edit**, and
   **Zone** → **DNS** → **Edit** (so the shop can create its two addresses).
5. **Continue to summary** → **Create Token**, then copy the token. You only see it once.

Also copy your **Account ID**: dashboard → **Workers & Pages**; it's shown on the right.

### 4. GitHub: add the two secrets

On GitHub, open the repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Name | Value |
|---|---|
| `CLOUDFLARE_API_TOKEN` | the token from step 3 |
| `CLOUDFLARE_ACCOUNT_ID` | the account ID from step 3 |

### 5. GitHub: publish

1. Merge the shop's branch into `main` (open a pull request and press **Merge**).
2. Publishing starts by itself. Watch it under **Actions** → **Deploy to Cloudflare** (about 3 minutes).
   To publish again later, open that workflow and press **Run workflow**.

The first publish creates the photo bucket, the database, and the two addresses (with HTTPS certificates).

### 6. Cloudflare: your password and contact details

Dashboard → **Workers & Pages** → **liquidatorusa** → **Settings** → **Variables and Secrets** → **Add**:

| Name | Type | Example |
|---|---|---|
| `ADMIN_PASSWORD` | **Secret** | a long password for `/admin` |
| `CONTACT_PHONE` | Text | `(626) 555-0199` |
| `WHATSAPP_NUMBER` | Text | `+1 626 555 0199` |
| `WECHAT_ID` | Text | your WeChat ID (shown on the Chinese site) |
| `WAREHOUSE_ADDRESS` | Text | `Ontario, CA 91761` |
| `DELIVERY_FEE` | Text | `150` |
| `PAYMENT_INSTRUCTIONS` | Text | `Zelle: pay@liquidatorusa.com\nCheck payable to Liquidator USA LLC` |
| `NOTIFY_EMAIL` | Text | where your order and quote alerts go (defaults to `sales@liquidatorusa.com`) |
| `STRIPE_SECRET_KEY` | **Secret** | optional: card payments |

Press **Deploy** to save. The shop restarts with the new settings within a few seconds.

### 7. Check it works

- `https://shop.liquidatorusa.com/healthz` shows **ok**
- `https://shop.liquidatorusa.com` shows the English store, `https://partner.liquidatorusa.com` the Chinese site
- Log in at `https://shop.liquidatorusa.com/admin`, open **Email** and press **Send test email**

---

## Good to know

- **Updates:** every merge into `main` publishes automatically after the tests pass.
- **Backups:** Cloudflare stores the database redundantly and keeps 30 days of history. For your own copy,
  use **Admin → Products → Export CSV**.
- **Upload limits:** spreadsheets and .zip files up to 25 MB each (a 20,000-row product list is about 1.5 MB).
  Photos chosen on the Photos page are sent one at a time, so a folder of any size works.
- **Photo formats:** JPG, PNG, WEBP, GIF. iPhone HEIC photos need converting to JPG first (when you upload from an
  iPhone browser, it usually converts them for you).
- **Spreadsheets:** `.xlsx` and `.csv`. Old `.xls` and `.ods` files: open them in Excel and save as `.xlsx`.
- **Logs:** Workers & Pages → liquidatorusa → **Logs** shows every request and any errors.
- **Moving to your own server later:** the same code runs in Docker (see `CLOUDFLARE.md`). Export your
  products to CSV and import them there.

## Trying it on your own computer (optional)

```bash
# needs Node.js 22 and uv (https://docs.astral.sh/uv/)
cd cloudflare
printf 'ADMIN_PASSWORD=test\n' > .dev.vars
uv run pywrangler dev          # → http://localhost:8787, with a local test database and bucket
```
