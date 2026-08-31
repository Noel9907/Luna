import { useState } from 'react'
import { api, RequestError } from '@shared/lib/api'
import { Icon } from '@shared/components/ui'
import type { AdminStudio, CreatedStudio } from '@shared/lib/types'

function rupees(paise: number) {
  return `₹${(paise / 100).toLocaleString('en-IN')}`
}

function since(iso: string | null) {
  if (!iso) return 'Never'
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  if (d === 0) return 'Today'
  if (d === 1) return 'Yesterday'
  if (d < 30) return `${d} days ago`
  return `${Math.floor(d / 30)} months ago`
}

export function StudiosTable({
  studios,
  onCreated,
  onReset,
  onChanged,
  onError,
}: {
  studios: AdminStudio[]
  onCreated: (s: CreatedStudio) => void
  onReset: (r: { username: string; initial_password: string }) => void
  onChanged: () => void
  onError: (m: string | null) => void
}) {
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ name: '', owner_username: '', city: '', phone: '' })
  const [pendingId, setPendingId] = useState<string | null>(null)

  async function create(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    onError(null)
    try {
      const s = await api.createStudio({
        name: form.name.trim(),
        owner_username: form.owner_username.trim().toLowerCase(),
        city: form.city.trim() || undefined,
        phone: form.phone.trim() || undefined,
      })
      setForm({ name: '', owner_username: '', city: '', phone: '' })
      setAdding(false)
      onCreated(s)
    } catch (err) {
      onError(err instanceof RequestError ? err.message : 'Could not create that studio.')
    } finally {
      setBusy(false)
    }
  }

  async function resetPassword(s: AdminStudio) {
    setPendingId(s.id)
    onError(null)
    try {
      onReset(await api.resetStudioPassword(s.id))
    } catch (err) {
      onError(err instanceof RequestError ? err.message : 'Could not reset that password.')
    } finally {
      setPendingId(null)
    }
  }

  async function toggle(s: AdminStudio) {
    setPendingId(s.id)
    onError(null)
    try {
      await api.setStudioStatus(s.id, s.status === 'active' ? 'suspended' : 'active')
      onChanged()
    } catch (err) {
      onError(err instanceof RequestError ? err.message : 'Could not change that studio.')
    } finally {
      setPendingId(null)
    }
  }

  return (
    <>
      <div className="toolbar">
        <h2 className="admin__h2">Studios</h2>
        {!adding ? (
          <button className="btn btn--pri" style={{ marginLeft: 'auto' }} onClick={() => setAdding(true)}>
            <Icon.Plus />
            Add studio
          </button>
        ) : null}
      </div>

      {adding ? (
        <div className="panel">
          <h3 style={{ margin: '0 0 4px', fontSize: 15, fontWeight: 600 }}>Add a studio</h3>
          <p style={{ margin: '0 0 16px', fontSize: 14, color: 'var(--ink3)' }}>
            This creates the studio and its owner account together. We generate a password for you
            to send over WhatsApp. The phone number is how you confirm who is calling if they ever
            lose it.
          </p>
          <form onSubmit={create} style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <label className="search" style={{ maxWidth: 230 }}>
              <input
                required
                autoFocus
                placeholder="Studio name"
                aria-label="Studio name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </label>
            <label className="search" style={{ maxWidth: 190 }}>
              <input
                required
                pattern="[a-zA-Z0-9._\-]+"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                placeholder="owner username"
                aria-label="Owner username"
                value={form.owner_username}
                onChange={(e) => setForm({ ...form, owner_username: e.target.value })}
              />
            </label>
            <label className="search" style={{ maxWidth: 150 }}>
              <input
                placeholder="City"
                aria-label="City"
                value={form.city}
                onChange={(e) => setForm({ ...form, city: e.target.value })}
              />
            </label>
            <label className="search" style={{ maxWidth: 180 }}>
              <input
                placeholder="Phone"
                aria-label="Phone"
                inputMode="tel"
                value={form.phone}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
              />
            </label>
            <button className="btn btn--pri" type="submit" disabled={busy}>
              {busy ? 'Creating' : 'Create studio'}
            </button>
            <button
              className="btn"
              type="button"
              onClick={() => {
                setAdding(false)
                onError(null)
              }}
            >
              Cancel
            </button>
          </form>
        </div>
      ) : null}

      {studios.length === 0 ? (
        <div className="empty">
          <div className="empty__t">No studios yet</div>
          <div className="empty__d">
            Add your first studio and send them their sign-in over WhatsApp.
          </div>
          <button className="btn btn--pri" onClick={() => setAdding(true)}>
            Add studio
          </button>
        </div>
      ) : (
        <div className="tbl">
          <div className="tbl__scroll">
            <table>
              <thead>
                <tr>
                  <th>Studio</th>
                  <th>Status</th>
                  <th>Events</th>
                  <th>This month</th>
                  <th>Revenue</th>
                  <th>Last event</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {studios.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <span className="fname">{s.name}</span>
                      <span className="fmeta">
                        {s.owner_username}
                        {s.city ? ` · ${s.city}` : ''}
                        {s.phone ? ` · ${s.phone}` : ''}
                      </span>
                    </td>
                    <td>
                      {s.status === 'active' ? (
                        <span className="pill pill--done">
                          <span className="pill__dot" />
                          Active
                        </span>
                      ) : (
                        <span className="pill pill--fail">
                          <span className="pill__dot" />
                          Suspended
                        </span>
                      )}
                    </td>
                    <td className="tnum">{s.events_total}</td>
                    <td className="tnum">{s.events_this_month}</td>
                    <td className="tnum">{rupees(s.revenue_paise)}</td>
                    <td className="tnum" style={{ color: 'var(--ink4)' }}>
                      {since(s.last_event_at)}
                    </td>
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                      <button
                        className="btn"
                        disabled={pendingId === s.id}
                        onClick={() => void resetPassword(s)}
                      >
                        Reset password
                      </button>{' '}
                      <button
                        className={s.status === 'active' ? 'btn btn--dgr' : 'btn'}
                        disabled={pendingId === s.id}
                        onClick={() => void toggle(s)}
                      >
                        {s.status === 'active' ? 'Suspend' : 'Reactivate'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
