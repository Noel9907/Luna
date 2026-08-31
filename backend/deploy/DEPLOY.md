# Deploying Frame

One Hetzner CPX31 in Singapore. Four vCPU, 8GB, about EUR 15 a month, which
holds roughly eight concurrent live events.

Singapore rather than Germany on purpose. The photographer's app makes hundreds
of small calls across an evening, and 130ms of extra latency on every one of
them is an hour of sluggishness during the event. A guest's single slow search
is survivable; the photographer's whole night is not.

## Before you start

- A domain, with `api.` and the bare name both pointing at the server's IP.
  Do this first: Caddy gets certificates on boot, and it cannot until DNS
  resolves.
- A Cloudflare R2 bucket, with CORS allowing `PUT` from the studio app's
  origin. Getting this wrong is the single most common way an afternoon
  disappears: the browser blocks the upload before it ever leaves, and the
  server sees nothing at all to log.
- Razorpay keys, and a webhook endpoint registered at
  `https://api.yourdomain/v1/webhooks/razorpay` subscribed to
  `payment.captured` and `payment.failed`.

## 1. Machine

```bash
adduser --system --group --home /srv/frame frame
apt update && apt install -y python3-venv python3-dev build-essential \
    postgresql-client caddy awscli git

# Postgres with pgvector already compiled in, same image as development.
apt install -y docker.io docker-compose-plugin
```

## 2. Database

```bash
cd /srv/frame/backend
docker compose -f docker-compose.prod.yml up -d
```

Postgres binds to `127.0.0.1` only. It has no reason to be reachable from the
internet, and the overwhelming majority of compromised databases are simply
ones that were listening on `0.0.0.0` with a weak password.

## 3. Application

```bash
python3 -m venv /srv/frame/venv
/srv/frame/venv/bin/pip install -r /srv/frame/backend/requirements.txt
/srv/frame/venv/bin/python scripts/download_models.py
```

Write `/srv/frame/backend/.env` from `.env.example`. These must all be right or
the process refuses to start, which is deliberate: every one of them fails
silently rather than loudly if it is wrong.

```
ENV=production
DATABASE_URL=postgresql+psycopg://frame:<strong>@127.0.0.1:5432/frame
DATABASE_APP_URL=postgresql+psycopg://frame_app:<strong>@127.0.0.1:5432/frame
APP_DB_PASSWORD=<strong>
JWT_SECRET=<48 random characters>
STORAGE_BACKEND=r2
PUBLIC_BASE_URL=https://api.yourdomain
GUEST_BASE_URL=https://yourdomain
CORS_ORIGINS_RAW=https://yourdomain,file://,null
RAZORPAY_KEY_ID=...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
```

`null` in that list is not a typo and not optional. The packaged desktop app
loads its window from `file://`, and Chromium sends the literal string `null`
as the Origin for every request from a `file://` page. Leave it out and the
released studio app is blocked by CORS on the first call, while development
against a dev server on localhost keeps working, which makes it a genuinely
confusing thing to debug after the fact.

It costs nothing to allow: this API authenticates with a bearer token in a
header and never a cookie, so an origin being permitted to send a request it
has no token for gains it nothing.

Generate the secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then migrate and create the first administrator:

```bash
cd /srv/frame/backend
/srv/frame/venv/bin/python scripts/init_db.py
/srv/frame/venv/bin/python scripts/create_admin.py noel
```

`init_db.py` creates the `frame_app` role using `APP_DB_PASSWORD`, so set that
before running it.

## 3a. Building the frontends

The API base is baked in at build time, so it has to be set before building,
not after:

```bash
# desktop/.env.production and web/.env.production
VITE_API_BASE=https://api.yourdomain/v1
```

```bash
npm run build
# web/dist  -> /srv/frame/guest, served by Caddy
# desktop   -> packaged and handed to studios
```

With `VITE_API_BASE` unset the apps start their MSW mock server instead and
never contact a backend at all. That is the right default for development and
completely wrong in a release, so check the built bundle actually points at the
API before shipping it to a studio.

## 4. Prove the isolation before anybody's wedding is on it

```bash
/srv/frame/venv/bin/python scripts/prove_isolation.py
/srv/frame/venv/bin/python scripts/prove_fair_share.py
```

The first is the one that matters. Row-level security is what stops one studio
seeing another's photographs, and the difference between having it and thinking
you have it is invisible until it is a phone call from a customer. If the
application is connected as the database owner, every policy is ignored and
everything still appears to work.

## 5. Services

```bash
cp deploy/frame-*.service deploy/frame-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now frame-api frame-worker@1 frame-worker@2 frame-worker@3
systemctl enable --now frame-purge.timer
```

Three workers, not four. The API and Postgres need a core between them, and a
machine with nothing spare is a machine where the photographer's app gets slow
at exactly the moment the queue is deepest.

## 6. HTTPS

```bash
cp deploy/Caddyfile /etc/caddy/Caddyfile   # edit the two hostnames first
systemctl reload caddy
```

## 7. Backups

```bash
chmod +x /srv/frame/backend/deploy/backup.sh
echo '0 3 * * * frame /srv/frame/backend/deploy/backup.sh >> /var/log/frame-backup.log 2>&1' \
    > /etc/cron.d/frame-backup
```

Then restore one into a scratch database this week, before you need to. A
backup nobody has restored is a hypothesis.

```bash
createdb frame_restore_test
gunzip -c /var/backups/frame/frame-*.dump.gz | pg_restore -d frame_restore_test --no-owner
psql frame_restore_test -c 'SELECT count(*) FROM guest_matches'
```

## 8. Monitoring

`GET /v1/health/deep` returns the queue depth and the age of the oldest
unprocessed job. That last number is the most useful one in the system: when it
climbs, guests at a wedding are watching a gallery that has stopped filling,
and you can see it minutes before anybody phones about it.

Point any uptime service at it. Alert on `oldest_pending_seconds` above 300.

```bash
journalctl -u frame-api -f
journalctl -u 'frame-worker@*' -f
systemctl list-timers frame-purge.timer
```

## Deploying a change

```bash
git pull
/srv/frame/venv/bin/pip install -r requirements.txt
/srv/frame/venv/bin/python scripts/init_db.py       # migrations
systemctl restart frame-api
systemctl restart frame-worker@1 frame-worker@2 frame-worker@3
```

Workers stop on SIGTERM after finishing the photograph in hand, so restarting
during a live event costs one photograph's latency rather than a stuck job.

Run migrations before restarting, and write them so the old code survives the
new schema for the few seconds in between. Adding a nullable column is safe;
renaming one is not, and wants two deploys.

## What to check before the first real wedding

- [ ] `prove_isolation.py` passes on the production database
- [ ] `/v1/health` reports `"row_level_security": true`
- [ ] A test event, paid with a real UPI payment, activates from the webhook
- [ ] The QR code resolves to the public URL, not localhost
- [ ] A selfie taken on a phone, over mobile data, returns a gallery
- [ ] The gallery loads thumbnails, not full-size originals
- [ ] A backup exists, and has been restored once
