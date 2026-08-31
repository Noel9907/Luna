import { useCallback, useEffect, useState } from 'react'
import { api, hasSession, setSignedOutHandler } from '@shared/lib/api'
import { Icon } from '@shared/components/ui'
import type { AdminOverview, AdminStudio, CreatedStudio } from '@shared/lib/types'
import { AdminLogin } from './AdminLogin'
import { StudiosTable } from './StudiosTable'

function rupees(paise: number) {
  const r = paise / 100
  if (r >= 100000) return `₹${(r / 100000).toFixed(1)}L`
  if (r >= 1000) return `₹${(r / 1000).toFixed(0)}k`
  return `₹${r.toFixed(0)}`
}

/**
 * Platform admin. Only you and your cousin sign in here.
 *
 * Lives in the web app rather than the desktop one: it is used occasionally,
 * from anywhere, and keeping it out of the studio app stops a studio owner from
 * ever loading code meant for the platform.
 */
export function AdminApp() {
  const [authed, setAuthed] = useState<boolean | null>(hasSession() ? null : false)
  const [denied, setDenied] = useState(false)

  const [overview, setOverview] = useState<AdminOverview | null>(null)
  const [studios, setStudios] = useState<AdminStudio[]>([])
  const [created, setCreated] = useState<CreatedStudio | null>(null)
  const [reset, setReset] = useState<{ username: string; initial_password: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const me = await api.me()
      if (me.role !== 'platform_admin') {
        setDenied(true)
        setAuthed(false)
        return
      }
      const [ov, st] = await Promise.all([api.adminOverview(), api.adminStudios()])
      setOverview(ov)
      setStudios(st.items)
      setAuthed(true)
    } catch {
      api.logout()
      setAuthed(false)
    }
  }, [])

  useEffect(() => {
    if (hasSession()) void load()
  }, [load])

  useEffect(() => {
    setSignedOutHandler(() => setAuthed(false))
    return () => setSignedOutHandler(null)
  }, [])

  if (authed === null) {
    return (
      <div className="auth">
        <div className="auth__spinner" aria-label="Loading" />
      </div>
    )
  }

  if (!authed) {
    return (
      <AdminLogin
        denied={denied}
        onSignedIn={() => {
          setDenied(false)
          void load()
        }}
      />
    )
  }

  return (
    <div className="admin">
      <header className="admin__bar">
        <div className="admin__brand">
          <span className="brand__mark">
            <Icon.Camera />
          </span>
          <span className="brand__name">Frame</span>
          <span className="brand__plan">Platform</span>
        </div>
        <button
          className="btn"
          onClick={() => {
            api.logout()
            setAuthed(false)
          }}
        >
          Sign out
        </button>
      </header>

      <div className="admin__body">
        {overview ? (
          <div className="stats">
            <div className="stat">
              <span className="stat__lbl">Studios</span>
              <div className="stat__v">{overview.studios_active}</div>
              <div className="stat__sub">
                {overview.studios_suspended
                  ? `${overview.studios_suspended} suspended`
                  : 'none suspended'}
              </div>
            </div>
            <div className="stat">
              <span className="stat__lbl">Live now</span>
              <div className={overview.events_live_now ? 'stat__v stat__v--amb' : 'stat__v'}>
                {overview.events_live_now}
              </div>
              <div className="stat__sub">
                {/* Capacity is roughly 8 concurrent events on one CPX31. */}
                {overview.events_live_now >= 8 ? 'at capacity' : 'of ~8 capacity'}
              </div>
            </div>
            <div className="stat">
              <span className="stat__lbl">Events this month</span>
              <div className="stat__v">{overview.events_this_month}</div>
              <div className="stat__sub">
                {overview.photos_this_month.toLocaleString('en-IN')} photographs
              </div>
            </div>
            <div className="stat">
              <span className="stat__lbl">Revenue this month</span>
              <div className="stat__v">{rupees(overview.revenue_this_month_paise)}</div>
              <div className="stat__sub">
                {rupees(overview.revenue_all_time_paise)} all time
              </div>
            </div>
            <div className="stat">
              <span className="stat__lbl">Server cost</span>
              <div className="stat__v">₹1,720</div>
              <div className="stat__sub">
                breaks even at {Math.ceil(172000 / 150000)} Basic events
              </div>
            </div>
          </div>
        ) : null}

        {created ? (
          <CredentialPanel
            title={`${created.name} created`}
            body="Send these over WhatsApp. The password is shown once and cannot be looked up again."
            username={created.owner_username}
            password={created.initial_password}
            onDone={() => setCreated(null)}
          />
        ) : null}

        {reset ? (
          <CredentialPanel
            title="New password issued"
            body="Their old password stopped working immediately. Send this over WhatsApp."
            username={reset.username}
            password={reset.initial_password}
            onDone={() => setReset(null)}
          />
        ) : null}

        {error ? (
          <div className="note" style={{ borderColor: 'var(--red-line)', background: 'var(--red-bg)' }}>
            {error}
          </div>
        ) : null}

        <StudiosTable
          studios={studios}
          onCreated={(s) => {
            setCreated(s)
            void load()
          }}
          onReset={(r) => setReset(r)}
          onChanged={() => void load()}
          onError={setError}
        />
      </div>
    </div>
  )
}

function CredentialPanel({
  title,
  body,
  username,
  password,
  onDone,
}: {
  title: string
  body: string
  username: string
  password: string
  onDone: () => void
}) {
  const [copied, setCopied] = useState(false)
  const message = `Frame sign-in
Username: ${username}
Password: ${password}

Open Frame Studio and sign in with these.`

  return (
    <div className="cred">
      <h3 className="cred__t">{title}</h3>
      <p className="cred__d">{body}</p>
      <div className="cred__row">
        <span className="cred__val">{username}</span>
        <span className="cred__val">{password}</span>
        <button
          className="btn"
          onClick={() => {
            void navigator.clipboard.writeText(message)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
          }}
        >
          {copied ? 'Copied' : 'Copy message'}
        </button>
        <button className="btn btn--sub" onClick={onDone}>
          Done
        </button>
      </div>
    </div>
  )
}
