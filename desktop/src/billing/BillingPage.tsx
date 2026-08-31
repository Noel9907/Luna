import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@shared/lib/api'
import { formatDate, Icon } from '@shared/components/ui'
import type { Payment } from '@shared/lib/types'

function rupees(paise: number) {
  return `₹${(paise / 100).toLocaleString('en-IN')}`
}

const TIER_NAME: Record<string, string> = {
  basic: 'Basic',
  pro: 'Pro',
  premium: 'Premium',
}

function StatusPill({ status }: { status: Payment['status'] }) {
  if (status === 'captured') {
    return (
      <span className="pill pill--done">
        <span className="pill__dot" />
        Paid
      </span>
    )
  }
  if (status === 'created') {
    return (
      <span className="pill pill--proc">
        <span className="pill__dot" />
        Pending
      </span>
    )
  }
  if (status === 'refunded') {
    return (
      <span className="pill pill--wait">
        <span className="pill__dot" />
        Refunded
      </span>
    )
  }
  return (
    <span className="pill pill--fail">
      <span className="pill__dot" />
      Failed
    </span>
  )
}

export function BillingPage() {
  const { data, isLoading } = useQuery({ queryKey: ['payments'], queryFn: api.payments })
  const payments = data?.items ?? []

  const paid = payments.filter((p) => p.status === 'captured')
  const total = paid.reduce((a, p) => a + p.amount_paise, 0)
  const thisMonth = paid
    .filter((p) => new Date(p.created_at).getMonth() === new Date().getMonth())
    .reduce((a, p) => a + p.amount_paise, 0)

  return (
    <>
      <div className="topbar">
        <div className="ttl">Billing</div>
        <Link to="/studio/events/new" className="btn btn--pri" style={{ textDecoration: 'none' }}>
          <Icon.Plus />
          New event
        </Link>
      </div>

      <div className="body">
        <div className="stats" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
          <div className="stat">
            <span className="stat__lbl">Events paid for</span>
            <div className="stat__v">{paid.length}</div>
            <div className="stat__sub">all time</div>
          </div>
          <div className="stat">
            <span className="stat__lbl">This month</span>
            <div className="stat__v">{rupees(thisMonth)}</div>
            <div className="stat__sub">
              {paid.filter((p) => new Date(p.created_at).getMonth() === new Date().getMonth()).length}{' '}
              events
            </div>
          </div>
          <div className="stat">
            <span className="stat__lbl">Total spent</span>
            <div className="stat__v">{rupees(total)}</div>
            <div className="stat__sub">all time</div>
          </div>
        </div>

        {isLoading ? (
          <div className="empty">
            <div className="empty__t">Loading</div>
          </div>
        ) : payments.length === 0 ? (
          <div className="empty">
            <div className="empty__t">Nothing billed yet</div>
            <div className="empty__d">
              You pay per event, not per month. Nothing is charged until you create one.
            </div>
            <Link to="/studio/events/new" className="btn btn--pri" style={{ textDecoration: 'none' }}>
              Create an event
            </Link>
          </div>
        ) : (
          <div className="tbl">
            <div className="tbl__scroll">
              <table>
                <thead>
                  <tr>
                    <th>Event</th>
                    <th>Plan</th>
                    <th>Paid with</th>
                    <th>Amount</th>
                    <th>Status</th>
                    <th>Date</th>
                  </tr>
                </thead>
                <tbody>
                  {payments.map((p) => (
                    <tr key={p.id}>
                      <td>
                        <Link
                          to={`/studio/events/${p.event_id}`}
                          style={{ color: 'inherit', textDecoration: 'none' }}
                        >
                          <span className="fname">{p.event_name}</span>
                          {p.razorpay_payment_id ? (
                            <span className="fmeta">{p.razorpay_payment_id}</span>
                          ) : null}
                        </Link>
                      </td>
                      <td>{TIER_NAME[p.tier_code] ?? p.tier_code}</td>
                      <td style={{ textTransform: 'uppercase', fontSize: 13 }}>{p.method ?? '—'}</td>
                      <td className="tnum">{rupees(p.amount_paise)}</td>
                      <td>
                        <StatusPill status={p.status} />
                      </td>
                      <td className="tnum" style={{ color: 'var(--ink4)' }}>
                        {formatDate(p.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="note">
          <b>Paying by UPI costs you nothing extra.</b> Card and netbanking payments carry a
          processing fee. If you have a choice at checkout, UPI is the cheaper one for everyone.
        </div>
      </div>
    </>
  )
}
