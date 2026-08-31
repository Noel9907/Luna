import { useState } from 'react'
import { api, RequestError } from '@shared/lib/api'
import { Icon } from '@shared/components/ui'

const MIN = 10

/**
 * Shown once, before anything else, to an account that still holds the
 * password whoever created it chose.
 *
 * This is not a nag screen that can be dismissed. The server refuses every
 * studio endpoint with PASSWORD_CHANGE_REQUIRED until the password is
 * replaced, so without this the app would sign someone in and then fail every
 * request they made, which looks like a broken product rather than a step they
 * have not done yet.
 *
 * It sits outside the studio layout on purpose. The layout's pages all call
 * endpoints that are currently blocked, so rendering it around this screen
 * would fill the window with errors behind the form.
 */
export function FirstPasswordScreen({
  username,
  onChanged,
}: {
  username: string
  onChanged: () => void
}) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const tooShort = next.length > 0 && next.length < MIN
  const mismatch = confirm.length > 0 && next !== confirm
  const reused = next.length > 0 && next === current
  const ok = next.length >= MIN && next === confirm && current.length > 0 && !reused

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!ok) return
    setBusy(true)
    setError(null)
    try {
      // Returns a fresh token pair, because changing a password revokes every
      // token this account had, including the one that made this request.
      await api.changePassword(current, next)
      onChanged()
    } catch (err) {
      setError(
        err instanceof RequestError
          ? err.status === 401
            ? 'That password is not the one you were sent.'
            : err.message
          : 'Could not save. Check your connection.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth">
      <form className="auth__card" onSubmit={submit}>
        <div className="auth__brand">
          <span className="brand__mark">
            <Icon.Camera />
          </span>
          <span className="brand__name">Frame</span>
        </div>

        <h1 className="auth__h1">Choose your own password</h1>
        <p className="auth__sub">
          The password you signed in with was chosen by someone else and sent to you over WhatsApp,
          so it is sitting in a chat history on at least two phones. Replace it before you start.
        </p>

        <label className="auth__field">
          <span>The password you were sent</span>
          <input
            type="password"
            required
            autoFocus
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>

        <label className="auth__field">
          <span>Your new password</span>
          <input
            type="password"
            required
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
          <small className={tooShort || reused ? 'auth__hint auth__hint--bad' : 'auth__hint'}>
            {reused ? 'Pick something different from the one you were sent.' : `At least ${MIN} characters.`}
          </small>
        </label>

        <label className="auth__field">
          <span>Your new password again</span>
          <input
            type="password"
            required
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
          {mismatch ? <small className="auth__hint auth__hint--bad">These do not match.</small> : null}
        </label>

        {error ? <div className="auth__err">{error}</div> : null}

        <button className="btn btn--pri btn--wide btn--lg" type="submit" disabled={!ok || busy}>
          {busy ? 'Saving' : 'Save and continue'}
        </button>

        <p className="auth__foot">
          Signed in as {username}. Everywhere else this account is signed in will be signed out.
        </p>
      </form>
    </div>
  )
}
