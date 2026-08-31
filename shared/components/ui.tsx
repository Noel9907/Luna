import type { ReactNode } from 'react'
import type { Photo } from '../lib/types'
import { PHOTO_ERROR_LABELS } from '../lib/types'

/* ── icons ── */

export const Icon = {
  Camera: () => (
    <svg viewBox="0 0 24 24">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="12" cy="12" r="3.5" />
    </svg>
  ),
  Calendar: () => (
    <svg viewBox="0 0 24 24">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 10h18" />
    </svg>
  ),
  People: () => (
    <svg viewBox="0 0 24 24">
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 19c0-3 2.5-5 5.5-5s5.5 2 5.5 5" />
      <path d="M17 11h4M19 9v4" />
    </svg>
  ),
  Activity: () => (
    <svg viewBox="0 0 24 24">
      <path d="M4 6h16M4 12h16M4 18h10" />
    </svg>
  ),
  Card: () => (
    <svg viewBox="0 0 24 24">
      <rect x="3" y="6" width="18" height="12" rx="2" />
      <path d="M3 10h18" />
    </svg>
  ),
  Brush: () => (
    <svg viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v8M8 12h8" />
    </svg>
  ),
  Plus: () => (
    <svg viewBox="0 0 24 24">
      <path d="M12 5v14M5 12h14" />
    </svg>
  ),
  Upload: () => (
    <svg viewBox="0 0 24 24">
      <path d="M12 15V3M7 10l5 5 5-5" />
      <path d="M4 17v3h16v-3" />
    </svg>
  ),
  Download: () => (
    <svg viewBox="0 0 24 24">
      <path d="M12 3v12M7 10l5 5 5-5" />
      <path d="M4 18v2h16v-2" />
    </svg>
  ),
  Search: () => (
    <svg viewBox="0 0 24 24">
      <circle cx="11" cy="11" r="7" />
      <path d="M16.5 16.5L21 21" />
    </svg>
  ),
  Chevron: () => (
    <svg viewBox="0 0 24 24">
      <path d="M6 9l6 6 6-6" />
    </svg>
  ),
  Lock: () => (
    <svg viewBox="0 0 24 24">
      <rect x="4" y="10" width="16" height="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  ),
  Building: () => (
    <svg viewBox="0 0 24 24">
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <path d="M9 20v-5h6v5M8 8h.01M12 8h.01M16 8h.01M8 12h.01M12 12h.01M16 12h.01" />
    </svg>
  ),
  Rupee: () => (
    <svg viewBox="0 0 24 24">
      <path d="M7 5h10M7 9h10M15 5c0 4-3.5 4.5-6 4.5L16 19" />
    </svg>
  ),
  QR: () => (
    <svg viewBox="0 0 24 24">
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  ),
}

/* ── status pill ── */

/**
 * The pill carries the failure reason, not a generic "Failed".
 * During a wedding a photographer should know whether a row needs action
 * without opening it.
 */
export function StatusPill({ photo }: { photo: Photo }) {
  if (photo.status === 'done') {
    return (
      <span className="pill pill--done">
        <span className="pill__dot" />
        Indexed
      </span>
    )
  }
  if (photo.status === 'processing') {
    return (
      <span className="pill pill--proc">
        <span className="pill__dot" />
        Processing
      </span>
    )
  }
  if (photo.status === 'failed') {
    return (
      <span className="pill pill--fail">
        <span className="pill__dot" />
        {PHOTO_ERROR_LABELS[photo.error_code ?? ''] ?? 'Failed'}
      </span>
    )
  }
  return (
    <span className="pill pill--wait">
      <span className="pill__dot" />
      Queued
    </span>
  )
}

/* ── stat card ── */

export function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string
  value: ReactNode
  sub?: string
  tone?: 'amber' | 'red'
}) {
  const toneClass = tone === 'amber' ? ' stat__v--amb' : tone === 'red' ? ' stat__v--red' : ''
  return (
    <div className="stat">
      <span className="stat__lbl">{label}</span>
      <div className={`stat__v${toneClass}`}>{value}</div>
      {sub ? <div className="stat__sub">{sub}</div> : null}
    </div>
  )
}

/* ── formatting ── */

export function formatBytes(n?: number) {
  if (n == null) return '—'
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${Math.round(n / 1024)} KB`
}

export function formatTime(iso: string | null) {
  if (!iso) return null
  return new Date(iso).toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

/** "4h 12m" since the given timestamp. */
export function elapsedSince(iso: string) {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}
