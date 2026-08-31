import { isRouteErrorResponse, Link, useRouteError } from 'react-router-dom'

/**
 * Catches both unmatched routes and anything a route throws.
 *
 * A photographer hitting this mid-event needs a way back to the thing they were
 * doing, not a stack trace. So: plain language, one obvious action.
 */
export function ErrorPage() {
  const error = useRouteError()

  let title = 'Something went wrong'
  let detail = 'The page could not be loaded. Going back to your events is the quickest way out.'

  if (isRouteErrorResponse(error)) {
    if (error.status === 404) {
      title = 'That page does not exist'
      detail = 'The link may be out of date, or the event may have been removed.'
    } else {
      title = `Error ${error.status}`
      detail = error.statusText || detail
    }
  } else if (error instanceof Error) {
    detail = error.message
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        padding: '24px',
        background: 'var(--page)',
      }}
    >
      <div className="empty" style={{ maxWidth: 460, width: '100%' }}>
        <div className="empty__t">{title}</div>
        <div className="empty__d">{detail}</div>
        <Link to="/studio/events" className="btn btn--pri" style={{ textDecoration: 'none' }}>
          Back to events
        </Link>
      </div>
    </div>
  )
}
