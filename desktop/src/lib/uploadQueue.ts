import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@shared/lib/api'
import { ResizePool } from './resizePool'
import { presetFor, type QualityMode } from './settings'
import type { PersistedItem } from './desktop'

const RESIZE_LOOKAHEAD = 12
const RESIZE_CONCURRENCY = 3 // matches the decode pool
const UPLOAD_CONCURRENCY = 5
const PRESIGN_SLICE = 25
const MAX_ATTEMPTS = 5

export type ItemStatus = 'waiting' | 'resizing' | 'ready' | 'uploading' | 'done' | 'failed'

export interface QueueItem {
  id: string
  path: string
  name: string
  status: ItemStatus
  progress: number
  attempts: number
  sourceBytes: number
  uploadBytes?: number
  blob?: Blob
  photoId?: string
  uploadUrl?: string
  errorMessage?: string
}

export interface QueueSnapshot {
  total: number
  done: number
  failed: number
  active: QueueItem[]
  activeOverflow: number
  counts: Record<ItemStatus, number>
  failures: QueueItem[]
  running: boolean
  paused: boolean
  sourceBytes: number
  uploadBytes: number
  watching: string | null
}

const ACTIVE_ROWS = 6


const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

const MIME: Record<string, string> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
}

function mimeFor(name: string) {
  return MIME[name.split('.').pop()?.toLowerCase() ?? ''] ?? 'image/jpeg'
}

/** XHR rather than fetch, because fetch still has no upload progress event. */
function putWithProgress(
  url: string,
  blob: Blob,
  type: string,
  onProgress: (frac: number) => void,
) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    xhr.setRequestHeader('Content-Type', type)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    }
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`Upload failed with ${xhr.status}`))
    xhr.onerror = () => reject(new Error('Network error'))
    xhr.send(blob)
  })
}

class QueueController {
  items: QueueItem[] = []
  paused = false
  running = false
  watching: string | null = null
  quality: QualityMode = 'standard'

  private pool = new ResizePool()
  private listeners = new Set<() => void>()
  private completed: string[] = []
  private seenPaths = new Set<string>()

  constructor(private eventId: string) {}

  subscribe(fn: () => void) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }
  private notify() {
    this.listeners.forEach((f) => f())
  }

  /**
   * Restores an interrupted round.
   *
   * This is the reason the studio app is not a web page. Paths are re-verified
   * because the card may have been ejected, and anything already uploaded is
   * skipped rather than sent twice.
   */
  private restored = false

  async restore() {
    // Once per controller: StrictMode mounts the effect twice.
    if (this.restored) return
    this.restored = true

    const saved = await window.desktop.loadQueue(this.eventId)
    if (!saved.length) return
    for (const s of saved) {
      this.seenPaths.add(s.path)
      if (s.status === 'done') {
        this.items.push({ ...s, status: 'done', progress: 1, attempts: 0 })
        continue
      }
      if (!(await window.desktop.exists(s.path))) {
        this.items.push({
          ...s,
          status: 'failed',
          progress: 0,
          attempts: 0,
          errorMessage: 'File is no longer on this machine',
        })
        continue
      }
      this.items.push({ ...s, status: 'waiting', progress: 0, attempts: 0 })
    }
    this.notify()
    void this.drive()
  }

  private persist() {
    const manifest: PersistedItem[] = this.items.map((i) => ({
      id: i.id,
      path: i.path,
      name: i.name,
      sourceBytes: i.sourceBytes,
      status: i.status === 'done' ? 'done' : i.status === 'failed' ? 'failed' : 'pending',
      photoId: i.photoId,
      errorMessage: i.errorMessage,
    }))
    void window.desktop.saveQueue(this.eventId, manifest)
  }

  async addPaths(paths: string[]) {
    let added = 0
    for (const p of paths) {
      if (this.seenPaths.has(p)) continue // folder watch re-reports; never upload twice
      this.seenPaths.add(p)
      try {
        const st = await window.desktop.stat(p)
        this.items.push({
          id: crypto.randomUUID(),
          path: p,
          name: st.name,
          status: 'waiting',
          progress: 0,
          attempts: 0,
          sourceBytes: st.size,
        })
        added++
      } catch {
        // unreadable file, skip silently
      }
    }
    if (added) {
      this.persist()
      this.notify()
      void this.drive()
    }
  }

  async startWatching(folder: string) {
    this.watching = folder
    await window.desktop.watchStart(folder)
    // Pick up anything already sitting in the folder when watching begins.
    await this.addPaths(await window.desktop.listImages(folder))
    this.notify()
  }

  async stopWatching() {
    this.watching = null
    await window.desktop.watchStop()
    this.notify()
  }

  pause() {
    this.paused = true
    this.notify()
  }
  resume() {
    this.paused = false
    this.notify()
    void this.drive()
  }
  retryFailed() {
    for (const i of this.items) {
      if (i.status === 'failed' && i.errorMessage !== 'File is no longer on this machine') {
        i.status = 'waiting'
        i.attempts = 0
        i.errorMessage = undefined
      }
    }
    this.notify()
    void this.drive()
  }

  /**
   * Empties the finished round so the next one starts clean.
   *
   * Clears `seenPaths` too, which is what allows the same folder to be sent
   * again. Photographs already on the server are untouched.
   */
  async clear() {
    this.items = []
    this.seenPaths.clear()
    this.completed = []
    await window.desktop.clearQueue(this.eventId)
    this.notify()
  }

  /**
   * Paired with `reattach`, not a teardown. The controller outlives the
   * effect that owns it, so detaching must stay recoverable.
   */
  detach() {
    this.pool.destroy()
    void window.desktop.watchStop()
  }

  reattach() {
    this.pool.reopen()
    if (this.items.some((i) => i.status !== 'done' && i.status !== 'failed')) {
      void this.drive()
    }
  }

  private preparing = false

  /**
   * One copy of this loop, several decodes inside it. `drive` calls it every
   * 150ms, so it must not stack; awaiting one file at a time would idle the
   * rest of the pool.
   */
  private async prepareLoop() {
    if (this.preparing) return
    this.preparing = true

    const preset = presetFor(this.quality)
    const inflight = new Set<Promise<void>>()

    try {
      while (!this.paused) {
        const ready = this.items.filter((i) => i.status === 'ready').length
        if (ready + inflight.size >= RESIZE_LOOKAHEAD) break

        const next = this.items.find((i) => i.status === 'waiting')
        if (!next) break

        let task: Promise<void>
        task = this.prepareOne(next, preset).finally(() => inflight.delete(task))
        inflight.add(task)

        if (inflight.size >= RESIZE_CONCURRENCY) await Promise.race(inflight)
      }
    } finally {
      await Promise.allSettled(inflight)
      this.preparing = false
    }
  }

  private async prepareOne(item: QueueItem, preset: ReturnType<typeof presetFor>) {
    item.status = 'resizing'
    this.notify()

    try {
      const bytes = await window.desktop.readFile(item.path)
      // Cast is safe and avoids copying 16MB: TypeScript 5.7 narrowed BlobPart
      // to ArrayBuffer-backed views, but IPC always hands back a plain one.
      const source = new Blob([bytes as unknown as BlobPart], { type: mimeFor(item.name) })

      if (preset.maxEdge === null) {
        // Original: no decode at all, the file goes up exactly as it is.
        item.blob = source
        item.uploadBytes = source.size
        item.status = 'ready'
      } else {
        const res = await this.pool.run({
          id: item.id,
          blob: source,
          name: item.name,
          maxEdge: preset.maxEdge,
          quality: preset.quality,
        })
        if (res.ok) {
          item.blob = res.blob
          item.uploadBytes = res.blob.size
          item.status = 'ready'
        } else {
          item.status = 'failed'
          item.errorMessage = res.message
        }
      }
    } catch (err) {
      item.status = 'failed'
      item.errorMessage = err instanceof Error ? err.message : 'Could not read file'
    }
    this.notify()
  }

  /** Small repeated slices: URLs expire in ten minutes, rounds take longer. */
  private async presignSlice() {
    const need = this.items
      .filter((i) => i.status === 'ready' && !i.uploadUrl)
      .slice(0, PRESIGN_SLICE)
    if (!need.length) return

    const { uploads } = await api.requestUploadSlots(
      this.eventId,
      need.map((i) => ({
        client_ref: i.id,
        content_type: presetFor(this.quality).maxEdge === null ? mimeFor(i.name) : 'image/jpeg',
        size_bytes: i.uploadBytes ?? 0,
      })),
    )
    for (const slot of uploads) {
      const it = this.items.find((i) => i.id === slot.client_ref)
      if (it) {
        it.uploadUrl = slot.upload_url
        it.photoId = slot.photo_id
      }
    }
    this.notify()
  }

  private async uploadOne(item: QueueItem) {
    item.status = 'uploading'
    item.progress = 0
    this.notify()

    const type = presetFor(this.quality).maxEdge === null ? mimeFor(item.name) : 'image/jpeg'
    try {
      await putWithProgress(item.uploadUrl!, item.blob!, type, (f) => {
        item.progress = f
        this.notify()
      })
      item.status = 'done'
      item.progress = 1
      item.blob = undefined // release memory the moment the bytes are gone
      this.completed.push(item.photoId!)
      this.persist()
      this.notify()
    } catch (err) {
      item.attempts += 1
      if (item.attempts >= MAX_ATTEMPTS) {
        item.status = 'failed'
        item.errorMessage = err instanceof Error ? err.message : 'Upload failed'
        this.persist()
      } else {
        // The URL may also have expired. Drop it so the next slice mints a fresh one.
        item.uploadUrl = undefined
        item.status = 'ready'
        await sleep(Math.min(8000, 500 * 2 ** item.attempts))
      }
      this.notify()
    }
  }

  private async flush(force = false) {
    if (!this.completed.length) return
    if (!force && this.completed.length < 20) return
    const batch = this.completed.splice(0, 250)
    try {
      await api.completeUploads(this.eventId, batch)
    } catch {
      this.completed.unshift(...batch) // endpoint is idempotent, safe to resend
    }
  }

  private async drive() {
    if (this.running) return
    this.running = true
    this.notify()
    try {
      while (!this.paused) {
        void this.prepareLoop()

        if (this.items.filter((i) => i.status === 'ready' && i.uploadUrl).length < UPLOAD_CONCURRENCY) {
          await this.presignSlice()
        }

        const batch = this.items
          .filter((i) => i.status === 'ready' && i.uploadUrl)
          .slice(0, UPLOAD_CONCURRENCY)

        if (!batch.length) {
          const pending = this.items.some(
            (i) => i.status === 'waiting' || i.status === 'resizing' || i.status === 'ready',
          )
          // Keep the loop alive while watching, so new files start immediately.
          if (!pending && !this.watching) break
          await sleep(pending ? 150 : 800)
          continue
        }

        await Promise.all(batch.map((i) => this.uploadOne(i)))
        await this.flush()
      }
      await this.flush(true)
    } finally {
      this.running = false
      this.notify()
    }
  }

  snapshot(): QueueSnapshot {
    const counts: Record<ItemStatus, number> = {
      waiting: 0,
      resizing: 0,
      ready: 0,
      uploading: 0,
      done: 0,
      failed: 0,
    }
    for (const i of this.items) counts[i.status] += 1

    const failures = this.items.filter((i) => i.status === 'failed')
    const active = this.items.filter(
      (i) => i.status === 'uploading' || i.status === 'resizing' || i.status === 'ready',
    )

    return {
      total: this.items.length,
      done: counts.done,
      failed: counts.failed,
      // Queue order, never sorted by stage: re-sorting makes rows jump.
      active: active.slice(0, ACTIVE_ROWS),
      activeOverflow: Math.max(0, active.length - ACTIVE_ROWS),
      counts,
      failures,
      running: this.running,
      paused: this.paused,
      sourceBytes: this.items.reduce((a, i) => a + i.sourceBytes, 0),
      // Only bytes actually sent, not merely resized.
      uploadBytes: this.items.reduce(
        (a, i) => a + (i.status === 'done' ? (i.uploadBytes ?? 0) : 0),
        0,
      ),
      watching: this.watching,
    }
  }
}

export function useUploadQueue(eventId: string, quality: QualityMode) {
  const ref = useRef<QueueController>()
  if (!ref.current) ref.current = new QueueController(eventId)
  const ctl = ref.current
  ctl.quality = quality

  const [, bump] = useState(0)

  useEffect(() => {
    // Progress fires many times a second across five uploads. Repaint at 10Hz.
    let dirty = false
    const unsub = ctl.subscribe(() => {
      dirty = true
    })
    const t = setInterval(() => {
      if (dirty) {
        dirty = false
        bump((n) => n + 1)
      }
    }, 100)
    return () => {
      unsub()
      clearInterval(t)
    }
  }, [ctl])

  useEffect(() => {
    ctl.reattach()
    void ctl.restore()
    const off = window.desktop.onFileAdded((p) => void ctl.addPaths([p]))
    return () => {
      off()
      ctl.detach()
    }
  }, [ctl])

  return {
    snapshot: ctl.snapshot(),
    addFiles: useCallback(() => window.desktop.pickFiles().then((p) => ctl.addPaths(p)), [ctl]),
    chooseWatchFolder: useCallback(
      () => window.desktop.pickFolder().then((f) => (f ? ctl.startWatching(f) : undefined)),
      [ctl],
    ),
    stopWatching: useCallback(() => ctl.stopWatching(), [ctl]),
    pause: useCallback(() => ctl.pause(), [ctl]),
    resume: useCallback(() => ctl.resume(), [ctl]),
    retryFailed: useCallback(() => ctl.retryFailed(), [ctl]),
    clear: useCallback(() => ctl.clear(), [ctl]),
  }
}
