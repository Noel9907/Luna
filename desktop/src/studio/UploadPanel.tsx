import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useUploadQueue, type QueueItem } from '../lib/uploadQueue'
import { loadQuality, presetFor, QUALITY_PRESETS, saveQuality, type QualityMode } from '../lib/settings'
import { formatBytes, Icon } from '@shared/components/ui'

function label(i: QueueItem) {
  if (i.status === 'resizing') return 'Preparing'
  if (i.status === 'ready') return 'Waiting'
  return null
}

function QualityPicker({ value, onChange }: { value: QualityMode; onChange: (m: QualityMode) => void }) {
  return (
    <div className="quality">
      {QUALITY_PRESETS.map((p) => (
        <button
          key={p.id}
          type="button"
          className={p.id === value ? 'quality__opt quality__opt--on' : 'quality__opt'}
          onClick={() => onChange(p.id)}
          aria-pressed={p.id === value}
        >
          <span className="quality__hd">
            <span className="quality__lbl">{p.label}</span>
            <span className="quality__sz tnum">{p.approxPerPhoto}</span>
          </span>
          <span className="quality__d">{p.detail}</span>
        </button>
      ))}
    </div>
  )
}

export function UploadPanel({ eventId }: { eventId: string }) {
  const qc = useQueryClient()
  const [quality, setQuality] = useState<QualityMode>(loadQuality)
  const [showQuality, setShowQuality] = useState(false)
  const q = useUploadQueue(eventId, quality)
  const s = q.snapshot

  const inFlight = s.total > 0 && s.done + s.failed < s.total
  const pct = s.total ? Math.round(((s.done + s.failed) / s.total) * 100) : 0

  // A stuck round and a slow one both sit at 0%, so say which out loud.
  const [stalledFor, setStalledFor] = useState(0)
  const mark = useRef({ progress: -1, at: Date.now() })
  useEffect(() => {
    const settledCount = s.done + s.failed
    const signal = settledCount * 1e12 + s.uploadBytes
    if (signal !== mark.current.progress) {
      mark.current = { progress: signal, at: Date.now() }
      setStalledFor(0)
      return
    }
    if (!inFlight || s.paused) return
    const t = setInterval(
      () => setStalledFor(Math.round((Date.now() - mark.current.at) / 1000)),
      1000,
    )
    return () => clearInterval(t)
  }, [s.done, s.failed, s.uploadBytes, inFlight, s.paused])

  const stalled = stalledFor >= 25

  function pickQuality(m: QualityMode) {
    setQuality(m)
    saveQuality(m)
    setShowQuality(false)
  }

  // Refresh the tables once, on the transition into "round finished".
  const settled = s.total > 0 && !inFlight && !s.running
  const wasSettled = useRef(false)
  useEffect(() => {
    if (settled && !wasSettled.current) {
      qc.invalidateQueries({ queryKey: ['stats', eventId] })
      qc.invalidateQueries({ queryKey: ['photos', eventId] })
    }
    wasSettled.current = settled
  }, [settled, eventId, qc])

  const qualityBar = (
    <div className="qbar">
      <button className="qbar__toggle" onClick={() => setShowQuality((v) => !v)}>
        Quality: <b>{presetFor(quality).label}</b> <Icon.Chevron />
      </button>
      {s.watching ? (
        <span className="qbar__watch">
          <span className="pill__dot" />
          Watching {s.watching.split(/[\\/]/).pop()}
          <button className="qbar__link" onClick={q.stopWatching}>
            Stop
          </button>
        </span>
      ) : null}
    </div>
  )

  if (s.total === 0) {
    return (
      <div className="drop">
        <div className="drop__t">Nothing queued yet</div>
        <div className="drop__d">
          Point Frame at the folder your camera imports into and it uploads everything that appears
          there, all night, without you touching it. Or choose photographs by hand.
        </div>
        <div className="drop__acts">
          <button className="btn btn--pri" onClick={q.chooseWatchFolder}>
            <Icon.Upload />
            Watch a folder
          </button>
          <button className="btn" onClick={q.addFiles}>
            Choose photographs
          </button>
        </div>
        {showQuality ? <QualityPicker value={quality} onChange={pickQuality} /> : null}
        {qualityBar}
      </div>
    )
  }

  return (
    <div className="queue">
      <div className="queue__head">
        <div>
          <span className="queue__t">
            {inFlight ? 'Uploading' : s.watching ? 'Waiting for photographs' : 'Round complete'}
            <span className="tnum">
              {s.done + s.failed} of {s.total}
            </span>
          </span>
          <div className="queue__sub tnum">
            {formatBytes(s.sourceBytes)} on disk, {formatBytes(s.uploadBytes)} sent
          </div>
          <div className="queue__stages tnum">
            {(
              [
                ['waiting', 'queued'],
                ['resizing', 'preparing'],
                ['ready', 'ready to send'],
                ['uploading', 'uploading'],
                ['done', 'sent'],
                ['failed', 'failed'],
              ] as const
            )
              .filter(([k]) => s.counts[k] > 0)
              .map(([k, text]) => (
                <span key={k} className={k === 'failed' ? 'queue__stage queue__stage--bad' : 'queue__stage'}>
                  {s.counts[k]} {text}
                </span>
              ))}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {s.failed > 0 ? (
            <button className="btn btn--dgr" onClick={q.retryFailed}>
              Retry {s.failed}
            </button>
          ) : null}
          {inFlight ? (
            s.paused ? (
              <button className="btn btn--pri" onClick={q.resume}>
                Resume
              </button>
            ) : (
              <button className="btn btn--sub" onClick={q.pause}>
                Pause
              </button>
            )
          ) : null}
          {!s.watching ? (
            <button className="btn btn--sub" onClick={q.chooseWatchFolder}>
              Watch a folder
            </button>
          ) : null}
          {!inFlight ? (
            <button
              className="btn btn--sub"
              title="Empties this list so you can send another round. Photographs already uploaded are not affected."
              onClick={() => void q.clear()}
            >
              Clear list
            </button>
          ) : null}
          <button className="btn btn--sub" onClick={q.addFiles}>
            <Icon.Upload />
            Add files
          </button>
        </div>
      </div>

      <div className="queue__bar">
        <i style={{ width: `${pct}%` }} />
      </div>

      {stalled ? (
        <div className="queue__stall">
          <b>Nothing has moved for {stalledFor} seconds.</b> The photographs are still here and
          nothing is lost. Try Pause then Resume; if that does not start it, Retry any failures.
        </div>
      ) : null}

      {s.active.map((i) => (
        <div className="qrow" key={i.id}>
          <span className="qrow__n">{i.name}</span>
          <span className="qrow__s tnum">{formatBytes(i.sourceBytes)}</span>
          {i.status === 'uploading' ? (
            <span className="qrow__p">
              <i style={{ width: `${Math.round(i.progress * 100)}%` }} />
            </span>
          ) : (
            <span className="qrow__lbl">{label(i)}</span>
          )}
        </div>
      ))}

      {s.activeOverflow > 0 ? (
        <div className="qrow qrow__more">and {s.activeOverflow} more waiting</div>
      ) : null}

      {s.failures.length > 0 ? (
        <div className="qfail">
          {s.failures.slice(0, 5).map((i) => (
            <div className="qrow" key={i.id}>
              <span className="qrow__n">{i.name}</span>
              <span className="qrow__err">{i.errorMessage}</span>
            </div>
          ))}
          {s.failures.length > 5 ? (
            <div className="qrow qrow__more">and {s.failures.length - 5} more</div>
          ) : null}
        </div>
      ) : null}

      {showQuality ? <QualityPicker value={quality} onChange={pickQuality} /> : null}
      {qualityBar}
    </div>
  )
}
