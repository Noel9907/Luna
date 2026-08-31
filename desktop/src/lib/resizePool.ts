import type { ResizeRequest, ResizeResponse } from './resize.worker'

// Not a performance limit. It is what stops one bad file freezing the round.
const JOB_TIMEOUT_MS = 60_000

interface Job {
  req: ResizeRequest
  settle: (r: ResizeResponse) => void
  timer: ReturnType<typeof setTimeout> | null
}

interface Slot {
  worker: Worker | null
  job: Job | null
}

/**
 * Decode workers, capped low: each in-flight decode holds a large bitmap, so
 * more workers costs memory rather than buying speed.
 *
 * Every job settles, workers respawn on death, and `destroy` is reversible.
 * All three exist because a pending promise here stops the uploader silently.
 */
export class ResizePool {
  private slots: Slot[]
  private queue: Job[] = []
  private destroyed = false

  constructor(size = Math.min(3, Math.max(1, (navigator.hardwareConcurrency ?? 4) - 1))) {
    this.slots = Array.from({ length: size }, () => ({ worker: null, job: null }))
  }

  /** Failure comes back as `ok: false` rather than a rejection. */
  run(req: ResizeRequest): Promise<ResizeResponse> {
    return new Promise<ResizeResponse>((settle) => {
      if (this.destroyed) {
        settle({ id: req.id, ok: false, code: 'POOL_CLOSED', message: 'Upload was stopped.' })
        return
      }
      this.queue.push({ req, settle, timer: null })
      this.pump()
    })
  }

  private spawn(slot: Slot): Worker | null {
    try {
      const w = new Worker(new URL('./resize.worker.ts', import.meta.url), { type: 'module' })
      w.onmessage = (e: MessageEvent<ResizeResponse>) => this.finish(slot, e.data)
      w.onerror = (e) => {
        console.error('[resizePool] worker error', e.message || e)
        this.recycle(slot, 'WORKER_ERROR', e.message || 'The image decoder crashed.')
      }
      w.onmessageerror = () => {
        console.error('[resizePool] worker sent an unreadable message')
        this.recycle(slot, 'WORKER_ERROR', 'The image decoder sent something unreadable.')
      }
      return w
    } catch (err) {
      console.error('[resizePool] could not start worker', err)
      return null
    }
  }

  private pump(): void {
    if (this.destroyed) return

    for (const slot of this.slots) {
      if (slot.job || !this.queue.length) continue

      if (!slot.worker) {
        slot.worker = this.spawn(slot)
        if (!slot.worker) {
          const job = this.queue.shift()
          job?.settle({
            id: job.req.id,
            ok: false,
            code: 'NO_WORKER',
            message: 'Could not start the image decoder.',
          })
          continue
        }
      }

      const job = this.queue.shift()!
      slot.job = job
      job.timer = setTimeout(() => {
        console.error(`[resizePool] decode timed out: ${job.req.name}`)
        this.recycle(slot, 'DECODE_TIMEOUT', `Timed out reading ${job.req.name}.`)
      }, JOB_TIMEOUT_MS)

      slot.worker.postMessage(job.req)
    }
  }

  private finish(slot: Slot, res: ResizeResponse): void {
    const job = slot.job
    if (!job) return // late reply from a worker already given up on
    if (job.timer) clearTimeout(job.timer)
    slot.job = null
    job.settle(res)
    this.pump()
  }

  /** Throws away a broken worker, fails what it held, carries on. */
  private recycle(slot: Slot, code: string, message: string): void {
    const job = slot.job
    if (job?.timer) clearTimeout(job.timer)
    slot.job = null

    try {
      slot.worker?.terminate()
    } catch {
      // already gone
    }
    slot.worker = null

    job?.settle({ id: job.req.id, ok: false, code, message })
    this.pump()
  }

  destroy(): void {
    this.destroyed = true
    for (const slot of this.slots) {
      const job = slot.job
      if (job?.timer) clearTimeout(job.timer)
      slot.job = null
      try {
        slot.worker?.terminate()
      } catch {
        // already gone
      }
      slot.worker = null
      job?.settle({ id: job.req.id, ok: false, code: 'POOL_CLOSED', message: 'Upload was stopped.' })
    }
    for (const job of this.queue.splice(0)) {
      job.settle({ id: job.req.id, ok: false, code: 'POOL_CLOSED', message: 'Upload was stopped.' })
    }
  }

  /** The owning component can remount without the pool being replaced. */
  reopen(): void {
    this.destroyed = false
    this.pump()
  }
}
