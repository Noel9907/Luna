import { app, BrowserWindow, dialog, ipcMain } from 'electron'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import fs from 'node:fs/promises'

const require = createRequire(import.meta.url)
const __dirname = path.dirname(fileURLToPath(import.meta.url))

// chokidar is CommonJS and lives in the app's real dependencies, not the bundle.
const chokidar = require('chokidar') as typeof import('chokidar')

const IMAGE_EXT = new Set(['.jpg', '.jpeg', '.png', '.webp'])

let win: BrowserWindow | null = null
let watcher: import('chokidar').FSWatcher | null = null

function createWindow() {
  win = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 1040,
    minHeight: 640,
    // Chrome-less title bar on macOS, so the app does not look like a web page.
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    backgroundColor: '#ffffff',
    webPreferences: {
      preload: path.join(__dirname, 'preload.mjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  /*
   * Forward the renderer's console to the terminal in development.
   *
   * Without this the renderer is a black box: an upload queue that silently
   * stalls looks identical to one that is working slowly, and the only way to
   * see the error is to open DevTools by hand and catch it live. Worker
   * failures in particular never surface anywhere else.
   */
  if (process.env.VITE_DEV_SERVER_URL) {
    win.webContents.on('console-message', (_e, level, message, line, sourceId) => {
      const tag = ['debug', 'log', 'warn', 'error'][level] ?? 'log'
      const where = sourceId ? ` (${sourceId.split('/').pop()}:${line})` : ''
      console.log(`  [renderer:${tag}]${where} ${message}`)
    })
    win.webContents.on('render-process-gone', (_e, details) => {
      console.log(`  [renderer GONE] ${details.reason} exitCode=${details.exitCode}`)
    })
    win.webContents.on('unresponsive', () => console.log('  [renderer UNRESPONSIVE]'))
  }

  const devUrl = process.env.VITE_DEV_SERVER_URL
  if (devUrl) {
    void win.loadURL(devUrl)
  } else {
    void win.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  win.on('closed', () => {
    win = null
  })
}

/* ── queue persistence ──────────────────────────────────────────────
 * The whole reason this is a desktop app: an interrupted round can resume.
 * A browser cannot do this because it never gets file handles back after a
 * reload. Here the manifest is just paths, so reopening the app picks up
 * exactly where it stopped.
 */
function queuePath(eventId: string) {
  return path.join(app.getPath('userData'), 'queues', `${eventId}.json`)
}

ipcMain.handle('queue:load', async (_e, eventId: string) => {
  try {
    return JSON.parse(await fs.readFile(queuePath(eventId), 'utf8'))
  } catch {
    return []
  }
})

ipcMain.handle('queue:save', async (_e, eventId: string, items: unknown) => {
  const p = queuePath(eventId)
  await fs.mkdir(path.dirname(p), { recursive: true })
  await fs.writeFile(p, JSON.stringify(items), 'utf8')
})

ipcMain.handle('queue:clear', async (_e, eventId: string) => {
  await fs.rm(queuePath(eventId), { force: true })
})

/* ── file access ── */

ipcMain.handle('dialog:pickFiles', async () => {
  const r = await dialog.showOpenDialog(win!, {
    title: 'Choose photographs',
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'Photographs', extensions: ['jpg', 'jpeg', 'png', 'webp'] }],
  })
  return r.canceled ? [] : r.filePaths
})

ipcMain.handle('dialog:pickFolder', async () => {
  const r = await dialog.showOpenDialog(win!, {
    title: 'Choose the folder your camera imports into',
    properties: ['openDirectory'],
  })
  return r.canceled ? null : r.filePaths[0]
})

ipcMain.handle('fs:stat', async (_e, filePath: string) => {
  const s = await fs.stat(filePath)
  return { size: s.size, name: path.basename(filePath), mtimeMs: s.mtimeMs }
})

ipcMain.handle('fs:readFile', async (_e, filePath: string) => {
  const buf = await fs.readFile(filePath)
  // Returned as a Uint8Array; the renderer wraps it in a Blob.
  return new Uint8Array(buf)
})

ipcMain.handle('fs:exists', async (_e, filePath: string) => {
  try {
    await fs.access(filePath)
    return true
  } catch {
    return false
  }
})

ipcMain.handle('fs:listImages', async (_e, folder: string) => {
  const names = await fs.readdir(folder)
  return names
    .filter((n) => IMAGE_EXT.has(path.extname(n).toLowerCase()))
    .map((n) => path.join(folder, n))
})

/* ── folder watching ──────────────────────────────────────────────
 * `awaitWriteFinish` matters more than it looks. A card import writes a 16MB
 * file over several seconds, and without this we would grab a half-written
 * file and upload a truncated photograph.
 */
ipcMain.handle('watch:start', async (_e, folder: string) => {
  await watcher?.close()
  watcher = chokidar.watch(folder, {
    ignoreInitial: true,
    depth: 2,
    awaitWriteFinish: { stabilityThreshold: 1500, pollInterval: 200 },
  })
  watcher.on('add', (p: string) => {
    if (IMAGE_EXT.has(path.extname(p).toLowerCase())) {
      win?.webContents.send('watch:added', p)
    }
  })
})

ipcMain.handle('watch:stop', async () => {
  await watcher?.close()
  watcher = null
})

app.whenReady().then(createWindow)

app.on('window-all-closed', () => {
  void watcher?.close()
  if (process.platform !== 'darwin') app.quit()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow()
})
