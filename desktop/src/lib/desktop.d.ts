export interface DesktopApi {
  pickFiles(): Promise<string[]>
  pickFolder(): Promise<string | null>
  stat(path: string): Promise<{ size: number; name: string; mtimeMs: number }>
  readFile(path: string): Promise<Uint8Array>
  exists(path: string): Promise<boolean>
  listImages(folder: string): Promise<string[]>
  watchStart(folder: string): Promise<void>
  watchStop(): Promise<void>
  onFileAdded(cb: (path: string) => void): () => void
  loadQueue(eventId: string): Promise<PersistedItem[]>
  saveQueue(eventId: string, items: PersistedItem[]): Promise<void>
  clearQueue(eventId: string): Promise<void>
  platform: string
}

/** What survives a restart. Paths only, so the manifest stays tiny. */
export interface PersistedItem {
  id: string
  path: string
  name: string
  sourceBytes: number
  status: 'pending' | 'done' | 'failed'
  photoId?: string
  errorMessage?: string
}

declare global {
  interface Window {
    desktop: DesktopApi
  }
}
