import React, { lazy, Suspense } from 'react'
import ReactDOM from 'react-dom/client'

import '@shared/styles/tokens.css'
import '@shared/styles/guest.css'

/*
 * Two apps share this bundle entry, split by path and loaded lazily.
 *
 * A guest on congested venue wifi must never download the admin panel, and the
 * admin panel must never pull in the camera code. Deciding here rather than with
 * a router keeps the guest payload at roughly 50KB gzipped.
 */
const isAdmin = window.location.pathname.startsWith('/admin')

const GuestApp = lazy(() =>
  import('./guest/GuestApp').then((m) => ({ default: m.GuestApp })),
)
const AdminApp = lazy(() =>
  Promise.all([import('./admin/AdminApp'), import('@shared/styles/app.css')]).then(([m]) => ({
    default: m.AdminApp,
  })),
)

function applyTheme() {
  if (isAdmin) {
    const saved = localStorage.getItem('frame.theme')
    const dark = saved
      ? saved === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
    return
  }
  /*
   * The guest gallery is always light, whatever the phone is set to.
   *
   * The photographs are the content and a white ground is how galleries and
   * print present them; a dark surround tints how the work is judged. It also
   * means the studio's gallery looks the same to every guest instead of
   * depending on a setting none of them think about.
   */
  document.documentElement.setAttribute('data-theme', 'light')
}
applyTheme()

async function start() {
  // Mocks only when no real backend is configured. Set VITE_API_BASE in
  // .env.local to point at your FastAPI server and they step aside.
  if (import.meta.env.DEV && !import.meta.env.VITE_API_BASE) {
    const { setupWorker } = await import('msw/browser')
    const { handlers, guestHandlers, memberHandlers, adminHandlers, billingHandlers } = await import(
      '@shared/mocks/handlers'
    )
    await setupWorker(
      ...handlers,
      ...guestHandlers,
      ...memberHandlers,
      ...adminHandlers,
      ...billingHandlers,
    ).start({ onUnhandledRequest: 'bypass' })
  }

  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <Suspense fallback={<div className="auth"><div className="auth__spinner" /></div>}>
        {isAdmin ? <AdminApp /> : <GuestApp />}
      </Suspense>
    </React.StrictMode>,
  )
}

void start()
