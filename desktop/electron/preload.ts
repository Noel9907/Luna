import { contextBridge, ipcRenderer } from 'electron'

/**
 * The only surface the renderer gets. Node stays out of the UI entirely, so a
 * bug in React can never touch the filesystem in a way this list does not allow.
 */
contextBridge.exposeInMainWorld('desktop', {
  pickFiles: (): Promise<string[]> => ipcRenderer.invoke('dialog:pickFiles'),
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('dialog:pickFolder'),

  stat: (p: string): Promise<{ size: number; name: string; mtimeMs: number }> =>
    ipcRenderer.invoke('fs:stat', p),
  readFile: (p: string): Promise<Uint8Array> => ipcRenderer.invoke('fs:readFile', p),
  exists: (p: string): Promise<boolean> => ipcRenderer.invoke('fs:exists', p),
  listImages: (folder: string): Promise<string[]> => ipcRenderer.invoke('fs:listImages', folder),

  watchStart: (folder: string): Promise<void> => ipcRenderer.invoke('watch:start', folder),
  watchStop: (): Promise<void> => ipcRenderer.invoke('watch:stop'),
  onFileAdded: (cb: (path: string) => void) => {
    const handler = (_e: unknown, p: string) => cb(p)
    ipcRenderer.on('watch:added', handler)
    return () => ipcRenderer.off('watch:added', handler)
  },

  loadQueue: (eventId: string): Promise<unknown[]> => ipcRenderer.invoke('queue:load', eventId),
  saveQueue: (eventId: string, items: unknown): Promise<void> =>
    ipcRenderer.invoke('queue:save', eventId, items),
  clearQueue: (eventId: string): Promise<void> => ipcRenderer.invoke('queue:clear', eventId),

  platform: process.platform,
})
