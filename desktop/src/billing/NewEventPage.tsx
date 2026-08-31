import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, RequestError } from '@shared/lib/api'
import { Icon } from '@shared/components/ui'
import type { Tier, TierCode } from '@shared/lib/types'
import { openCheckout } from '../lib/razorpay'

type Phase = 'details' | 'paying' | 'confirming' | 'stuck'

const ACTIVATION_TIMEOUT_MS = 90_000
const POLL_MS = 2_000

function rupees(paise: number) {
  return `₹${(paise / 100).toLocaleString('en-IN')}`
}

function TierCard({
  tier,
  selected,
  onSelect,
}: {
  tier: Tier
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      className={selected ? 'tier tier--on' : 'tier'}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <span className="tier__hd">
        <span className="tier__name">{tier.name}</span>
        <span className="tier__price tnum">{rupees(tier.price_paise)}</span>
      </span>
      <ul className="tier__list">
        <li>
          {tier.branding_mode === 'studio' ? 'Your logo on the gallery' : 'Frame branding'}
        </li>
        <li>Photographs stay {tier.photo_retention_days} days</li>
        {tier.custom_domain ? <li>Your own domain</li> : null}
        <li className="tier__fixed">Face data deleted after {tier.face_retention_days} days</li>
      </ul>
    </button>
  )
}

export function NewEventPage() {
  const nav = useNavigate()
  const qc = useQueryClient()

  const [name, setName] = useState('')
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [tier, setTier] = useState<TierCode>('pro')
  const [phase, setPhase] = useState<Phase>('details')
  const [error, setError] = useState<string | null>(null)
  const [eventId, setEventId] = useState<string | null>(null)

  const { data: tiers } = useQuery({ queryKey: ['tiers'], queryFn: api.tiers })
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me })

  const pollTimer = useRef<number | null>(null)
  useEffect(() => () => { if (pollTimer.current) clearInterval(pollTimer.current) }, [])

  /**
   * Waits for the webhook to flip the event to active.
   *
   * Checkout reporting success is not proof of anything: it happened on this
   * machine and the server may not know yet. Polling the event is the only
   * honest confirmation, and it also covers the studio who paid and then closed
   * the window before the handler fired.
   */
  function waitForActivation(id: string) {
    setPhase('confirming')
    const started = Date.now()
    pollTimer.current = window.setInterval(async () => {
      try {
        const ev = await api.event(id)
        if (ev.status === 'active') {
          if (pollTimer.current) clearInterval(pollTimer.current)
          qc.invalidateQueries({ queryKey: ['events'] })
          nav(`/studio/events/${id}`)
          return
        }
      } catch {
        // keep polling; a dropped request is not a failed payment
      }
      if (Date.now() - started > ACTIVATION_TIMEOUT_MS) {
        if (pollTimer.current) clearInterval(pollTimer.current)
        setPhase('stuck')
      }
    }, POLL_MS)
  }

  async function start(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setPhase('paying')
    try {
      const ev = eventId
        ? await api.event(eventId)
        : await api.createEvent({ name: name.trim(), event_date: date, tier_code: tier })
      setEventId(ev.id)

      if (ev.status === 'active') {
        nav(`/studio/events/${ev.id}`)
        return
      }

      const checkout = await api.checkout(ev.id)
      const mock = checkout.key_id.startsWith('rzp_test_mock')
      const outcome = await openCheckout(checkout, me?.studio?.name ?? 'Frame')

      if (outcome.kind === 'dismissed') {
        setPhase('details')
        setError('Payment was cancelled. Your event is saved as a draft, so nothing is lost.')
        return
      }
      if (outcome.kind === 'failed') {
        setPhase('details')
        setError(outcome.message)
        return
      }

      if (mock) {
        /*
         * With no Razorpay account configured there is no bank to send a
         * webhook, so nothing would ever activate the event and this screen
         * would sit at "confirming" until it timed out.
         *
         * The server signs a real payload and runs the real webhook handler,
         * so what gets exercised below is the production activation path, not
         * a shortcut around it. The endpoint refuses to exist in production.
         */
        void api.simulatePayment(ev.id).catch(() => undefined)
      } else {
        // Optimistic only. Ignore the result: activation comes from the webhook.
        void api.verifyCheckout(ev.id, outcome.response).catch(() => undefined)
      }
      waitForActivation(ev.id)
    } catch (err) {
      setPhase('details')
      setError(err instanceof RequestError ? err.message : 'Could not start the payment.')
    }
  }

  if (phase === 'confirming') {
    return (
      <>
        <div className="topbar">
          <div className="ttl">Confirming your payment</div>
        </div>
        <div className="body">
          <div className="empty">
            <div className="auth__spinner" style={{ margin: '0 auto 16px' }} />
            <div className="empty__t">Waiting for the bank</div>
            <div className="empty__d">
              This usually takes a few seconds. You can leave this open, the event opens by itself
              once the payment clears.
            </div>
          </div>
        </div>
      </>
    )
  }

  if (phase === 'stuck') {
    return (
      <>
        <div className="topbar">
          <div className="ttl">Still confirming</div>
        </div>
        <div className="body">
          <div className="note" style={{ borderColor: 'var(--amb)', background: 'var(--amb-bg)' }}>
            <b>Your payment has not been confirmed yet.</b> If money left your account it will be
            confirmed shortly and the event opens on its own. Nothing is lost and you have not been
            charged twice. If it has not opened in a few minutes, tell us and we will check.
          </div>
          <div className="toolbar">
            <button className="btn" onClick={() => eventId && waitForActivation(eventId)}>
              Keep waiting
            </button>
            <button className="btn btn--sub" onClick={() => nav('/studio/events')}>
              Back to events
            </button>
          </div>
        </div>
      </>
    )
  }

  const chosen = tiers?.items.find((t) => t.code === tier)

  return (
    <>
      <div className="topbar">
        <div className="ttl">New event</div>
        <button className="btn" onClick={() => nav('/studio/events')}>
          Cancel
        </button>
      </div>

      <div className="body">
        <form onSubmit={start} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div className="panel" style={{ background: 'var(--page)' }}>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <label className="search" style={{ maxWidth: 320 }}>
                <input
                  required
                  autoFocus
                  placeholder="Whose wedding is it?"
                  aria-label="Event name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <input
                className="select"
                type="date"
                required
                aria-label="Event date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </div>
          </div>

          <div className="tiers">
            {(tiers?.items ?? []).map((t) => (
              <TierCard key={t.code} tier={t} selected={t.code === tier} onSelect={() => setTier(t.code)} />
            ))}
          </div>

          {error ? (
            <div className="note" style={{ borderColor: 'var(--red-line)', background: 'var(--red-bg)' }}>
              {error}
            </div>
          ) : null}

          <div className="toolbar">
            <button className="btn btn--pri btn--lg" type="submit" disabled={phase === 'paying'}>
              <Icon.Rupee />
              {phase === 'paying'
                ? 'Opening payment'
                : chosen
                  ? `Pay ${rupees(chosen.price_paise)} and create`
                  : 'Continue'}
            </button>
            <span style={{ fontSize: 13.5, color: 'var(--ink4)' }}>
              UPI, card or netbanking. The event opens as soon as the payment clears.
            </span>
          </div>
        </form>
      </div>
    </>
  )
}
