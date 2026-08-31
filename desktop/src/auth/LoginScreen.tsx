import { useState } from 'react'
import { api, RequestError } from '@shared/lib/api'
import { Icon } from '@shared/components/ui'

export function LoginScreen({ onSignedIn }: { onSignedIn: () => void }) {
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
      /*
       * Never distinguish "no such account" from "wrong password". Doing so
       * confirms which usernames exist to anyone who asks.
       */
      setError(
        err instanceof RequestError && err.status === 401
          ? 'That username or password is not right.'
          : 'Could not reach the server. Check your connection.',
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

        <h1 className="auth__h1">Sign in to your studio</h1>
        <p className="auth__sub">
          Use the username and password sent to you. If you do not have them, ask whoever set up
          your studio.
        </p>

        <label className="auth__field">
          <span>Username or email</span>
          <input
            autoFocus
            required
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="kocheekkaran"
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

        <p className="auth__foot">
          Forgotten your password? It has to be reset for you. Message the studio that set you up.
        </p>
      </form>
    </div>
  )
}
