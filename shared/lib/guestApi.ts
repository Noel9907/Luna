/**
 * Guest API.
 *
 * Deliberately separate from the staff client. Guests authenticate with an
 * opaque session token in `X-Guest-Session`, never a bearer token, so a guest
 * credential can never be mistaken for a staff one. Keeping the two clients
 * apart also keeps staff code out of the guest bundle.
 */

import type { GuestEventInfo, GuestPhoto, GuestSession } from './types'

// `||`, not `??`. An empty VITE_API_BASE must fall back to the relative path,
// because that is how a production build is told to talk to whatever host
// served it. With `??` an empty value stays an empty string and every request
// loses its /v1 prefix.
const BASE = import.meta.env.VITE_API_BASE || '/v1'

export class GuestError extends Error {
  code: string
  status: number
  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'GuestError'
    this.status = status
    this.code = code
  }
}

function sessionKey(qrToken: string) {
  return `frame.guest.${qrToken}`
}

export function loadSession(qrToken: string): string | null {
  return localStorage.getItem(sessionKey(qrToken))
}

export function saveSession(qrToken: string, token: string) {
  localStorage.setItem(sessionKey(qrToken), token)
}

export function clearSession(qrToken: string) {
  localStorage.removeItem(sessionKey(qrToken))
}

/*
 * Venue wifi and a phone on the edge of a cell both fail the same way: the
 * request neither completes nor errors. Without a deadline `fetch` waits
 * forever, the promise never settles, and the caller's `finally` never runs, so
 * the button sits on "Looking for you" with nothing to retry and nothing shown.
 * A stall has to become a normal error the guest can act on.
 */
const READ_TIMEOUT_MS = 20_000
const UPLOAD_TIMEOUT_MS = 60_000

async function request<T>(path: string, init: RequestInit = {}, session?: string): Promise<T> {
  const headers = new Headers(init.headers)
  if (session) headers.set('X-Guest-Session', session)
  if (init.body && typeof init.body === 'string') headers.set('Content-Type', 'application/json')

  // Uploads get longer: a selfie on a weak uplink is slow but still working.
  const ms = init.body instanceof FormData ? UPLOAD_TIMEOUT_MS : READ_TIMEOUT_MS
  const control = new AbortController()
  const timer = setTimeout(() => control.abort(), ms)

  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers, signal: control.signal })
  } catch (e) {
    // AbortError and a dropped connection are the same thing to a guest.
    throw new GuestError(
      0,
      (e as Error)?.name === 'AbortError' ? 'TIMEOUT' : 'NETWORK',
      'The connection dropped. Check your signal and try again.',
    )
  } finally {
    clearTimeout(timer)
  }

  if (res.status === 204) return undefined as T

  const body = await res.json().catch(() => null)
  if (!res.ok) {
    throw new GuestError(
      res.status,
      body?.error?.code ?? 'UNKNOWN',
      body?.error?.message ?? 'Something went wrong. Please try again.',
    )
  }
  return body as T
}

export const guestApi = {
  event: (qrToken: string) => request<GuestEventInfo>(`/g/${qrToken}`),

  /**
   * Consent is recorded here. There is no path to a session without it.
   *
   * The response carries the exact date this guest's face data is deleted,
   * frozen at this moment. Show that date rather than recomputing one.
   */
  openSession: (qrToken: string) =>
    request<GuestSession>(`/g/${qrToken}/session`, {
      method: 'POST',
      body: JSON.stringify({ consented: true }),
    }),

  /**
   * Runs once per guest. After this, matching happens in the worker as new
   * photographs arrive, so the gallery never calls this again.
   */
  submitSelfie: (session: string, blob: Blob) => {
    const form = new FormData()
    form.append('selfie', blob, 'selfie.jpg')
    return request<{ matched_count: number }>('/g/selfie', { method: 'POST', body: form }, session)
  },

  /**
   * A plain database read on the server. No model, no cost, safe to poll.
   *
   * Two directions, and they are not the same request. `after` asks for
   * photographs newer than a cursor, which is the twenty-second poll. `cursor`
   * asks for older ones, which is scrolling back through a gallery that may
   * run to a thousand images.
   */
  photos: (session: string, opts: { after?: string; cursor?: string } = {}) => {
    const q = new URLSearchParams()
    if (opts.after) q.set('after', opts.after)
    if (opts.cursor) q.set('cursor', opts.cursor)
    const qs = q.toString()
    return request<{
      items: GuestPhoto[]
      next_cursor: string | null
      latest_cursor: string
      total_count: number
    }>(`/g/photos${qs ? `?${qs}` : ''}`, {}, session)
  },

  /**
   * Every matched photograph as one zip.
   *
   * Fetched rather than linked, because the endpoint authenticates on a header
   * and an <a href> cannot send one. The trade is that the zip lands in memory
   * before it reaches disk, so it gets its own long deadline: this is minutes
   * of transfer on venue wifi, not the twenty seconds a read gets.
   */
  downloadAll: async (session: string): Promise<Blob> => {
    const control = new AbortController()
    const timer = setTimeout(() => control.abort(), 10 * 60_000)
    try {
      const res = await fetch(`${BASE}/g/download`, {
        headers: { 'X-Guest-Session': session },
        signal: control.signal,
      })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new GuestError(
          res.status,
          body?.error?.code ?? 'UNKNOWN',
          body?.error?.message ?? 'The download failed. Try again.',
        )
      }
      return await res.blob()
    } catch (e) {
      if (e instanceof GuestError) throw e
      throw new GuestError(0, 'NETWORK', 'The download stopped. Check your signal and try again.')
    } finally {
      clearTimeout(timer)
    }
  },

  deleteMe: (session: string) => request<void>('/g/session', { method: 'DELETE' }, session),
}
