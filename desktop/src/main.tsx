import React from 'react'
import ReactDOM from 'react-dom/client'
import { createHashRouter, Navigate, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import '@shared/styles/tokens.css'
import '@shared/styles/app.css'

import { StudioLayout } from './studio/StudioLayout'
import { EventsPage } from './studio/EventsPage'
import { EventPage } from './studio/EventPage'
import { ErrorPage } from '@shared/components/ErrorPage'
import { ActivityPage } from '@shared/components/Placeholder'
import { BrandingPage } from './studio/BrandingPage'
import { BillingPage } from './billing/BillingPage'
import { NewEventPage } from './billing/NewEventPage'
import { PhotographersPage } from './studio/PhotographersPage'
import { AuthGate } from './auth/AuthGate'
import { ChangePasswordScreen } from './auth/ChangePasswordScreen'

/*
 * Hash router, not browser router. A packaged Electron app loads from file://,
 * where path-based routing has no server to fall back to and every deep link
 * 404s. The hash lives entirely in the client.
 */
const router = createHashRouter([
  { path: '/', element: <Navigate to="/studio/events" replace /> },
  {
    path: '/studio',
    element: <StudioLayout />,
    errorElement: <ErrorPage />,
    children: [
      { index: true, element: <Navigate to="/studio/events" replace /> },
      { path: 'events', element: <EventsPage /> },
      { path: 'events/new', element: <NewEventPage /> },
      { path: 'events/:eventId', element: <EventPage /> },
      { path: 'photographers', element: <PhotographersPage /> },
      { path: 'activity', element: <ActivityPage /> },
      { path: 'billing', element: <BillingPage /> },
      { path: 'branding', element: <BrandingPage /> },
      { path: 'password', element: <ChangePasswordScreen /> },
    ],
  },
  { path: '*', element: <ErrorPage /> },
])

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      // Venue wifi. Fewer surprise refetches, a retry budget that assumes the
      // network is bad rather than broken.
      refetchOnWindowFocus: false,
      retry: 2,
      staleTime: 3000,
    },
  },
})

function applyTheme() {
  const saved = localStorage.getItem('frame.theme')
  const dark = saved ? saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
}
applyTheme()

async function start() {
  // Mocks only when no real backend is configured. Set VITE_API_BASE in
  // .env.local to point at your FastAPI server and they step aside.
  if (import.meta.env.DEV && !import.meta.env.VITE_API_BASE) {
    const { setupWorker } = await import('msw/browser')
    const { handlers, memberHandlers, billingHandlers } = await import(
      '@shared/mocks/handlers'
    )
    await setupWorker(...handlers, ...memberHandlers, ...billingHandlers).start({
      onUnhandledRequest: 'bypass',
    })
  }

  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <QueryClientProvider client={qc}>
        <AuthGate>
          <RouterProvider router={router} />
        </AuthGate>
      </QueryClientProvider>
    </React.StrictMode>,
  )
}

void start()
