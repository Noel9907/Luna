export type QualityMode = 'standard' | 'high' | 'original'

export interface QualityPreset {
  id: QualityMode
  label: string
  detail: string
  maxEdge: number | null
  quality: number
  approxPerPhoto: string
}

/**
 * Guests view on phones, and face matching needs faces above roughly 100px.
 * Neither improves past 2400px, which is why Standard is the default and why
 * Original is described honestly rather than sold as "best".
 */
export const QUALITY_PRESETS: QualityPreset[] = [
  {
    id: 'standard',
    label: 'Standard',
    detail: '2400px. Best balance. Guests see no difference on a phone.',
    maxEdge: 2400,
    quality: 0.82,
    approxPerPhoto: '~400 KB',
  },
  {
    id: 'high',
    label: 'High',
    detail: '4000px. Use when the gallery may be viewed on a desktop screen.',
    maxEdge: 4000,
    quality: 0.9,
    approxPerPhoto: '~1.2 MB',
  },
  {
    id: 'original',
    label: 'Original',
    detail: 'Untouched files. 40x the storage, and no benefit to guests or matching.',
    maxEdge: null,
    quality: 1,
    approxPerPhoto: '~16 MB',
  },
]

const KEY = 'frame.quality'

export function loadQuality(): QualityMode {
  const v = localStorage.getItem(KEY)
  return v === 'high' || v === 'original' ? v : 'standard'
}

export function saveQuality(mode: QualityMode) {
  localStorage.setItem(KEY, mode)
}

export function presetFor(mode: QualityMode): QualityPreset {
  return QUALITY_PRESETS.find((p) => p.id === mode) ?? QUALITY_PRESETS[0]
}
