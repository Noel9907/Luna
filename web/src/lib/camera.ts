/**
 * Selfie capture.
 *
 * A live preview beats the native camera app here: a guest can see whether
 * their face is lit and centred before shooting, which is most of the
 * difference between a selfie that matches and one that gets rejected.
 * Falls back to the OS camera where getUserMedia is unavailable or refused.
 */

// 1280, not 900. A face has to clear a pixel floor to embed reliably, and on a
// laptop webcam at arm's length an aggressive downscale pushes it under.
export const SELFIE_MAX_EDGE = 1280
export const SELFIE_QUALITY = 0.85

export async function openCamera(): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: 'user',
      width: { ideal: 1920 },
      height: { ideal: 1920 },
    },
    audio: false,
  })
}

export function closeCamera(stream: MediaStream | null) {
  stream?.getTracks().forEach((t) => t.stop())
}

export function cameraSupported() {
  return Boolean(navigator.mediaDevices?.getUserMedia)
}

/**
 * Grabs a frame and compresses it hard.
 *
 * ~900px is far more than face matching needs, and the guest is on the worst
 * network in the building. Roughly 150KB instead of 3MB is the difference
 * between a search that feels instant and one that feels broken.
 */
export function captureFrame(video: HTMLVideoElement): Promise<Blob> {
  const vw = video.videoWidth
  const vh = video.videoHeight
  const scale = Math.min(1, SELFIE_MAX_EDGE / Math.max(vw, vh))
  const w = Math.round(vw * scale)
  const h = Math.round(vh * scale)

  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')!

  // Un-mirror. The preview is flipped so it feels like a mirror, but the
  // stored image must be the real orientation.
  ctx.translate(w, 0)
  ctx.scale(-1, 1)
  ctx.drawImage(video, 0, 0, w, h)

  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error('Could not capture'))),
      'image/jpeg',
      SELFIE_QUALITY,
    )
  })
}

/** Same compression for a photo picked through the OS camera fallback. */
export async function compressFile(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file, {
    resizeWidth: SELFIE_MAX_EDGE,
    resizeQuality: 'high',
    imageOrientation: 'from-image',
  })
  const canvas = document.createElement('canvas')
  canvas.width = bitmap.width
  canvas.height = bitmap.height
  canvas.getContext('2d')!.drawImage(bitmap, 0, 0)
  bitmap.close()
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error('Could not read that photo'))),
      'image/jpeg',
      SELFIE_QUALITY,
    )
  })
}
