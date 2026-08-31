import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@shared/lib/api'
import {
  elapsedSince,
  formatBytes,
  formatDate,
  formatTime,
  Icon,
  Stat,
  StatusPill,
} from '@shared/components/ui'
import { UploadPanel } from './UploadPanel'

export function EventPage() {
  const { eventId = '' } = useParams()
  const qc = useQueryClient()
  const [filter, setFilter] = useState('')

  const { data: event } = useQuery({
    queryKey: ['event', eventId],
    queryFn: () => api.event(eventId),
  })

  /**
   * Stats poll every 5s while the event is live. This is the most frequently
   * hit endpoint in the whole system during a wedding, which is why the
   * contract keeps its response small.
   */
  const { data: stats } = useQuery({
    queryKey: ['stats', eventId],
    queryFn: () => api.eventStats(eventId),
    refetchInterval: event?.status === 'active' ? 5000 : false,
  })

  const { data: photos } = useQuery({
    queryKey: ['photos', eventId],
    queryFn: () => api.eventPhotos(eventId),
    refetchInterval: event?.status === 'active' ? 15000 : false,
  })

  const retry = useMutation({
    mutationFn: (photoId: string) => api.retryPhoto(photoId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['photos', eventId] })
      qc.invalidateQueries({ queryKey: ['stats', eventId] })
    },
  })

  const rows = (photos?.items ?? []).filter((p) =>
    filter ? (p.filename ?? '').toLowerCase().includes(filter.toLowerCase()) : true,
  )

  const failedIds = (photos?.items ?? []).filter((p) => p.status === 'failed').map((p) => p.id)

  /**
   * Surfaced when the queue starts falling behind. This is the earliest visible
   * sign of trouble, and it appears here before any guest notices an empty gallery.
   */
  const behind = (stats?.oldest_pending_seconds ?? 0) > 180

  return (
    <>
      <div className="topbar">
        <div className="ttl">
          {event?.name ?? 'Loading'}
          {event ? <span>{formatDate(event.event_date)}</span> : null}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          {event?.status === 'active' ? (
            <span className="live">
              <span className="pill__dot" />
              Live {elapsedSince(event.created_at)}
            </span>
          ) : null}
          <button className="btn btn--sub">
            <Icon.QR />
            QR code
          </button>
        </div>
      </div>

      <div className="tabs">
        <span className="tb tb--on">Photographs</span>
        <span className="tb">Guests</span>
        <span className="tb">Settings</span>
      </div>

      <div className="body">
        {behind ? (
          <div className="note" style={{ borderColor: 'var(--red-line)', background: 'var(--red-bg)' }}>
            <b>Processing is falling behind.</b> The oldest photograph has been waiting{' '}
            {stats?.oldest_pending_seconds}s. Guests will see new photographs later than usual.
          </div>
        ) : null}

        <UploadPanel eventId={eventId} />

        <div className="stats">
          <Stat label="Uploaded" value={(stats?.done ?? 0) + (stats?.processing ?? 0)} sub="6 rounds" />
          <Stat
            label="Processing"
            value={stats?.processing ?? 0}
            tone="amber"
            sub={
              stats?.processing
                ? `~${Math.max(1, Math.round(stats.processing / 8.5))}s remaining`
                : 'all caught up'
            }
          />
          <Stat label="Indexed" value={(stats?.done ?? 0).toLocaleString('en-IN')} sub="14,102 faces" />
          <Stat
            label="Needs you"
            value={stats?.failed ?? 0}
            tone={stats?.failed ? 'red' : undefined}
            sub={stats?.failed ? 'retry available' : 'nothing to do'}
          />
          <Stat label="Guests" value={stats?.guests_registered ?? 0} sub="registered" />
        </div>

        <div className="toolbar">
          <label className="search">
            <Icon.Search />
            <input
              placeholder="Search by filename"
              aria-label="Search photographs"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </label>
          <button className="btn">
            Status <Icon.Chevron />
          </button>
          <button className="btn">
            Round <Icon.Chevron />
          </button>
          {failedIds.length > 0 ? (
            <button
              className="btn btn--dgr"
              style={{ marginLeft: 'auto' }}
              disabled={retry.isPending}
              onClick={() => failedIds.forEach((id) => retry.mutate(id))}
            >
              Retry {failedIds.length} failed
            </button>
          ) : null}
          <button className="btn" style={failedIds.length ? undefined : { marginLeft: 'auto' }}>
            <Icon.Download />
            Export
          </button>
        </div>

        {rows.length === 0 ? (
          <div className="empty">
            <div className="empty__t">No photographs yet</div>
            <div className="empty__d">
              Drop your first round above and guests will start finding themselves within about a
              minute.
            </div>
          </div>
        ) : (
          <div className="tbl">
            <div className="tbl__scroll">
              <table>
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Round</th>
                    <th>Faces</th>
                    <th>Size</th>
                    <th>Status</th>
                    <th>Indexed</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.id}>
                      <td>
                        <span className="fname">{p.filename}</span>
                        <span className="fmeta">{p.id}</span>
                      </td>
                      <td className="tnum">{p.round ?? '—'}</td>
                      <td className={p.face_count == null ? 'dash' : 'tnum'}>
                        {p.face_count ?? '—'}
                      </td>
                      <td className="tnum">{formatBytes(p.size_bytes)}</td>
                      <td>
                        <StatusPill photo={p} />
                      </td>
                      <td className={p.processed_at ? 'tnum' : 'dash'} style={{ color: 'var(--ink4)' }}>
                        {formatTime(p.processed_at) ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
