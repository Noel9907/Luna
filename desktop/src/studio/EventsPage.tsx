import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@shared/lib/api'
import { formatDate, Icon } from '@shared/components/ui'
import type { Event } from '@shared/lib/types'

const TIER_LABEL: Record<Event['tier_code'], string> = {
  basic: 'Basic',
  pro: 'Pro',
  premium: 'Premium',
}

function EventStatusPill({ status }: { status: Event['status'] }) {
  if (status === 'active') {
    return (
      <span className="live">
        <span className="pill__dot" />
        Live
      </span>
    )
  }
  if (status === 'draft') {
    return (
      <span className="pill pill--wait">
        <span className="pill__dot" />
        Not paid
      </span>
    )
  }
  return (
    <span className="pill pill--done">
      <span className="pill__dot" />
      Ended
    </span>
  )
}

export function EventsPage() {
  const { data, isLoading } = useQuery({ queryKey: ['events'], queryFn: () => api.events() })

  return (
    <>
      <div className="topbar">
        <div className="ttl">Events</div>
        <Link to="/studio/events/new" className="btn btn--pri" style={{ textDecoration: 'none' }}>
          <Icon.Plus />
          New event
        </Link>
      </div>

      <div className="body">
        <div className="toolbar">
          <label className="search">
            <Icon.Search />
            <input placeholder="Search events" aria-label="Search events" />
          </label>
          <button className="btn">
            Status <Icon.Chevron />
          </button>
        </div>

        {isLoading ? (
          <div className="empty">
            <div className="empty__t">Loading events</div>
          </div>
        ) : !data?.items.length ? (
          <div className="empty">
            <div className="empty__t">No events yet</div>
            <div className="empty__d">
              Create an event, print its QR code, and guests can start finding themselves as soon as
              you upload.
            </div>
            <Link to="/studio/events/new" className="btn btn--pri" style={{ textDecoration: 'none' }}>
              Create your first event
            </Link>
          </div>
        ) : (
          <div className="tbl">
            <div className="tbl__scroll">
              <table>
                <thead>
                  <tr>
                    <th>Event</th>
                    <th>Date</th>
                    <th>Plan</th>
                    <th>Photographs stay</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((ev) => (
                    <tr key={ev.id}>
                      <td>
                        <Link
                          to={`/studio/events/${ev.id}`}
                          style={{ color: 'inherit', textDecoration: 'none' }}
                        >
                          <span className="fname">{ev.name}</span>
                          <span className="fmeta">{ev.qr_url.replace('https://', '')}</span>
                        </Link>
                      </td>
                      <td className="tnum">{formatDate(ev.event_date)}</td>
                      <td>{TIER_LABEL[ev.tier_code]}</td>
                      <td className="tnum">{ev.photo_retention_days} days</td>
                      <td>
                        <EventStatusPill status={ev.status} />
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
