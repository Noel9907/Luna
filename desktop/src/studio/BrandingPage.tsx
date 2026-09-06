import { useEffect, useRef, useState } from 'react'
import { api } from '@shared/lib/api'
import type { Branding } from '@shared/lib/types'

/**
 * The studio's mark, and whether it is burned into photographs.
 *
 * The preview is deliberately a real photograph rather than a swatch. A logo
 * that reads perfectly on white can vanish against a dark hall, and a studio
 * should find that out here rather than from a guest.
 */
export function BrandingPage() {
  const [b, setB] = useState<Branding | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const file = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    api
      .branding()
      .then(setB)
      .catch(() => setError('Could not load your branding settings.'))
  }, [])

  async function patch(input: Parameters<typeof api.updateBranding>[0]) {
    setSaving(true)
    setError(null)
    try {
      setB(await api.updateBranding(input))
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That did not save.')
    } finally {
      setSaving(false)
    }
  }

  async function onPick(f: File | undefined) {
    if (!f) return
    setSaving(true)
    setError(null)
    try {
      setB(await api.uploadLogo(f))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That logo could not be uploaded.')
    } finally {
      setSaving(false)
      if (file.current) file.current.value = ''
    }
  }

  return (
    <>
      <div className="topbar">
        <div className="ttl">Branding</div>
        {saved ? <span className="pill pill--done">Saved</span> : null}
      </div>

      <div className="body">
        {error ? <div className="note note--bad">{error}</div> : null}
        {!b ? (
          <div className="empty">
            <div className="empty__d">Loading</div>
          </div>
        ) : (
          <div className="bstack">
            <section className="panel">
              <h3 className="brand__h">Your logo</h3>
              <p className="brand__p">
                Used on the guest gallery, and as the watermark when that is switched on. A PNG
                with a transparent background works best: anything else paints a solid rectangle
                over the photograph.
              </p>

              <div className="brandrow">
                <div className="logobox" style={{ background: '#1b1b20' }}>
                  {b.logo_url ? (
                    <img src={b.logo_url} alt="Your logo" />
                  ) : (
                    <span className="logobox__none">No logo yet</span>
                  )}
                </div>
                <div className="logobox" style={{ background: '#f2f2f4' }}>
                  {b.logo_url ? (
                    <img src={b.logo_url} alt="Your logo on a light background" />
                  ) : (
                    <span className="logobox__none">No logo yet</span>
                  )}
                </div>
              </div>
              <p className="brand__hint">
                Shown on dark and light so you can see where it disappears.
              </p>

              <input
                ref={file}
                type="file"
                accept="image/png,image/webp"
                style={{ display: 'none' }}
                onChange={(e) => onPick(e.target.files?.[0])}
              />
              <button
                className="btn btn--pri"
                disabled={saving}
                onClick={() => file.current?.click()}
              >
                {b.has_logo ? 'Replace logo' : 'Upload logo'}
              </button>
            </section>

            <section className="panel">
              <h3 className="brand__h">Watermark</h3>
              <p className="brand__p">
                Burns your logo into the bottom centre of every photograph a guest sees or
                downloads. Your original files are never changed.
              </p>

              <label className="bswitch">
                <input
                  type="checkbox"
                  checked={b.watermark_enabled}
                  disabled={saving || !b.has_logo}
                  onChange={(e) => patch({ watermark_enabled: e.target.checked })}
                />
                <span>Watermark photographs</span>
              </label>
              {!b.has_logo ? (
                <p className="brand__hint">Upload a logo first.</p>
              ) : null}

              <div className="brow">
                <label htmlFor="wm-size">Size</label>
                <input
                  id="wm-size"
                  type="range"
                  min={3}
                  max={40}
                  value={Math.round(b.watermark_scale * 100)}
                  disabled={saving || !b.watermark_enabled}
                  onChange={(e) => setB({ ...b, watermark_scale: Number(e.target.value) / 100 })}
                  onMouseUp={() => patch({ watermark_scale: b.watermark_scale })}
                  onTouchEnd={() => patch({ watermark_scale: b.watermark_scale })}
                />
                <span className="tnum">{Math.round(b.watermark_scale * 100)}% of width</span>
              </div>

              <div className="brow">
                <label htmlFor="wm-opacity">Strength</label>
                <input
                  id="wm-opacity"
                  type="range"
                  min={10}
                  max={100}
                  value={Math.round(b.watermark_opacity * 100)}
                  disabled={saving || !b.watermark_enabled}
                  onChange={(e) => setB({ ...b, watermark_opacity: Number(e.target.value) / 100 })}
                  onMouseUp={() => patch({ watermark_opacity: b.watermark_opacity })}
                  onTouchEnd={() => patch({ watermark_opacity: b.watermark_opacity })}
                />
                <span className="tnum">{Math.round(b.watermark_opacity * 100)}%</span>
              </div>

              {/*
                The one thing a studio will get wrong. The mark is applied when a
                photograph is indexed, not when it is viewed, so a change made
                mid-event leaves the earlier rounds unmarked.
              */}
              <div className="note">
                This applies to photographs uploaded from now on. Change it before an event
                starts, not during one.
              </div>
            </section>
          </div>
        )}
      </div>
    </>
  )
}
