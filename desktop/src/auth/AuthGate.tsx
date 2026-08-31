import { useCallback, useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, hasSession, setSignedOutHandler } from '@shared/lib/api'
import type { Me } from '@shared/lib/types'
import { LoginScreen } from './LoginScreen'
import { FirstPasswordScreen } from './FirstPasswordScreen'

type State = 'checking' | 'signed_out' | 'must_change_password' | 'ready'

/**
 * Everything behind sign-in.
 *
 * Accounts are created by hand: the platform creates a studio, the owner creates
 * photographers, credentials go out over WhatsApp. No self-signup, no route into
 * the app without a password someone handed over.
 *
 * The forced password change is enforced here rather than suggested. The server
 * refuses every studio endpoint with PASSWORD_CHANGE_REQUIRED while the flag is
 * set, so letting someone past this point would sign them in and then fail
 * every request they made. `/me` and the password change itself are the only
 * two endpoints that still work in that state, which is exactly what this
 * screen needs.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient()
  const [state, setState] = useState<State>(hasSession() ? 'checking' : 'signed_out')
  const [me, setMe] = useState<Me | null>(null)

  const load = useCallback(async () => {
    try {
      const who = await api.me()
      setMe(who)
      setState(who.must_change_password ? 'must_change_password' : 'ready')
    } catch {
      api.logout()
      setMe(null)
      setState('signed_out')
    }
  }, [])

  useEffect(() => {
    if (hasSession()) void load()
  }, [load])

  /* A session that dies mid-use drops straight back to sign-in. */
  useEffect(() => {
    setSignedOutHandler(() => {
      qc.clear()
      setMe(null)
      setState('signed_out')
    })
    return () => setSignedOutHandler(null)
  }, [qc])

  if (state === 'checking') {
    return (
      <div className="auth">
        <div className="auth__spinner" aria-label="Signing in" />
      </div>
    )
  }

  if (state === 'signed_out') return <LoginScreen onSignedIn={load} />

  if (state === 'must_change_password') {
    return (
      <FirstPasswordScreen
        username={me?.username ?? ''}
        onChanged={() => {
          // Anything cached during the blocked state is an error response.
          qc.clear()
          void load()
        }}
      />
    )
  }

  return <>{children}</>
}
