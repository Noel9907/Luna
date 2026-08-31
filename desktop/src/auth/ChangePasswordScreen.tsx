import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, RequestError } from '@shared/lib/api'

const MIN = 10

/** Voluntary for now. Reachable from Password in the sidebar. */
export function ChangePasswordScreen() {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me })

  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const tooShort = next.length > 0 && next.length < MIN
  const mismatch = confirm.length > 0 && next !== confirm
  const ok = next.length >= MIN && next === confirm && current.length > 0

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!ok) return
    setBusy(true)
    setError(null)
    try {
      await api.changePassword(current, next)
      setCurrent('')
      setNext('')
      setConfirm('')
      setDone(true)
    } catch (err) {
      setError(
        err instanceof RequestError
          ? err.status === 401
            ? 'That current password is not right.'
            : err.message
          : 'Could not save. Check your connection.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="topbar">
        <div className="ttl">
          Password
          {me ? <span>Signed in as {me.username}</span> : null}
        </div>
      </div>

      <div className="body">
        <div className="panel" style={{ maxWidth: 460, background: 'var(--page)' }}>
          <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <p style={{ margin: 0, fontSize: 14, color: 'var(--ink3)' }}>
              If your password was sent to you over WhatsApp, it is sitting in a chat history on at
              least two phones. Replacing it here is worth doing.
            </p>

            <label className="auth__field">
              <span>Current password</span>
              <input
                type="password"
                required
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
              />
            </label>

            <label className="auth__field">
              <span>New password</span>
              <input type="password" required value={next} onChange={(e) => setNext(e.target.value)} />
              <small className={tooShort ? 'auth__hint auth__hint--bad' : 'auth__hint'}>
                At least {MIN} characters.
              </small>
            </label>

            <label className="auth__field">
              <span>New password again</span>
              <input
                type="password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
              {mismatch ? (
                <small className="auth__hint auth__hint--bad">These do not match.</small>
              ) : null}
            </label>

            {error ? <div className="auth__err">{error}</div> : null}
            {done ? (
              <div className="note" style={{ borderColor: 'var(--grn)', background: 'var(--grn-bg)' }}>
                Password changed. Everywhere else you were signed in has been signed out.
              </div>
            ) : null}

            <button className="btn btn--pri btn--lg" type="submit" disabled={!ok || busy}>
              {busy ? 'Saving' : 'Change password'}
            </button>
          </form>
        </div>
      </div>
    </>
  )
}
