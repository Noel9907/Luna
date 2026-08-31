/**
 * Decode + downscale worker.
 *
 * These are pro camera files. A 16MB JPEG is roughly 24 megapixels, and a full
 * decode materialises ~96MB of RGBA. Doing that on the main thread freezes the
 * window; doing several at once kills the renderer.
 *
 * The trick is `createImageBitmap(blob, { resizeWidth })`. The browser engine
 * decodes straight to the target size, so the full-size bitmap never exists.
 * For JPEG it can use scaled DCT decoding, which is far cheaper than
 * decode-then-shrink.
 */

export interface ResizeRequest {
  id: string
  blob: Blob
  name: string
  maxEdge: number
  quality: number
}

export type ResizeResponse =
  | { id: string; ok: true; blob: Blob; width: number; height: number }
  | { id: string; ok: false; code: string; message: string }

async function resize(req: ResizeRequest): Promise<ResizeResponse> {
  const { id, blob, name, maxEdge, quality } = req

  try {
    /*
     * Probe pass. We need the aspect ratio to know which axis is the long edge,
     * and cannot know it without decoding something. A 32px decode is cheap.
     * `from-image` applies EXIF rotation, so portrait shots report portrait
     * dimensions rather than the sensor's landscape ones. Skipping this would
     * upload sideways photographs, and face detection fails on those.
     */
    const probe = await createImageBitmap(blob, {
      resizeWidth: 32,
      resizeQuality: 'pixelated',
      imageOrientation: 'from-image',
    })
    const aspect = probe.width / probe.height
    probe.close()

    const opts: ImageBitmapOptions = {
      resizeQuality: 'high',
      imageOrientation: 'from-image',
    }
    if (aspect >= 1) opts.resizeWidth = maxEdge
    else opts.resizeHeight = maxEdge

    const bitmap = await createImageBitmap(blob, opts)

    // Never upscale. Already-small exports pass through untouched.
    const w = Math.min(bitmap.width, aspect >= 1 ? maxEdge : Math.round(maxEdge * aspect))
    const h = Math.min(bitmap.height, aspect >= 1 ? Math.round(maxEdge / aspect) : maxEdge)

    const canvas = new OffscreenCanvas(w, h)
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('no 2d context')
    ctx.drawImage(bitmap, 0, 0, w, h)
    bitmap.close()

    const out = await canvas.convertToBlob({ type: 'image/jpeg', quality })
    return { id, ok: true, blob: out, width: w, height: h }
  } catch (err) {
    return {
      id,
      ok: false,
      code: 'DECODE_FAILED',
      message: `Could not read ${name}. ${err instanceof Error ? err.message : ''}`.trim(),
    }
  }
}

self.onmessage = async (e: MessageEvent<ResizeRequest>) => {
  self.postMessage(await resize(e.data))
}
