# Going live on liquidatorusa.com with Cloudflare

The result:

| Address | What it is |
|---|---|
| `https://shop.liquidatorusa.com` | English store for US customers |
| `https://partner.liquidatorusa.com` | Chinese seller site for factories and stock owners (email campaign landing page) |
| `https://shop.liquidatorusa.com/admin` | Your back office |

Your main site at `liquidatorusa.com` / `www.liquidatorusa.com` is **not touched**. The shop only uses the two
new subdomains. To send visitors to the store, add a "Shop" link on your main site pointing to `https://shop.liquidatorusa.com`.

The app runs on one small server. Cloudflare sits in front of it for the domain, HTTPS and protection,
and connects to it through a **Cloudflare Tunnel**, so the server needs no open ports.

**Monthly cost:** Cloudflare free plan $0 + a small server about $5 + the domain (about $10 a year).

---

## 1. Put the domain on Cloudflare

> ✅ Already done for liquidatorusa.com: it resolves to a Cloudflare address. Continue with step 2.

1. Log in at dash.cloudflare.com → **Add a domain** → `liquidatorusa.com` → choose the **Free** plan.
2. If you bought the domain somewhere else, Cloudflare shows two nameservers. Set them at your
   registrar, then wait until Cloudflare says the domain is **Active**. This usually takes minutes, and at most 24 hours.

## 2. Get a server

Any Linux server with 1 GB of RAM or more is enough, e.g. Hetzner CX22, DigitalOcean or Vultr, about $4–6 a month.
Choose Ubuntu, then log in and install Docker:

```bash
curl -fsSL https://get.docker.com | sh
```

## 3. Create the tunnel

1. Cloudflare dashboard → **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel** →
   **Cloudflared** → name it `liquidatorusa`.
2. On the "install connector" screen, copy the **token** (the long text after `--token`). You will
   paste it into `.env`. You don't need to run their install command, because Docker runs the connector.
3. Add these two **public hostnames**. Both point to the same app:

   | Subdomain | Domain | Service type | URL |
   |---|---|---|---|
   | shop | liquidatorusa.com | HTTP | `app:8000` |
   | partner | liquidatorusa.com | HTTP | `app:8000` |

   Cloudflare creates the DNS records for `shop` and `partner` for you. **Don't add the main domain
   or `www`**: they keep serving your current site. The app shows the Chinese site on `partner.`
   automatically (`SELLER_SITE_DOMAIN`) and the English store on `shop.`.

## 4. Start the shop on the server

```bash
git clone https://github.com/chunchiu1996/claude.git liquidatorusa
cd liquidatorusa
cp .env.example .env
nano .env        # fill in ADMIN_PASSWORD, SECRET_KEY, CLOUDFLARE_TUNNEL_TOKEN, contacts
docker compose up -d --build
```

The repository is private: when `git clone` asks for a password, use a GitHub
[personal access token](https://github.com/settings/tokens) with read access to the repository.

Check that it's running:

- `https://shop.liquidatorusa.com/healthz` shows **ok**
- `https://shop.liquidatorusa.com` shows the English store
- `https://partner.liquidatorusa.com` shows the Chinese seller site
- On the server, `docker compose ps` shows the app as **healthy**

Then open `https://shop.liquidatorusa.com/admin`, log in, and follow the **Getting started** checklist.

## 5. Recommended Cloudflare settings

- **SSL/TLS → Edge Certificates → Always Use HTTPS**: on.
- **Email → Email Routing** (free): create `sales@liquidatorusa.com` and forward it to your own inbox,
  so the contact address on the site receives mail. Cloudflare only receives mail. To also *send* alerts
  from the shop, fill the `SMTP_*` settings, e.g. with a Gmail App Password, Brevo or Resend.

## Updating to a new version

```bash
cd liquidatorusa
git pull
docker compose up -d --build
```

## Backups

The `backup` service writes a snapshot of the database every day to `/data/backups` inside the
`shopdata` volume, and keeps the last 14. To copy everything (database, snapshots, product photos) off the server:

```bash
docker run --rm -v liquidatorusa_shopdata:/data -v "$PWD":/out alpine tar czf /out/shop-backup.tgz -C /data .
```

For automatic off-site copies, create a Cloudflare **R2** bucket (free up to 10 GB) and sync the
`shopdata` volume to it with `rclone` from a daily cron job.

## Chinese visitors

On Cloudflare's normal plans, visitors in mainland China reach the site through servers outside China.
It works, but it can be slower than in the US. The Chinese site loads no Google fonts or
third-party scripts, which are often blocked in China, and it offers WeChat as the main contact.
Hosting *inside* China would need a Chinese ICP licence, which isn't needed to get started.
