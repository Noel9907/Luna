/**
 * Thin API client.
 *
 * Every call goes through `request`, so switching from the mock server to the
 * real backend is one environment variable and nothing else.
 */

import type {
  AdminOverview,
  AdminStudio,
  Checkout,
  CreatedMember,
  CreatedStudio,
  Event,
  EventStats,
  Me,
  Page,
  Photo,
  StudioMember,
  TokenPair,
  Branding,
  Payment,
  Tier,
  UploadSlot,
} from './types'

// `||`, not `??`. An empty VITE_API_BASE must fall back to the relative path,
// because that is how a production build is told to talk to whatever host
// served it. With `??` an empty value stays an empty string and every request
// loses its /v1 prefix.
const BASE = import.meta.env.VITE_API_BASE || '/v1'

let accessToken: string | null = localStorage.getItem('frame.token')
let refreshToken: string | null = localStorage.getItem('frame.refresh')

export function setTokens(pair: TokenPair | null) {
  accessToken = pair?.access_token ?? null
  refreshToken = pair?.refresh_token ?? null
  if (pair) {
    localStorage.setItem('frame.token', pair.access_token)
    localStorage.setItem('frame.refresh', pair.refresh_token)
  } else {
    localStorage.removeItem('frame.token')
    localStorage.removeItem('frame.refresh')
  }
}

export function hasSession() {
  return Boolean(accessToken)
}

/** Fires when a session dies mid-use so the app can drop back to sign-in. */
let onSignedOut: (() => void) | null = null
export function setSignedOutHandler(fn: (() => void) | null) {
  onSignedOut = fn
}

/** Thrown for any non-2xx. Carries the contract's error code. */
export class RequestError extends Error {
  code: string
  status: number
  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'RequestError'
    this.status = status
    this.code = code
  }
}

async function raw(path: string, init: RequestInit, withAuth: boolean): Promise<Response> {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body && typeof init.body === 'string') {
    headers.set('Content-Type', 'application/json')
  }
  if (withAuth && accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  return fetch(`${BASE}${path}`, { ...init, headers })
}

/**
 * Refreshes at most once per expired access token.
 *
 * The shared promise matters: five uploads run in parallel and would otherwise
 * each fire their own refresh, racing to invalidate one another's new token.
 */
let refreshing: Promise<boolean> | null = null

async function tryRefresh(): Promise<boolean> {
  if (!refreshToken) return false
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await raw(
          '/auth/refresh',
          { method: 'POST', body: JSON.stringify({ refresh_token: refreshToken }) },
          false,
        )
        if (!res.ok) return false
        setTokens((await res.json()) as TokenPair)
        return true
      } catch {
        return false
      } finally {
        refreshing = null
      }
    })()
  }
  return refreshing
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res = await raw(path, init, true)

  if (res.status === 401 && accessToken) {
    if (await tryRefresh()) {
      res = await raw(path, init, true)
    } else {
      setTokens(null)
      onSignedOut?.()
    }
  }

  if (res.status === 204) return undefined as T

  const body = await res.json().catch(() => null)

  if (!res.ok) {
    const err = body?.error
    throw new RequestError(
      res.status,
      err?.code ?? 'UNKNOWN',
      err?.message ?? 'Something went wrong. Please try again.',
    )
  }

  return body as T
}

export const api = {
  /** `identifier` is a username or an email address. Both are accepted. */
  login: async (identifier: string, password: string) => {
    const res = await raw(
      '/auth/login',
      { method: 'POST', body: JSON.stringify({ identifier, password }) },
      false,
    )
    const body = await res.json().catch(() => null)
    if (!res.ok) {
      throw new RequestError(
        res.status,
        body?.error?.code ?? 'UNKNOWN',
        body?.error?.message ?? 'Could not sign in.',
      )
    }
    setTokens(body as TokenPair)
    return body as TokenPair
  },

  /**
   * Signs out here and on the server.
   *
   * Clearing local storage alone leaves the refresh token valid for thirty
   * days, so anyone who lifted it from a shared laptop still has a session
   * long after the photographer thinks they signed out. The server call
   * revokes the whole token family.
   *
   * Fire and forget, and local state is cleared either way: a sign-out that
   * fails because the venue wifi dropped must still sign you out of this
   * machine.
   */
  logout: () => {
    const token = refreshToken
    setTokens(null)
    if (token) {
      void raw('/auth/logout', { method: 'POST', body: JSON.stringify({ refresh_token: token }) }, false)
        .catch(() => undefined)
    }
  },

  /** Also clears must_change_password and revokes other sessions. */
  changePassword: async (current_password: string, new_password: string) => {
    const pair = await request<TokenPair>('/auth/password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password }),
    })
    setTokens(pair)
    return pair
  },

  me: () => request<Me>('/me'),

  branding: () => request<Branding>('/studio/branding'),

  updateBranding: (input: {
    brand_color?: string | null
    watermark_enabled?: boolean
    watermark_scale?: number
    watermark_opacity?: number
  }) => request<Branding>('/studio/branding', { method: 'PATCH', body: JSON.stringify(input) }),

  /**
   * Multipart, so no Content-Type header is set by hand: the browser has to
   * add its own with the boundary, and setting it manually produces a body the
   * server cannot parse.
   */
  uploadLogo: (file: File) => {
    const form = new FormData()
    form.append('logo', file)
    return request<Branding>('/studio/branding/logo', { method: 'POST', body: form })
  },

  events: (status?: string) =>
    request<Page<Event>>(`/events${status ? `?status=${status}` : ''}`),

  event: (id: string) => request<Event>(`/events/${id}`),

  createEvent: (input: { name: string; event_date: string; tier_code: string }) =>
    request<Event>('/events', { method: 'POST', body: JSON.stringify(input) }),

  /**
   * Closes an event to new guests.
   *
   * Photographs already indexed stay reachable for the retention window the
   * studio bought. This also starts the clock the server's retention purge
   * measures from, which is why it is an explicit action rather than something
   * inferred from the event date.
   */
  endEvent: (id: string) => request<Event>(`/events/${id}/end`, { method: 'POST' }),

  tiers: () => request<{ items: Tier[] }>('/tiers'),

  /** Safe to call twice: returns the existing open order for a draft event. */
  checkout: (eventId: string) =>
    request<Checkout>(`/events/${eventId}/checkout`, { method: 'POST' }),

  /** Optimistic only. The webhook is what actually activates the event. */
  verifyCheckout: (
    eventId: string,
    payload: {
      razorpay_order_id: string
      razorpay_payment_id: string
      razorpay_signature: string
    },
  ) =>
    request<{ verified: boolean }>(`/events/${eventId}/checkout/verify`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  payments: () => request<Page<Payment>>('/payments'),

  eventStats: (id: string) => request<EventStats>(`/events/${id}/stats`),

  eventPhotos: (id: string, cursor?: string) =>
    request<Page<Photo>>(`/events/${id}/photos${cursor ? `?cursor=${cursor}` : ''}`),

  /**
   * Requests presigned upload URLs for the next slice of the queue.
   *
   * Called repeatedly in small batches rather than once for the whole round.
   * URLs expire in ten minutes and a 600 file round takes longer than that, so
   * minting them all upfront would leave the tail of the queue holding dead URLs.
   */
  requestUploadSlots: (
    eventId: string,
    files: { client_ref: string; content_type: string; size_bytes: number }[],
  ) =>
    request<{ uploads: UploadSlot[] }>(`/events/${eventId}/uploads`, {
      method: 'POST',
      body: JSON.stringify({ files }),
    }),

  /**
   * Idempotent. Safe to re-send after a flaky connection.
   *
   * `missing` counts ids whose bytes never actually reached storage. The server
   * checks before enqueuing, so a claimed upload that did not land is reported
   * here rather than becoming five failed processing attempts during a live
   * event. Anything counted here should be retried, not treated as done.
   */
  completeUploads: (eventId: string, photoIds: string[]) =>
    request<{ enqueued: number; skipped: number; missing: number }>(
      `/events/${eventId}/photos/complete`,
      { method: 'POST', body: JSON.stringify({ photo_ids: photoIds }) },
    ),

  retryPhoto: (photoId: string) =>
    request<Photo>(`/photos/${photoId}/retry`, { method: 'POST' }),

  members: () => request<{ items: StudioMember[] }>('/studio/members'),

  /** Returns the generated password once. It is never retrievable again. */
  createMember: (input: {
    username: string
    name?: string
    email?: string
    role: 'photographer' | 'owner'
  }) => request<CreatedMember>('/studio/members', { method: 'POST', body: JSON.stringify(input) }),

  removeMember: (userId: string) =>
    request<void>(`/studio/members/${userId}`, { method: 'DELETE' }),

  /**
   * Issues a new password for a photographer and signs them out everywhere.
   *
   * The revocation is the point. Resetting a password because somebody else
   * has it, while leaving that person's existing sessions alive, changes
   * nothing.
   */
  resetMemberPassword: (userId: string) =>
    request<CreatedMember>(`/studio/members/${userId}/reset-password`, { method: 'POST' }),

  /* ── platform admin ── */

  adminOverview: () => request<AdminOverview>('/admin/overview'),

  adminStudios: () => request<{ items: AdminStudio[] }>('/admin/studios'),

  /** Creates the studio and its owner together. Password returned once. */
  createStudio: (input: {
    name: string
    owner_username: string
    city?: string
    phone?: string
    /** Optional. Defaults to the platform default, capped server-side. */
    face_retention_days?: number
  }) => request<CreatedStudio>('/admin/studios', { method: 'POST', body: JSON.stringify(input) }),

  /**
   * Issues a new owner password. Returned once, same as creation.
   *
   * No username: this is called from a row in the studio table, so the server
   * resolves the account it created alongside the studio.
   */
  resetStudioPassword: (studioId: string) =>
    request<{ username: string; initial_password: string }>(
      `/admin/studios/${studioId}/reset-password`,
      { method: 'POST' },
    ),

  /**
   * Suspending also revokes every refresh token in that studio, so it takes
   * effect within one access token lifetime rather than whenever people
   * happen to sign out.
   */
  setStudioStatus: (studioId: string, status: 'active' | 'suspended') =>
    request<AdminStudio>(`/admin/studios/${studioId}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),

  setStudioRetention: (studioId: string, days: number) =>
    request<AdminStudio>(`/admin/studios/${studioId}`, {
      method: 'PATCH',
      body: JSON.stringify({ default_face_retention_days: days }),
    }),

  /* ── development only ── */

  /**
   * Fires a correctly signed payment webhook at the server, for use when no
   * Razorpay account is configured.
   *
   * Not a stub: it builds the payload Razorpay sends, signs it with the same
   * secret, and runs the same handler, so the path that activates an event is
   * the real one. Only reachable while the server is not in production.
   */
  simulatePayment: (eventId: string) =>
    request<Event>(`/webhooks/razorpay/simulate?event_id=${encodeURIComponent(eventId)}`, {
      method: 'POST',
    }),
}
