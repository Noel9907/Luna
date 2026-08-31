# Backend

Live event face matching. A photographer uploads rounds during a wedding, a
guest scans a QR code and takes one selfie, and their gallery fills through the
night as more photographs are indexed.

FastAPI, Postgres with pgvector, and a worker. The queue is Postgres, not a
broker. Face detection and embedding are YuNet and SFace from OpenCV Zoo, both
Apache 2.0, chosen because the alternatives with better benchmarks ship weights
licensed for research only and this is sold.

## Run it

Four terminals. Docker Desktop must be running.

```bash
# 1. database
docker compose up -d

# 2. schema and accounts, once
cp .env.example .env
pip install -r requirements.txt
python scripts/download_models.py
python scripts/init_db.py
python scripts/seed_dev.py

# 3. api   (python -m, not bare `uvicorn`: that one resolves to miniconda's,
#           which has none of these dependencies installed)
python -m uvicorn app.main:app --reload

# 4. worker  (run several for more throughput)
python -m app.worker
```

`seed_dev.py` prints three logins. They are development-only passwords and are
in the repository.

## Prove it works

Five scripts, each checking one thing that would be expensive to get wrong.

```bash
python scripts/prove_matching.py me1.jpg me2.jpg someone_else.jpg
python scripts/prove_isolation.py
python scripts/prove_fair_share.py
python scripts/prove_api_flow.py
python scripts/prove_frontend_contract.py
python scripts/smoke_test.py photo1.jpg photo2.jpg --selfie me.jpg
```

- **prove_matching** two photographs of one person score high, a third person
  scores low. The only question the product genuinely rests on.
- **prove_isolation** one studio cannot read or write another studio's rows,
  an unpinned connection sees nothing, and no table with a `studio_id` is
  missing its policy.
- **prove_fair_share** a live event is not starved behind another studio's
  backlog. Prints where the live event lands under FIFO versus fair share.
- **prove_api_flow** drives the whole API in-process: auth, refresh rotation,
  payment webhooks, guest consent, permissions. Needs no server and no photos.
- **prove_frontend_contract** every response carries the fields
  `shared/lib/types.ts` says it does. `npm run typecheck` proves the
  TypeScript agrees with itself and nothing checks it against the server, so a
  renamed field compiles on both sides and fails only in a browser. Run this
  after changing any response shape.
- **smoke_test** the real thing against a running server, with real faces.

## How it is put together

```
app/
  config.py      every setting, and what production refuses to start without
  db.py          two connections, two roles: the tenant boundary lives here
  models.py      tables
  security.py    Argon2 passwords, JWTs, opaque token digests
  auth.py        login, refresh rotation, the per-request tenant dependencies
  faces.py       detection and embedding. No web framework, no database
  matching.py    the two queries that are the product
  worker.py      the queue claim, indexing, thumbnails
  retention.py   the three deletion clocks
  razorpay.py    order creation and two HMAC checks, no SDK
  storage.py     presigned URLs, local disk in dev and R2 in production
  routers/       one module per part of the product
```

### Five things worth knowing before changing anything

**Photo bytes never pass through the API.** The client asks for a presigned
URL, uploads straight to storage, then tells the API it finished. The server
always builds the object key; if the client chose it, a crafted key could
overwrite another event's photographs.

**Matching happens on index, not on refresh.** When the worker indexes a
photograph it immediately matches those faces against every guest already
registered and writes `guest_matches` rows. A guest refreshing then runs a
plain SELECT with no model and no cost. The other way round would mean roughly
3,600 extra searches per event.

**Tenant isolation is row-level security, not a WHERE clause.** The API
connects as a Postgres role that owns nothing, with `app.studio_id` set for the
transaction. A forgotten filter returns no rows instead of another studio's
wedding. This only holds if `DATABASE_APP_URL` points at the restricted role;
`/v1/health` reports whether it does.

**The webhook activates an event. The browser never does.** Studios pay on
phones at venues with bad signal and close the tab the moment UPI says success.

**Tune for precision, not recall.** A guest who misses a photograph is
disappointed. A guest who sees a stranger's photographs is a privacy incident
at somebody's wedding.

## The queue

One statement claims a job, and three ideas are packed into it.

`FOR UPDATE SKIP LOCKED` is why there is no broker: each worker locks a
different row rather than queueing behind the same one.

A stale-lock clause is crash recovery. A worker that dies releases its job
after five minutes, so a power cut during a reception costs minutes, not the
night. Attempts increment at claim time, so a job that kills the process still
burns an attempt and cannot loop forever.

Ordering by `events.last_job_claimed_at` is the fair share. Plain FIFO lets a
studio bulk-uploading a 15,000 photograph archive starve a live wedding for
half an hour, because every backlog job is older. Serving whichever event
waited longest, and stamping it as served on the way past, interleaves them.

There is an attempt at this that looks right and is not: ranking each job by
its position within its own event with `row_number()`. The ranking is
recomputed over pending jobs on every claim, so the backlog's second job
becomes position 1 the moment its first is taken, the two events tie forever,
and the older backlog wins every tiebreak. Positions have to accumulate to be
fair. See the comment on `claim_job`.

## Retention

Three clocks, and conflating them is the mistake worth avoiding.

| what | deleted when |
|---|---|
| guest selfie embeddings | consent + the days that guest was shown |
| face embeddings from photographs | event ended + the event's face retention |
| photographs and their R2 objects | event ended + the tier's photo retention |

`guest_matches` survives all of it. It holds a guest id, a photo id and a
score, none of which is biometric, so galleries keep working after every
embedding is gone. That is what lets the biometric window be short without
shortening the product.

Face retention is negotiated per studio by the platform, capped by
`MAX_FACE_RETENTION_DAYS`, snapshotted onto each event at creation and onto
each guest session at consent, and shown to the guest as a calendar date. It is
never a tier feature: guests consent without knowing what the studio paid.

## Deploying

See `deploy/DEPLOY.md`. Run `prove_isolation.py` against production before any
real wedding is on it.
