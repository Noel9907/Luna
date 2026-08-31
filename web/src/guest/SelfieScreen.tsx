import { useCallback, useEffect, useRef, useState } from 'react'
import { cameraSupported, captureFrame, closeCamera, compressFile, openCamera } from '../lib/camera'

export function SelfieScreen({
  busy,
  error,
  onSubmit,
  onBack,
}: {
  busy: boolean
  error: string | null
  onSubmit: (blob: Blob) => void
  onBack: () => void
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const streamRef = useRef<MediaStream | null>(null)

  const [ready, setReady] = useState(false)
  const [denied, setDenied] = useState(false)
  const [preview, setPreview] = useState<{ blob: Blob; url: string } | null>(null)

  /**
   * Reattaches the live stream whenever a video element mounts.
   *
   * Taking a shot swaps the camera view for the preview, which unmounts the
   * video. Retake mounts a fresh one, and without this it stays black: the
   * stream is still running, it just is not connected to anything.
   */
  const attachVideo = useCallback((node: HTMLVideoElement | null) => {
    videoRef.current = node
    if (node && streamRef.current) {
      node.srcObject = streamRef.current
      void node.play()
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    if (!cameraSupported()) {
      setDenied(true)
      return
    }
    openCamera()
      .then((stream) => {
        if (cancelled) return closeCamera(stream)
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          void videoRef.current.play()
        }
        setReady(true)
      })
      .catch(() => setDenied(true))

    return () => {
      cancelled = true
      closeCamera(streamRef.current)
      streamRef.current = null
    }
  }, [])

  // Revoke the object URL when the preview changes or unmounts.
  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview.url)
    }
  }, [preview])

  async function shoot() {
    if (!videoRef.current) return
    const blob = await captureFrame(videoRef.current)
    setPreview({ blob, url: URL.createObjectURL(blob) })
  }

  async function pickFile(file: File | undefined) {
    if (!file) return
    const blob = await compressFile(file)
    setPreview({ blob, url: URL.createObjectURL(blob) })
  }

  if (preview) {
    return (
      <div className="g-screen">
        <div className="g-shot">
          <img src={preview.url} alt="Your selfie" />
        </div>

        {error ? <div className="g-err">{error}</div> : null}

        <div className="g-actions">
          <button className="g-btn g-btn--pri" disabled={busy} onClick={() => onSubmit(preview.blob)}>
            {busy ? 'Looking for you' : 'Find my photographs'}
          </button>
          <button
            className="g-btn"
            disabled={busy}
            onClick={() => {
              URL.revokeObjectURL(preview.url)
              setPreview(null)
            }}
          >
            Retake
          </button>
        </div>
      </div>
    )
  }

  if (denied) {
    return (
      <div className="g-screen">
        <div className="g-pad">
          <h2 className="g-h2">Use your camera app</h2>
          <p className="g-p">
            We could not open the camera here. Take a selfie with your normal camera instead and
            choose it below.
          </p>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          capture="user"
          hidden
          onChange={(e) => void pickFile(e.target.files?.[0])}
        />
        {error ? <div className="g-err">{error}</div> : null}
        <div className="g-actions">
          <button className="g-btn g-btn--pri" onClick={() => fileRef.current?.click()}>
            Take a selfie
          </button>
          <button className="g-btn" onClick={onBack}>
            Back
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="g-screen">
      <div className="g-cam">
        <video ref={attachVideo} playsInline muted />
        <div className="g-guide" aria-hidden="true" />
        {!ready ? <div className="g-camwait">Opening camera</div> : null}
      </div>

      <p className="g-hint">Face a light, and make sure you are the only person in frame.</p>
      {error ? <div className="g-err">{error}</div> : null}

      <div className="g-actions">
        <button className="g-shutter" onClick={shoot} disabled={!ready} aria-label="Take selfie">
          <span />
        </button>
      </div>
    </div>
  )
}
