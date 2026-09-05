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

async function request<T>(path: string, init: RequestInit = {}, session?: string): Promise<T> {
  const headers = new Headers(init.headers)
  if (session) headers.set('X-Guest-Session', session)
  if (init.body && typeof init.body === 'string') headers.set('Content-Type', 'application/json')

  const res = await fetch(`${BASE}${path}`, { ...init, headers })
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

  /** A plain database read on the server. No model, no cost, safe to poll. */
  photos: (session: string, after?: string) =>
    request<{
      items: GuestPhoto[]
      next_cursor: string | null
      latest_cursor: string
      total_count: number
    }>(`/g/photos${after ? `?after=${encodeURIComponent(after)}` : ''}`, {}, session),

  deleteMe: (session: string) => request<void>('/g/session', { method: 'DELETE' }, session),
}
