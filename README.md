# Frame

Live event face matching for wedding photographers.

Photographers upload rounds of photographs *during* the event, roughly every ten
minutes. Guests scan a QR code, take one selfie, and get a gallery of every
photograph they appear in. The gallery keeps filling through the night as more
photographs are indexed.

Uploading and guest searching happen at the same time, on the same machine, at
the moment the venue's wifi is worst. That single fact drove most of what
follows.

## Stack

| | |
|---|---|
| Studio app | Electron, React, TypeScript |
| Guest app | mobile web, React, no install |
| Admin panel | web |
| API | FastAPI |
| Database | PostgreSQL with pgvector |
| Queue | PostgreSQL `SKIP LOCKED`, not a broker |
| Face models | YuNet (detect) + SFace (embed), OpenCV Zoo, both Apache 2.0 |
| Storage | local disk in development, Cloudflare R2 in production |

## What was rejected, and why

More useful than the list of what was chosen.

**AWS Rekognition.** Never returns embeddings, only an opaque `FaceId`, so
searching in your own database is impossible. Also 5 TPS in the Mumbai region,
shared between indexing and searching, which breaks the moment two events run
at once.

**InsightFace `buffalo_l`.** Best accuracy of anything tested. The MIT licence
covers the code, not the pretrained weights, which are non-commercial research
only. That rules it out for anything sold.

**CompreFace.** Apache 2.0 covers Exadel's code, not the InsightFace weights it
loads, so the same restriction applies one layer deeper. It also ships two
Spring Boot services and its own Postgres to wrap a model callable in twenty
lines, and hides the embeddings.

**Cloudflare Queues.** The native consumer is a JS Worker. Postgres already does
this job, and it has to be tenant-fair, which a generic queue will not do.

**A dedicated vector database.** Roughly 14,000 vectors per event. Postgres
scans that in milliseconds. Not a problem worth a new service.

**React Native.** Photographers work from laptops and guests must not install
anything, so nobody would have used it.

**A European server.** Adds ~130ms to every call in the photographer's app for
an entire evening. A guest's one slow search is survivable; an evening of
sluggish uploading is not.

**Razorpay's SDK.** The whole integration is one POST and two HMAC checks.
Writing them out keeps every security-relevant line readable.

## Design decisions

**Photo bytes never pass through the API.** The client asks for a presigned
URL, uploads directly to storage, then tells the API it finished. A 600
photograph round is several gigabytes, and relaying that through a small server
is how it falls over on the one night it must not. The server always generates
the object key; if the client chose it, a crafted key could overwrite another
event's photographs.

**Match on index, not on refresh.** When the worker indexes a photograph it
immediately matches those faces against every guest already registered and
writes the match rows. A guest refreshing then runs a plain `SELECT` with no
model and no cost. The other way round would mean roughly 3,600 extra searches
per event, spiking at the worst possible moment.

**Tune for precision, not recall.** A guest who misses a photograph is
disappointed. A guest who sees a stranger's photographs is a privacy incident at
somebody's wedding. Target 99% precision even if recall falls to 70%.

**The payment webhook activates an event. The browser never does.** Studios pay
on phones at venues with bad signal and close the tab the moment UPI reports
success. Anything depending on the client coming back will eventually strand
someone who has paid and cannot upload.

**Entitlements are snapshotted onto the event at purchase**, never resolved
through a foreign key at read time, so changing a price or a policy later cannot
rewrite what somebody already bought.

**Accounts are created by hand.** No self-signup. The platform creates a studio
and its owner together, the owner creates photographers, credentials are handed
over directly. Login accepts a username *or* an email, because many small
studios have no email address at all.

## Tenant isolation

Two Postgres roles against one database. `frame` owns the tables and runs
migrations. `frame_app` owns nothing, has no `BYPASSRLS`, and is what the API
connects as, with `app.studio_id` set for the transaction.

The point is that a forgotten `WHERE` clause returns *no rows* rather than
another studio's wedding. Application-level filtering is one missed clause away
from a leak; a policy is applied by the database whether or not the developer
remembered.

The owner connection is used for exactly seven operations, all of which
genuinely have no tenant yet: logging in, refreshing a token, resolving a QR
token, loading a guest session, claiming a queue job, the retention purge, and
the admin panel. That list is in `backend/app/db.py` and is the whole security
argument.

`scripts/prove_isolation.py` checks it holds, including that no table with a
`studio_id` is missing its policy.

## The queue

One statement claims a job, and three ideas are packed into it.

`FOR UPDATE SKIP LOCKED` is why there is no broker: each worker locks a
different row rather than queueing behind the same one.

A stale-lock clause is crash recovery. A worker that dies releases its job after
five minutes, so a power cut during a reception costs minutes rather than the
night. Attempts increment at claim time, not at failure time, so a job that
kills the process still burns an attempt and cannot loop forever.

Ordering by `events.last_job_claimed_at` is the fair share. Plain FIFO lets a
studio bulk uploading a 15,000 photograph archive starve a live wedding for half
an hour, because every backlog job is older. Serving whichever event waited
longest, and stamping it as served on the way past, interleaves them. The unit
is the event rather than the studio, because starvation is felt by guests
standing at one particular wedding.

There is an approach here that looks correct and is not: ranking each job by its
position within its own event using `row_number()`. That ranking is recomputed
over pending jobs on every claim, so the backlog's second job becomes position 1
the moment its first is taken, the two events tie forever, and the older backlog
wins every tiebreak. It behaves exactly like FIFO while looking like fair
scheduling. Positions have to accumulate to be fair.

`scripts/prove_fair_share.py` runs both orderings over the same rows and prints
where the live event actually lands.

## Retention

Three clocks, and conflating them is the mistake worth avoiding.

| what | deleted when |
|---|---|
| guest selfie embeddings | consent + the window that guest was shown |
| face embeddings from photographs | event ended + the event's face retention |
| photographs and their storage objects | event ended + the purchased window |

Match rows survive all of it. They hold a guest id, a photo id and a score, none
of which is biometric, so galleries keep working after every embedding is gone.
That is what lets the biometric window be short without shortening the product.

Biometric retention is never a paid feature. It is agreed per studio, capped by
a server-side limit that needs a restart to change, snapshotted onto each event
at creation and onto each guest session at consent, and shown to the guest as a
calendar date rather than a duration. A date is a promise somebody can check.

Deleting database rows without deleting the storage objects is the standard
version of this bug: nothing breaks, nothing complains, and the storage bill
grows every month for files nobody can reach.

## Running it

Docker must be running.

```bash
cd backend
docker compose up -d
pip install -r requirements.txt
python scripts/download_models.py
python scripts/init_db.py
python scripts/seed_dev.py

python -u -m uvicorn app.main:app --reload   # terminal 2
python -u -m app.worker                      # terminal 3
```

```bash
npm run studio    # Electron studio app, localhost:5174
npm run guest     # guest gallery + admin, localhost:5175
```

With `VITE_API_BASE` unset the frontends run against MSW mocks and never
contact a backend, which is the right default for frontend work and completely
wrong in a release.

## Proving it works

Each script checks one thing that would be expensive to get wrong.

```bash
python scripts/prove_matching.py a1.jpg a2.jpg someone_else.jpg
python scripts/prove_isolation.py
python scripts/prove_fair_share.py
python scripts/prove_api_flow.py
python scripts/prove_frontend_contract.py
python scripts/review_matches.py
python scripts/smoke_test.py photo1.jpg photo2.jpg --selfie me.jpg
```

`prove_frontend_contract.py` exists because `npm run typecheck` proves the
TypeScript agrees with itself and nothing checks it against the server. A
renamed field compiles on both sides and fails only in a browser, as `undefined`
rendered into the page.

`review_matches.py` lays a guest's matches out weakest score first, which is
where false positives live, so a threshold can be chosen by looking rather than
guessing.

## Layout

```
backend/
  app/            config, db, models, security, auth, faces, matching,
                  worker, retention, razorpay, storage, audit, tiers
  app/routers/    one module per part of the product
  migrations/     Alembic
  scripts/        the prove_* scripts and operational tooling
  deploy/         Caddyfile, systemd units, backup script, DEPLOY.md
desktop/          Electron studio app
web/              guest gallery and admin panel
shared/           design system, API client, contract types, MSW mocks
api-contract-v1.yaml
```

The OpenAPI contract was frozen before either side was built, so the frontend
and backend could be developed against it independently.

## Licence

Source-available, not open source. You may read, study and run it locally to
evaluate it. Commercial use and redistribution need written permission. See
[LICENSE](LICENSE).

## Deployment

See `backend/deploy/DEPLOY.md`. It has the full sequence plus a checklist to run
before any real event, the first item of which is proving tenant isolation holds
on the production database.
