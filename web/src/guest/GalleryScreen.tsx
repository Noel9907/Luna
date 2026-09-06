import { useEffect, useRef, useState } from 'react'
import type { GuestPhoto } from '@shared/lib/types'

const POLL_MS = 20_000

/**
 * The gallery polls a plain database read. No model runs, nothing is charged,
 * so a guest refreshing every twenty seconds for six hours costs nothing.
 */
export function GalleryScreen({
  eventName,
  studioName,
  photos,
  totalCount,
  hasMore,
  onLoadMore,
  onDownloadAll,
  pendingCount,
  onShowPending,
  onPoll,
  onDeleteMe,
}: {
  eventName: string
  studioName: string | null
  photos: GuestPhoto[]
  totalCount: number
  hasMore: boolean
  onLoadMore: () => void
  onDownloadAll: () => Promise<void>
  pendingCount: number
  onShowPending: () => void
  onPoll: () => void
  onDeleteMe: () => void
}) {
  const [open, setOpen] = useState<number | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [zipping, setZipping] = useState(false)
  const pollRef = useRef(onPoll)
  pollRef.current = onPoll
  const sentinel = useRef<HTMLDivElement | null>(null)
  const moreRef = useRef(onLoadMore)
  moreRef.current = onLoadMore

  useEffect(() => {
    const t = setInterval(() => pollRef.current(), POLL_MS)
    // Catch up immediately when the phone comes back from sleep or a lock screen.
    const onVisible = () => {
      if (document.visibilityState === 'visible') pollRef.current()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(t)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])

  /*
   * Infinite scroll, via a sentinel below the grid. rootMargin loads the next
   * page while it is still a screen away, so the guest never watches a spinner.
   *
   * onLoadMore is held in a ref and the effect depends only on hasMore. Passing
   * the callback itself would tear down and rebuild the observer on every
   * render, and an observer that is re-created mid-scroll can miss the
   * intersection entirely and simply stop paging.
   */
  useEffect(() => {
    const el = sentinel.current
    if (!el || !hasMore) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) moreRef.current()
      },
      { rootMargin: '600px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [hasMore])

  // Close the lightbox with the hardware back button rather than leaving the page.
  useEffect(() => {
    if (open === null) return
    const onPop = () => setOpen(null)
    window.history.pushState({ lightbox: true }, '')
    window.addEventListener('popstate', onPop)
    return () => {
      window.removeEventListener('popstate', onPop)
      if (window.history.state?.lightbox) window.history.back()
    }
  }, [open])

  const current = open !== null ? photos[open] : null

  return (
    <div className="g-gallery">
      <header className="g-head">
        <div className="g-head__id">
          <div className="g-event">{eventName}</div>
          {studioName ? <div className="g-studio">{studioName}</div> : null}
        </div>
        <div className="g-head__right">
          <div className="g-count tnum">{totalCount || photos.length}</div>
          {photos.length > 0 ? (
            <button
              className="g-dlbtn"
              disabled={zipping}
              onClick={async () => {
                setZipping(true)
                try {
                  await onDownloadAll()
                } finally {
                  setZipping(false)
                }
              }}
            >
              {zipping ? 'Preparing' : 'Download all'}
            </button>
          ) : null}
        </div>
      </header>

      {pendingCount > 0 ? (
        <button className="g-new" onClick={onShowPending}>
          {pendingCount} new {pendingCount === 1 ? 'photograph' : 'photographs'}
          <span>Show</span>
        </button>
      ) : null}

      {photos.length === 0 ? (
        <div className="g-empty">
          <div className="g-empty__t">Nothing yet</div>
          <p className="g-p">
            The photographers have not uploaded anything you appear in. This page updates on its
            own, so leave it open.
          </p>
        </div>
      ) : (
        <div className="g-grid">
          {photos.map((p, i) => (
            <button
              key={p.photo_id}
              className="g-cell"
              onClick={() => setOpen(i)}
              aria-label={`Open photograph ${i + 1}`}
            >
              {/* An <img>, not a background: backgrounds cannot lazy-load,
                  and this gallery runs to thousands of files on venue wifi. */}
              <img src={p.thumbnail_url} alt="" loading="lazy" decoding="async" />
            </button>
          ))}
        </div>
      )}

      {hasMore ? (
        <div ref={sentinel} className="g-more">
          <div className="g-spinner" aria-label="Loading more photographs" />
        </div>
      ) : null}

      <footer className="g-foot">
        {confirmDelete ? (
          <div className="g-confirm">
            <p className="g-p">
              This deletes your face data and your list of photographs. The photographs themselves
              belong to the studio and stay with them.
            </p>
            <div className="g-actions g-actions--row">
              <button className="g-btn g-btn--danger" onClick={onDeleteMe}>
                Delete my data
              </button>
              <button className="g-btn" onClick={() => setConfirmDelete(false)}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button className="g-link" onClick={() => setConfirmDelete(true)}>
            Delete my data
          </button>
        )}
      </footer>

      {current ? (
        <div className="g-light" role="dialog" aria-modal="true">
          <button className="g-light__close" onClick={() => setOpen(null)} aria-label="Close">
            ×
          </button>
          {/* The only place the full-size original is fetched. */}
          <div className="g-light__img">
            <img src={current.full_url} alt="" decoding="async" />
          </div>
          <div className="g-light__bar">
            {/*
              A plain link, not a fetch. The signed URL is on this same origin,
              which is the condition for `download` to save the file instead of
              navigating to it.
            */}
            <a className="g-light__save" href={current.full_url} download={`photograph-${(open ?? 0) + 1}.jpg`}>
              Save
            </a>
            <button
              className="g-light__nav"
              disabled={open === 0}
              onClick={() => setOpen((n) => (n ?? 0) - 1)}
            >
              Previous
            </button>
            <span className="tnum">
              {(open ?? 0) + 1} of {photos.length}
            </span>
            <button
              className="g-light__nav"
              disabled={open === photos.length - 1}
              onClick={() => setOpen((n) => (n ?? 0) + 1)}
            >
              Next
            </button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
