import { useState } from 'react'
import { api, RequestError } from '@shared/lib/api'
import { Icon } from '@shared/components/ui'

export function AdminLogin({
  denied,
  onSignedIn,
}: {
  denied: boolean
  onSignedIn: () => void
}) {
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.login(identifier.trim(), password)
      onSignedIn()
    } catch (err) {
      setError(
        err instanceof RequestError && err.status === 401
          ? 'That username or password is not right.'
          : 'Could not reach the server.',
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
          <span className="brand__plan">Platform</span>
        </div>

        <h1 className="auth__h1">Platform sign in</h1>
        <p className="auth__sub">
          For you and your cousin only. Studio accounts cannot sign in here.
        </p>

        {denied ? (
          <div className="auth__err">
            That account is a studio, not a platform admin. Use Frame Studio instead.
          </div>
        ) : null}

        <label className="auth__field">
          <span>Username</span>
          <input
            autoFocus
            required
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
          />
        </label>

        <label className="auth__field">
          <span>Password</span>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {error ? <div className="auth__err">{error}</div> : null}

        <button className="btn btn--pri btn--wide btn--lg" type="submit" disabled={busy}>
          {busy ? 'Signing in' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
