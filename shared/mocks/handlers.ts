/**
 * Mock server implementing api-contract-v1.yaml.
 *
 * This is the whole point of freezing the contract first: the frontend runs
 * against this until the real FastAPI server exists, and neither side waits
 * on the other. It also simulates a live event so the UI can be built against
 * counters that actually move.
 */

import { http, HttpResponse, delay } from 'msw'
import type {
  AdminOverview,
  AdminStudio,
  Event,
  EventStats,
  GuestPhoto,
  Me,
  Payment,
  Photo,
  StudioMember,
  Tier,
} from '../lib/types'

const BASE = '/v1'

const me: Me = {
  user_id: '11111111-1111-1111-1111-111111111111',
  username: 'lakeview',
  email: null,
  name: 'Noel K',
  role: 'owner',
  // Flip to true to see the forced password change on first sign-in.
  must_change_password: false,
  studio: {
    id: '22222222-2222-2222-2222-222222222222',
    name: 'Lakeview Studio',
    phone: '+91 98470 12345',
    brand_color: '#2B45C4',
    brand_logo_url: null,
  },
}

/** Any password of 8+ characters signs you in against the mock. */
const authHandlers = [
  http.post(`${BASE}/auth/login`, async ({ request }) => {
    await delay(600)
    const body = (await request.json()) as { identifier?: string; password?: string }
    if (!body?.identifier || (body.password ?? '').length < 8) {
      return HttpResponse.json(
        { error: { code: 'INVALID_CREDENTIALS', message: 'That username or password is not right.' } },
        { status: 401 },
      )
    }
    return HttpResponse.json({
      access_token: 'mock_access',
      refresh_token: 'mock_refresh',
      expires_in: 3600,
    })
  }),

  http.post(`${BASE}/auth/refresh`, async () => {
    await delay(200)
    return HttpResponse.json({
      access_token: 'mock_access',
      refresh_token: 'mock_refresh',
      expires_in: 3600,
    })
  }),

  http.post(`${BASE}/auth/password`, async ({ request }) => {
    await delay(700)
    const body = (await request.json()) as { new_password?: string }
    if ((body.new_password ?? '').length < 10) {
      return HttpResponse.json(
        { error: { code: 'PASSWORD_TOO_WEAK', message: 'Use at least 10 characters.' } },
        { status: 422 },
      )
    }
    me.must_change_password = false
    return HttpResponse.json({
      access_token: 'mock_access2',
      refresh_token: 'mock_refresh2',
      expires_in: 3600,
    })
  }),
]

const events: Event[] = [
  {
    id: 'e1',
    name: 'Sanjay & Meera',
    event_date: '2026-08-16',
    status: 'active',
    tier_code: 'pro',
    branding_mode: 'studio',
    photo_retention_days: 90,
    face_retention_days: 30,
    qr_url: 'https://frame.app/g/k3n8xq2p',
    created_at: '2026-08-16T09:00:00Z',
    activated_at: '2026-08-16T09:02:00Z',
    ended_at: null,
  },
  {
    id: 'e2',
    name: 'Anitha & Rahul',
    event_date: '2026-08-09',
    status: 'ended',
    tier_code: 'basic',
    branding_mode: 'platform',
    photo_retention_days: 30,
    face_retention_days: 30,
    qr_url: 'https://frame.app/g/m7p2wd4h',
    created_at: '2026-08-09T08:00:00Z',
    activated_at: '2026-08-09T08:01:00Z',
    ended_at: '2026-08-09T23:30:00Z',
  },
  {
    id: 'e3',
    name: 'Deepa & Vishnu',
    event_date: '2026-08-23',
    status: 'draft',
    tier_code: 'premium',
    branding_mode: 'studio',
    photo_retention_days: 180,
    face_retention_days: 30,
    qr_url: 'https://frame.app/g/t9r5cz8k',
    created_at: '2026-08-15T14:20:00Z',
    // Draft: never paid for, so never activated.
    activated_at: null,
    ended_at: null,
  },
]

/** Live counters for e1, nudged on every poll so the UI has movement to render. */
const live = { uploaded: 3614, processing: 128, done: 3479, failed: 7, guests: 147 }

function tickLiveCounters() {
  if (live.processing > 0) {
    const moved = Math.min(live.processing, Math.floor(Math.random() * 9) + 2)
    live.processing -= moved
    live.done += moved
  }
  // a new round lands occasionally
  if (live.processing === 0 && Math.random() < 0.25) {
    const round = Math.floor(Math.random() * 40) + 20
    live.processing += round
    live.uploaded += round
  }
}

const FILE_TINTS = ['#7E8A6A', '#9A7B5C', '#6A7C8A', '#8A6A7C', '#7E7E6A', '#5C7E9A', '#9A8B5C', '#6A8A7C']

/**
 * A flat colour as a real image file.
 *
 * The gallery renders photographs with <img src>, because a background image
 * cannot be lazily loaded and the couple's own gallery runs to thousands of
 * files. So the mocks have to return something an <img> can actually load: a
 * bare colour string used to work only while the markup was a CSS background,
 * and would now render as a broken image everywhere.
 */
function tintImage(hex: string, label: string) {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600">` +
    `<rect width="600" height="600" fill="${hex}"/>` +
    `<text x="50%" y="52%" font-family="system-ui" font-size="42" fill="rgba(255,255,255,.55)" ` +
    `text-anchor="middle">${label}</text></svg>`
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
}

function makePhotos(count: number): Photo[] {
  const out: Photo[] = []
  for (let i = 0; i < count; i++) {
    const n = 4417 - i
    let status: Photo['status'] = 'done'
    let error: string | null = null
    if (i === 2) status = 'processing'
    else if (i === 3) status = 'uploaded'
    else if (i === 4) {
      status = 'failed'
      error = 'TOO_LARGE'
    } else if (i === 9) {
      status = 'failed'
      error = 'NO_FACES'
    }
    out.push({
      id: `p${n}`,
      status,
      thumbnail_url:
        status === 'done' ? tintImage(FILE_TINTS[i % FILE_TINTS.length], String(n)) : null,
      face_count: status === 'done' ? Math.floor(Math.random() * 14) + 1 : null,
      error_code: error,
      created_at: new Date(Date.now() - i * 40000).toISOString(),
      processed_at: status === 'done' ? new Date(Date.now() - i * 38000).toISOString() : null,
      filename: `DSC_0${n}.jpg`,
      size_bytes: status === 'failed' && error === 'TOO_LARGE' ? 2202009 : 380000 + i * 1300,
      round: 6 - Math.floor(i / 8),
    })
  }
  return out
}

const photosByEvent: Record<string, Photo[]> = {
  e1: makePhotos(48),
  e2: makePhotos(20),
  e3: [],
}

export const handlers = [
  ...authHandlers,
  http.get(`${BASE}/me`, async () => {
    await delay(120)
    return HttpResponse.json(me)
  }),

  // Revokes the token family server-side. Never reports whether the token was
  // real: an endpoint that distinguishes them is a way to test a stolen one.
  http.post(`${BASE}/auth/logout`, async () => {
    await delay(80)
    return new HttpResponse(null, { status: 204 })
  }),

  http.post(`${BASE}/events/:id/end`, async ({ params }) => {
    await delay(300)
    const ev = events.find((e) => e.id === params.id)
    if (!ev) {
      return HttpResponse.json(
        { error: { code: 'NOT_FOUND', message: 'That event does not exist.' } },
        { status: 404 },
      )
    }
    if (ev.status !== 'active' && ev.status !== 'ended') {
      return HttpResponse.json(
        { error: { code: 'NOT_ACTIVE', message: 'That event was never activated.' } },
        { status: 409 },
      )
    }
    ev.status = 'ended'
    ev.ended_at = ev.ended_at ?? new Date().toISOString()
    return HttpResponse.json(ev)
  }),

  http.get(`${BASE}/events`, async ({ request }) => {
    await delay(180)
    const status = new URL(request.url).searchParams.get('status')
    const items = status ? events.filter((e) => e.status === status) : events
    return HttpResponse.json({ items, next_cursor: null })
  }),

  http.get(`${BASE}/events/:id`, async ({ params }) => {
    await delay(140)
    const ev = events.find((e) => e.id === params.id)
    /*
     * Stands in for the payment.captured webhook landing a moment after the
     * studio finishes checkout. The client polls this endpoint and only treats
     * the event as paid once the server says active.
     */
    const due = ev ? pendingActivation.get(ev.id) : undefined
    if (ev && due && Date.now() >= due) {
      ev.status = 'active'
      pendingActivation.delete(ev.id)
      payments.unshift({
        id: `pmt_${Math.random().toString(36).slice(2, 8)}`,
        event_id: ev.id,
        event_name: ev.name,
        tier_code: ev.tier_code,
        amount_paise: tiers.find((t) => t.code === ev.tier_code)?.price_paise ?? 0,
        status: 'captured',
        method: 'upi',
        razorpay_payment_id: `pay_mock_${Math.random().toString(36).slice(2, 10)}`,
        created_at: new Date().toISOString(),
      })
    }
    if (!ev) {
      return HttpResponse.json(
        { error: { code: 'NOT_FOUND', message: 'That event does not exist.' } },
        { status: 404 },
      )
    }
    return HttpResponse.json(ev)
  }),

  http.get(`${BASE}/events/:id/stats`, async ({ params }) => {
    await delay(90)
    if (params.id === 'e1') {
      tickLiveCounters()
      const stats: EventStats = {
        pending: 0,
        uploaded: live.processing > 0 ? 12 : 0,
        processing: live.processing,
        done: live.done,
        failed: live.failed,
        guests_registered: live.guests,
        oldest_pending_seconds: live.processing > 0 ? Math.floor(Math.random() * 40) + 5 : null,
      }
      return HttpResponse.json(stats)
    }
    const photos = photosByEvent[params.id as string] ?? []
    const count = (s: Photo['status']) => photos.filter((p) => p.status === s).length
    return HttpResponse.json({
      pending: count('pending'),
      uploaded: count('uploaded'),
      processing: count('processing'),
      done: count('done'),
      failed: count('failed'),
      guests_registered: params.id === 'e2' ? 92 : 0,
      oldest_pending_seconds: null,
    } satisfies EventStats)
  }),

  http.get(`${BASE}/events/:id/photos`, async ({ params }) => {
    await delay(220)
    return HttpResponse.json({
      items: photosByEvent[params.id as string] ?? [],
      next_cursor: null,
    })
  }),

  http.post(`${BASE}/events/:id/uploads`, async ({ request }) => {
    await delay(200)
    const body = (await request.json()) as {
      files: { client_ref: string; content_type: string; size_bytes: number }[]
    }
    return HttpResponse.json(
      {
        uploads: body.files.map((f) => ({
          client_ref: f.client_ref,
          photo_id: crypto.randomUUID(),
          // In the mock, uploads PUT to a sink the service worker also handles.
          upload_url: `${BASE}/__mock_r2__/${crypto.randomUUID()}`,
          expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
        })),
      },
      { status: 201 },
    )
  }),

  /** Stands in for R2. Randomly fails so retry logic gets exercised. */
  http.put(`${BASE}/__mock_r2__/:key`, async () => {
    await delay(400 + Math.random() * 900)
    if (Math.random() < 0.12) return new HttpResponse(null, { status: 503 })
    return new HttpResponse(null, { status: 200 })
  }),

  http.post(`${BASE}/events/:id/photos/complete`, async ({ request }) => {
    await delay(150)
    const body = (await request.json()) as { photo_ids: string[] }
    return HttpResponse.json({ enqueued: body.photo_ids.length, skipped: 0 })
  }),

  http.post(`${BASE}/photos/:id/retry`, async ({ params }) => {
    await delay(200)
    for (const list of Object.values(photosByEvent)) {
      const p = list.find((x) => x.id === params.id)
      if (p) {
        p.status = 'processing'
        p.error_code = null
        return HttpResponse.json(p)
      }
    }
    return HttpResponse.json(
      { error: { code: 'NOT_FOUND', message: 'That photograph does not exist.' } },
      { status: 404 },
    )
  }),
]

/* ── guest ────────────────────────────────────────────────────────────
 * Simulates a live event: the matched set grows over time so the polling
 * gallery and the "new photographs" bar can be seen working.
 */

const GUEST_TINTS = [
  '#7E8A6A', '#9A7B5C', '#6A7C8A', '#8A6A7C', '#7E7E6A', '#5C7E9A',
  '#9A8B5C', '#6A8A7C', '#8A7B6A', '#6A6A8A', '#8A8A6A', '#5C8A7E',
]

let guestMatched = 0
let guestSeq = 0
const guestPhotos: GuestPhoto[] = []

function growGuestGallery(n: number) {
  for (let i = 0; i < n; i++) {
    guestSeq++
    const tint = GUEST_TINTS[guestSeq % GUEST_TINTS.length]
    guestPhotos.unshift({
      photo_id: `gp${guestSeq}`,
      // Two different images on purpose. The real server serves a small
      // thumbnail in the grid and the original only when one is opened, and
      // rendering the same file for both would hide a regression in that.
      thumbnail_url: tintImage(tint, `#${guestSeq}`),
      full_url: tintImage(tint, `#${guestSeq} full`),
      width: 2400,
      height: 1600,
      matched_at: new Date().toISOString(),
    })
  }
}

/** First selfie is rejected on purpose so the retake flow is exercised. */
let selfieAttempts = 0

export const guestHandlers = [
  http.get(`${BASE}/g/:qrToken`, async () => {
    await delay(200)
    return HttpResponse.json({
      event_name: 'Sanjay & Meera',
      event_date: '2026-08-16',
      accepting_guests: true,
      face_retention_days: 30,
      branding: {
        mode: 'studio',
        studio_name: 'Lakeview Studio',
        logo_url: null,
        brand_color: '#2B45C4',
      },
    })
  }),

  http.post(`${BASE}/g/:qrToken/session`, async ({ request }) => {
    await delay(220)
    const body = (await request.json()) as { consented?: boolean }
    if (!body?.consented) {
      return HttpResponse.json(
        { error: { code: 'CONSENT_REQUIRED', message: 'Consent is required.' } },
        { status: 422 },
      )
    }
    return HttpResponse.json(
      {
        session_token: `gs_${crypto.randomUUID()}`,
        expires_at: null,
        face_retention_days: 30,
        // A date, not a duration. Frozen at consent, so it is the promise this
        // particular guest was shown and not a policy that can move later.
        face_deletion_date: new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10),
      },
      { status: 201 },
    )
  }),

  http.post(`${BASE}/g/selfie`, async () => {
    await delay(1400)
    selfieAttempts++
    if (selfieAttempts === 1) {
      return HttpResponse.json(
        {
          error: {
            code: 'NO_FACE_DETECTED',
            message: 'We could not find a face in that photo.',
          },
        },
        { status: 422 },
      )
    }
    guestMatched = 18
    growGuestGallery(18)
    return HttpResponse.json({ matched_count: guestMatched })
  }),

  http.get(`${BASE}/g/photos`, async ({ request }) => {
    await delay(180)
    const after = new URL(request.url).searchParams.get('after')

    // A new round lands every so often while the event runs.
    if (guestPhotos.length && Math.random() < 0.4) growGuestGallery(Math.floor(Math.random() * 4) + 1)

    const items = after
      ? guestPhotos.filter((p) => p.matched_at > after)
      : guestPhotos

    return HttpResponse.json({
      items,
      next_cursor: null,
      latest_cursor: guestPhotos[0]?.matched_at ?? new Date().toISOString(),
      total_count: guestPhotos.length,
    })
  }),

  http.delete(`${BASE}/g/session`, async () => {
    await delay(400)
    guestPhotos.length = 0
    guestSeq = 0
    guestMatched = 0
    selfieAttempts = 0
    return new HttpResponse(null, { status: 204 })
  }),
]

/* ── studio members ── */

const members: StudioMember[] = [
  {
    user_id: '11111111-1111-1111-1111-111111111111',
    username: 'lakeview',
    email: null,
    name: 'Noel K',
    role: 'owner',
    status: 'active',
    must_change_password: false,
    photos_uploaded: 1842,
    last_active_at: new Date().toISOString(),
    created_at: '2026-07-02T10:00:00Z',
  },
  {
    user_id: '33333333-3333-3333-3333-333333333333',
    username: 'arun',
    email: null,
    name: 'Arun M',
    role: 'photographer',
    status: 'active',
    must_change_password: false,
    photos_uploaded: 1204,
    last_active_at: new Date(Date.now() - 12 * 60000).toISOString(),
    created_at: '2026-07-11T09:30:00Z',
  },
  {
    user_id: '44444444-4444-4444-4444-444444444444',
    username: 'sneha',
    email: null,
    name: 'Sneha R',
    role: 'photographer',
    // Set up but never signed in: the account works, it just still holds the
    // password the owner generated. The table reads those two fields together.
    status: 'active',
    must_change_password: true,
    photos_uploaded: 0,
    last_active_at: null,
    created_at: '2026-08-14T16:05:00Z',
  },
]

export const memberHandlers = [
  http.get(`${BASE}/studio/members`, async () => {
    await delay(180)
    return HttpResponse.json({ items: members })
  }),

  http.post(`${BASE}/studio/members/:userId/reset-password`, async ({ params }) => {
    await delay(420)
    const m = members.find((x) => x.user_id === params.userId)
    if (!m) {
      return HttpResponse.json(
        { error: { code: 'NOT_FOUND', message: 'No such member.' } },
        { status: 404 },
      )
    }
    // Back to needing a change, and every session they had is revoked. A reset
    // that left their old sessions alive would not have reset anything.
    m.must_change_password = true
    return HttpResponse.json({ ...m, initial_password: makePassword() })
  }),

  http.post(`${BASE}/studio/members`, async ({ request }) => {
    await delay(500)
    const body = (await request.json()) as {
      username: string
      name?: string
      email?: string
      role: 'photographer' | 'owner'
    }
    const taken = members.some((m) => m.username.toLowerCase() === body.username.toLowerCase())
    if (taken) {
      return HttpResponse.json(
        { error: { code: 'USERNAME_TAKEN', message: 'That username is already in use.' } },
        { status: 409 },
      )
    }

    const created: StudioMember = {
      user_id: crypto.randomUUID(),
      username: body.username,
      email: body.email ?? null,
      name: body.name ?? null,
      role: body.role,
      status: 'active',
      must_change_password: true,
      photos_uploaded: 0,
      last_active_at: null,
      created_at: new Date().toISOString(),
    }
    members.push(created)

    // The real server generates this. It is returned once and never again.
    const words = ['maple', 'harbour', 'copper', 'lantern', 'willow', 'cobalt']
    const initial_password = `${words[Math.floor(Math.random() * words.length)]}-${Math.floor(
      1000 + Math.random() * 9000,
    )}-${words[Math.floor(Math.random() * words.length)]}`

    return HttpResponse.json({ ...created, initial_password }, { status: 201 })
  }),

  http.delete(`${BASE}/studio/members/:userId`, async ({ params }) => {
    await delay(300)
    const i = members.findIndex((m) => m.user_id === params.userId)
    if (i === -1) {
      return HttpResponse.json(
        { error: { code: 'NOT_FOUND', message: 'That person is not in this studio.' } },
        { status: 404 },
      )
    }
    const owners = members.filter((m) => m.role === 'owner')
    if (members[i].role === 'owner' && owners.length === 1) {
      return HttpResponse.json(
        { error: { code: 'LAST_OWNER', message: 'A studio must always have at least one owner.' } },
        { status: 409 },
      )
    }
    members.splice(i, 1)
    return new HttpResponse(null, { status: 204 })
  }),
]

/* ── platform admin ── */

const PASSWORD_WORDS = ['maple', 'harbour', 'copper', 'lantern', 'willow', 'cobalt', 'ember', 'quartz']

function makePassword() {
  const w = () => PASSWORD_WORDS[Math.floor(Math.random() * PASSWORD_WORDS.length)]
  return `${w()}-${Math.floor(1000 + Math.random() * 9000)}-${w()}`
}

const studios: AdminStudio[] = [
  {
    id: '22222222-2222-2222-2222-222222222222',
    name: 'Lakeview Studio',
    slug: 'lakeview-studio',
    city: 'Kochi',
    phone: '+91 98470 12345',
    status: 'active',
    brand_color: '#2B45C4',
    default_face_retention_days: 30,
    owner_username: 'lakeview',
    events_total: 14,
    events_this_month: 3,
    photos_total: 41208,
    revenue_paise: 4200000,
    created_at: '2026-07-02T10:00:00Z',
    last_event_at: new Date().toISOString(),
  },
  {
    id: '55555555-5555-5555-5555-555555555555',
    name: 'Vaidehi Photo Works',
    slug: 'vaidehi-photo-works',
    city: 'Kochi',
    phone: '+91 97440 55221',
    status: 'active',
    brand_color: null,
    // A studio that negotiated a longer window. Retention is agreed per studio
    // and capped server-side; it is never something a tier can buy.
    default_face_retention_days: 60,
    owner_username: 'vaidehi',
    events_total: 6,
    events_this_month: 2,
    photos_total: 17640,
    revenue_paise: 1800000,
    created_at: '2026-07-28T08:30:00Z',
    last_event_at: new Date(Date.now() - 6 * 86400000).toISOString(),
  },
  {
    id: '66666666-6666-6666-6666-666666666666',
    name: 'Neel Weddings',
    slug: 'neel-weddings',
    city: 'Kollam',
    phone: null,
    status: 'suspended',
    brand_color: null,
    default_face_retention_days: 30,
    owner_username: 'neel',
    events_total: 1,
    events_this_month: 0,
    photos_total: 2110,
    revenue_paise: 150000,
    created_at: '2026-08-05T12:00:00Z',
    last_event_at: new Date(Date.now() - 21 * 86400000).toISOString(),
  },
]

export const adminHandlers = [
  http.get(`${BASE}/admin/overview`, async () => {
    await delay(200)
    const active = studios.filter((s) => s.status === 'active')
    const overview: AdminOverview = {
      studios_active: active.length,
      studios_suspended: studios.length - active.length,
      events_this_month: studios.reduce((a, s) => a + s.events_this_month, 0),
      events_live_now: 1,
      revenue_this_month_paise: 1050000,
      revenue_all_time_paise: studios.reduce((a, s) => a + s.revenue_paise, 0),
      photos_this_month: 18420,
    }
    return HttpResponse.json(overview)
  }),

  http.get(`${BASE}/admin/studios`, async () => {
    await delay(240)
    return HttpResponse.json({ items: studios })
  }),

  http.post(`${BASE}/admin/studios`, async ({ request }) => {
    await delay(600)
    const body = (await request.json()) as {
      name: string
      owner_username: string
      city?: string
      phone?: string
      face_retention_days?: number
    }
    const taken = studios.some(
      (s) => s.owner_username.toLowerCase() === body.owner_username.toLowerCase(),
    )
    if (taken) {
      return HttpResponse.json(
        { error: { code: 'USERNAME_TAKEN', message: 'That username is already in use.' } },
        { status: 409 },
      )
    }
    const created: AdminStudio = {
      id: crypto.randomUUID(),
      name: body.name,
      slug: body.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''),
      city: body.city ?? null,
      phone: body.phone ?? null,
      status: 'active',
      brand_color: null,
      default_face_retention_days: body.face_retention_days ?? 30,
      owner_username: body.owner_username,
      events_total: 0,
      events_this_month: 0,
      photos_total: 0,
      revenue_paise: 0,
      created_at: new Date().toISOString(),
      last_event_at: null,
    }
    studios.push(created)
    return HttpResponse.json({ ...created, initial_password: makePassword() }, { status: 201 })
  }),

  http.post(`${BASE}/admin/studios/:id/reset-password`, async ({ params }) => {
    await delay(500)
    const s = studios.find((x) => x.id === params.id)
    if (!s) {
      return HttpResponse.json(
        { error: { code: 'NOT_FOUND', message: 'No such studio.' } },
        { status: 404 },
      )
    }
    return HttpResponse.json({ username: s.owner_username, initial_password: makePassword() })
  }),

  http.patch(`${BASE}/admin/studios/:id`, async ({ params, request }) => {
    await delay(350)
    const body = (await request.json()) as { status: 'active' | 'suspended' }
    const s = studios.find((x) => x.id === params.id)
    if (!s) {
      return HttpResponse.json(
        { error: { code: 'NOT_FOUND', message: 'No such studio.' } },
        { status: 404 },
      )
    }
    s.status = body.status
    return HttpResponse.json(s)
  }),
]

/* ── billing ──────────────────────────────────────────────────────────
 * `key_id` starts with rzp_test_mock, which tells the client to skip loading
 * the real Razorpay SDK and simulate the modal instead. Swap in a real test key
 * and the identical code path runs against Razorpay's sandbox.
 */

const tiers: Tier[] = [
  {
    code: 'basic',
    name: 'Basic',
    price_paise: 150000,
    branding_mode: 'platform',
    photo_retention_days: 30,
    face_retention_days: 30,
    custom_domain: false,
  },
  {
    code: 'pro',
    name: 'Pro',
    price_paise: 300000,
    branding_mode: 'studio',
    photo_retention_days: 90,
    face_retention_days: 30,
    custom_domain: false,
  },
  {
    code: 'premium',
    name: 'Premium',
    price_paise: 600000,
    branding_mode: 'studio',
    photo_retention_days: 180,
    face_retention_days: 30,
    custom_domain: true,
  },
]

const payments: Payment[] = [
  {
    id: 'pmt1',
    event_id: 'e1',
    event_name: 'Sanjay & Meera',
    tier_code: 'pro',
    amount_paise: 300000,
    status: 'captured',
    method: 'upi',
    razorpay_payment_id: 'pay_Qk3n8Xq2pLmA',
    created_at: '2026-08-16T08:40:00Z',
  },
  {
    id: 'pmt2',
    event_id: 'e2',
    event_name: 'Anitha & Rahul',
    tier_code: 'basic',
    amount_paise: 150000,
    status: 'captured',
    method: 'card',
    razorpay_payment_id: 'pay_QhP2wD4hR7Yz',
    created_at: '2026-08-09T07:15:00Z',
  },
]

/** Draft events waiting on a webhook, keyed by event id. */
const pendingActivation = new Map<string, number>()

export const billingHandlers = [
  http.get(`${BASE}/tiers`, async () => {
    await delay(140)
    return HttpResponse.json({ items: tiers })
  }),

  http.get(`${BASE}/payments`, async () => {
    await delay(200)
    return HttpResponse.json({ items: payments, next_cursor: null })
  }),

  http.post(`${BASE}/events`, async ({ request }) => {
    await delay(400)
    const body = (await request.json()) as {
      name: string
      event_date: string
      tier_code: 'basic' | 'pro' | 'premium'
    }
    const tier = tiers.find((t) => t.code === body.tier_code)!
    const created: Event = {
      id: `e${Math.floor(Math.random() * 90000) + 10000}`,
      name: body.name,
      event_date: body.event_date,
      // Draft until the webhook says otherwise. This is the whole point.
      status: 'draft',
      tier_code: tier.code,
      branding_mode: tier.branding_mode,
      photo_retention_days: tier.photo_retention_days,
      face_retention_days: tier.face_retention_days,
      activated_at: null,
      qr_url: `https://frame.app/g/${Math.random().toString(36).slice(2, 10)}`,
      created_at: new Date().toISOString(),
      ended_at: null,
    }
    events.unshift(created)
    photosByEvent[created.id] = []
    return HttpResponse.json(created, { status: 201 })
  }),

  http.post(`${BASE}/events/:id/checkout`, async ({ params }) => {
    await delay(350)
    const ev = events.find((e) => e.id === params.id)
    if (!ev) {
      return HttpResponse.json(
        { error: { code: 'NOT_FOUND', message: 'No such event.' } },
        { status: 404 },
      )
    }
    if (ev.status === 'active') {
      return HttpResponse.json(
        { error: { code: 'ALREADY_ACTIVE', message: 'This event is already paid for.' } },
        { status: 409 },
      )
    }
    const tier = tiers.find((t) => t.code === ev.tier_code)!
    return HttpResponse.json(
      {
        razorpay_order_id: `order_mock_${ev.id}`,
        amount_paise: tier.price_paise,
        currency: 'INR',
        key_id: 'rzp_test_mock_key',
        event_id: ev.id,
        prefill_contact: '+919847012345',
        prefill_name: 'Lakeview Studio',
      },
      { status: 201 },
    )
  }),

  http.post(`${BASE}/events/:id/checkout/verify`, async ({ params }) => {
    await delay(250)
    // Stands in for the webhook arriving a moment later, as it would in production.
    pendingActivation.set(String(params.id), Date.now() + 4000)
    return HttpResponse.json({ verified: true })
  }),

  /*
   * The real server exposes this only when Razorpay is not configured, so the
   * payment path can be exercised without an account. It signs a real payload
   * and runs the real webhook handler there; here it just schedules the same
   * delayed activation the verify handler above does, so both sides of the
   * VITE_API_BASE switch behave the same way.
   */
  http.post(`${BASE}/webhooks/razorpay/simulate`, async ({ request }) => {
    await delay(250)
    const id = new URL(request.url).searchParams.get('event_id') ?? ''
    const ev = events.find((e) => e.id === id)
    if (!ev) {
      return HttpResponse.json(
        { error: { code: 'NOT_FOUND', message: 'No open order for that event.' } },
        { status: 404 },
      )
    }
    pendingActivation.set(id, Date.now() + 4000)
    return HttpResponse.json(ev)
  }),
]
