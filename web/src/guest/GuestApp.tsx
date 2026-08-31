import { useCallback, useEffect, useRef, useState } from 'react'
import {
  clearSession,
  guestApi,
  GuestError,
  loadSession,
  saveSession,
} from '@shared/lib/guestApi'
import { SELFIE_ERROR_MESSAGES, type GuestEventInfo, type GuestPhoto } from '@shared/lib/types'
import { SelfieScreen } from './SelfieScreen'
import { GalleryScreen } from './GalleryScreen'

type Stage = 'loading' | 'landing' | 'selfie' | 'gallery' | 'dead'

/** The QR encodes /g/<token>, so the token is simply the last path segment. */
function tokenFromUrl() {
  const m = window.location.pathname.match(/\/g\/([^/]+)/)
  return m?.[1] ?? ''
}

export function GuestApp() {
  const qrToken = tokenFromUrl()

  const [stage, setStage] = useState<Stage>('loading')
  const [event, setEvent] = useState<GuestEventInfo | null>(null)
  const [session, setSession] = useState<string | null>(() => loadSession(qrToken))
  const [fatal, setFatal] = useState<string | null>(null)

  const [busy, setBusy] = useState(false)
  const [selfieError, setSelfieError] = useState<string | null>(null)

  const [photos, setPhotos] = useState<GuestPhoto[]>([])
  const [pending, setPending] = useState<GuestPhoto[]>([])
  const cursor = useRef<string | undefined>(undefined)

  /* Load the event, and skip straight to the gallery for a returning guest. */
  useEffect(() => {
    if (!qrToken) {
      setFatal('This link is incomplete. Scan the QR code again.')
      setStage('dead')
      return
    }
    guestApi
      .event(qrToken)
      .then(async (info) => {
        setEvent(info)
        const existing = loadSession(qrToken)
        if (existing) {
          setSession(existing)
          const first = await guestApi.photos(existing)
          setPhotos(first.items)
          cursor.current = first.latest_cursor
          setStage('gallery')
        } else {
          setStage('landing')
        }
      })
      .catch((e) => {
        setFatal(
          e instanceof GuestError && e.status === 404
            ? 'This event could not be found. The link may have expired.'
            : 'We could not load this event. Check your connection and try again.',
        )
        setStage('dead')
      })
  }, [qrToken])

  /* Studio branding, applied by overriding the accent token. */
  useEffect(() => {
    const c = event?.branding.mode === 'studio' ? event.branding.brand_color : null
    if (c) document.documentElement.style.setProperty('--pri', c)
  }, [event])

  async function beginConsent() {
    setBusy(true)
    try {
      const s = await guestApi.openSession(qrToken)
      saveSession(qrToken, s.session_token)
      setSession(s.session_token)
      setStage('selfie')
    } catch {
      setFatal('We could not start. Please try again.')
      setStage('dead')
    } finally {
      setBusy(false)
    }
  }

  async function submitSelfie(blob: Blob) {
    if (!session) return
    setBusy(true)
    setSelfieError(null)
    try {
      await guestApi.submitSelfie(session, blob)
      const first = await guestApi.photos(session)
      setPhotos(first.items)
      cursor.current = first.latest_cursor
      setStage('gallery')
    } catch (e) {
      /*
       * Selfie rejection is the most common failure a guest will ever hit, so
       * each code gets its own retake instruction. A generic message here makes
       * the whole product feel broken.
       */
      const code = e instanceof GuestError ? e.code : ''
      setSelfieError(
        SELFIE_ERROR_MESSAGES[code] ??
          (e instanceof GuestError ? e.message : 'Something went wrong. Try again.'),
      )
    } finally {
      setBusy(false)
    }
  }

  /*
   * New photographs are held aside rather than inserted. Inserting reflows the
   * grid under the guest's thumb while they are looking at something.
   */
  const poll = useCallback(async () => {
    if (!session) return
    try {
      const res = await guestApi.photos(session, cursor.current)
      if (res.items.length) {
        cursor.current = res.latest_cursor
        setPending((prev) => {
          const seen = new Set(prev.map((p) => p.photo_id))
          return [...res.items.filter((p) => !seen.has(p.photo_id)), ...prev]
        })
      }
    } catch {
      // A failed poll is not worth telling anyone about. The next one runs in 20s.
    }
  }, [session])

  function showPending() {
    setPhotos((prev) => {
      const seen = new Set(prev.map((p) => p.photo_id))
      return [...pending.filter((p) => !seen.has(p.photo_id)), ...prev]
    })
    setPending([])
  }

  async function deleteMe() {
    if (!session) return
    try {
      await guestApi.deleteMe(session)
    } finally {
      clearSession(qrToken)
      setSession(null)
      setPhotos([])
      setPending([])
      cursor.current = undefined
      setStage('landing')
    }
  }

  if (stage === 'loading') {
    return (
      <div className="g-screen g-screen--center">
        <div className="g-spinner" aria-label="Loading" />
      </div>
    )
  }

  if (stage === 'dead') {
    return (
      <div className="g-screen g-screen--center">
        <div className="g-pad">
          <h2 className="g-h2">Something is wrong</h2>
          <p className="g-p">{fatal}</p>
        </div>
      </div>
    )
  }

  if (stage === 'landing' && event) {
    const studio = event.branding.mode === 'studio' ? event.branding.studio_name : null
    return (
      <div className="g-screen">
        <div className="g-pad g-pad--tall">
          {studio ? <div className="g-brandline">{studio}</div> : <div className="g-brandline">Frame</div>}
          <h1 className="g-h1">{event.event_name}</h1>
          <p className="g-lede">
            Take one selfie and your photographs from tonight will appear here, as the
            photographers upload them.
          </p>

          {!event.accepting_guests ? (
            <div className="g-err">This event has ended and is no longer accepting guests.</div>
          ) : (
            <>
              <div className="g-consent">
                <h2 className="g-consent__t">Before you do</h2>
                <ul>
                  <li>Your selfie is used to recognise your face in the photographs, nothing else.</li>
                  {/* From the event: the window is agreed per studio. */}
                  <li>
                    Your face data is deleted automatically after{' '}
                    {event.face_retention_days} days.
                  </li>
                  <li>You can delete it yourself at any time from your gallery.</li>
                  <li>It is never shared with anyone or used to identify you elsewhere.</li>
                </ul>
              </div>
              <div className="g-actions">
                <button className="g-btn g-btn--pri" disabled={busy} onClick={beginConsent}>
                  {busy ? 'One moment' : 'I agree, take my selfie'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    )
  }

  if (stage === 'selfie') {
    return (
      <SelfieScreen
        busy={busy}
        error={selfieError}
        onSubmit={submitSelfie}
        onBack={() => setStage('landing')}
      />
    )
  }

  return (
    <GalleryScreen
      eventName={event?.event_name ?? ''}
      studioName={event?.branding.mode === 'studio' ? event.branding.studio_name : null}
      photos={photos}
      pendingCount={pending.length}
      onShowPending={showPending}
      onPoll={poll}
      onDeleteMe={deleteMe}
    />
  )
}
