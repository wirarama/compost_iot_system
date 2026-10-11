# NGINX setup (Ubuntu VM)

Three sites share the VM:

| Address | What | Served by |
|---|---|---|
| `compostiq.win` (and `www.`, which redirects) | The public website | NGINX, straight from the repo's `website/public`, bind-mounted at `/var/www/compostiq` ([website/README.md](../../website/README.md)) |
| `dashboard.compostiq.win` | The dashboard | PM2 `compostiq-dashboard`, port 8050 |
| `api.compostiq.win` | The API | PM2 `compostiq-api`, port 8000 |

All four names need DNS A records pointing at the VM, and ports 80 and 443 must be open.

If DNS is on Cloudflare, set every record to **DNS only** (grey cloud) while requesting the certificate.
Afterwards you can turn the proxy back on, but set Cloudflare's SSL/TLS mode to **Full (strict)**.
The **Flexible** mode causes an endless redirect loop with this config.

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo certbot certonly --nginx --expand -d dashboard.compostiq.win -d api.compostiq.win \
    -d compostiq.win -d www.compostiq.win            # get the cert first
sudo cp compostiq.conf /etc/nginx/sites-available/compostiq.conf
sudo ln -s /etc/nginx/sites-available/compostiq.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo certbot renew --dry-run                        # confirm auto-renewal works
```

The website also needs its folder mounted into place once. The steps, and why it's a bind mount rather than a symlink, are in [website/README.md](../../website/README.md#set-up-on-the-vm-once).

### Adding compostiq.win to an existing certificate

If the VM already has the certificate for the two subdomains, add the website's names to it. Check first that `compostiq.win` and `www.compostiq.win` resolve to the VM (`dig +short compostiq.win`), and that the config above is installed, since its port-80 block answers the challenge for all four names:

```bash
sudo certbot certonly --nginx --expand --cert-name dashboard.compostiq.win \
    -d dashboard.compostiq.win -d api.compostiq.win -d compostiq.win -d www.compostiq.win
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certificates                           # the cert should list all four names
```

The certificate keeps its name and path (`/etc/letsencrypt/live/dashboard.compostiq.win/`), so the config doesn't change.

The apps must listen on `127.0.0.1` only, so the outside world reaches them through NGINX.

## Rate limiting

Requests to `https://api.compostiq.win/auth/` (register, sign in, change password, delete account) are limited **per client IP** to 10 a minute, with a burst of 10. Over the limit, NGINX answers `429 Too Many Requests` and the request never reaches the API. The zone is `auth` in `compostiq.conf`; change `rate=` and `burst=` there.

Requests to `https://api.compostiq.win/pairing/` (devices redeeming a pairing code) have the same limit in their own zone, `pairing`. The API also counts wrong codes per IP (10 in 15 minutes, then 429) whether or not NGINX is in front of it. For that count to see real client IPs, uvicorn needs `--proxy-headers`, which PM2 already passes.

```bash
# expect ten 401s, then 429s
for i in $(seq 1 15); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST https://api.compostiq.win/auth/login \
    -H 'content-type: application/json' -d '{"email":"nobody@example.com","password":"wrong-password"}'
done
```

Things to know:

- **The dashboard must call the API on the loopback address** (`API_URL=http://127.0.0.1:8000` in `.env`). Its calls are made by the server, so through NGINX every user would share the VM's IP and one rate limit.
- That also means this limit only covers clients that call the API directly. Sign-in attempts made through the dashboard's own pages are not limited yet.
- If Cloudflare's proxy is on (orange cloud), NGINX sees Cloudflare's IP, not the visitor's. Restore the real address with `set_real_ip_from` + `real_ip_header CF-Connecting-IP` before relying on the limit.

## Fresh reinstall

Use this to wipe NGINX and start clean. Let's Encrypt certificates live in `/etc/letsencrypt`, which this does not touch.

```bash
# 1. Back up the current config, just in case
sudo tar czf ~/nginx-backup-$(date +%F).tgz /etc/nginx

# 2. Stop and remove NGINX completely, including its config
sudo systemctl stop nginx
sudo apt purge -y nginx nginx-common nginx-core python3-certbot-nginx
sudo apt autoremove -y
sudo rm -rf /etc/nginx              # purge leaves behind files it didn't install

# 3. Reinstall
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
sudo mkdir -p /var/www/html
sudo systemctl enable --now nginx
curl -I http://localhost            # expect HTTP 200 from the welcome page

# 4. Certificate: skip certonly if one already exists for your domain
sudo certbot certificates
sudo certbot certonly --nginx -d dashboard.compostiq.win -d api.compostiq.win -d compostiq.win -d www.compostiq.win

# 5. Install the site, then run the setup commands above from the cp step onward
```
