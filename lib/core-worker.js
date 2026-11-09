/** task-223: one persistent python core per DSH plugin lifetime.
 *
 * start(dataDir) spawns python/biocore_runtime/service.py; call(action, payload)
 * correlates requests by id; a crash restarts at most 3 times (0.5s/1s/2s) and
 * every failure is logged as '__bioWorker ...'. stop() sends SIGTERM and
 * force-kills after 5s. When the worker is down, callers fall back to the old
 * spawn-per-call bridge (handled in py-bridge.js).
 */
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { embeddingEnv } from './modules/python-env.js'

export const PACKAGE_ROOT = fileURLToPath(new URL('..', import.meta.url))
export const PYTHON_DIR = join(PACKAGE_ROOT, 'python')
export const SERVICE_SCRIPT = join(PYTHON_DIR, 'biocore_runtime', 'service.py')
export const ADAPTER_DIR = join(PYTHON_DIR, 'dsh_adapter')
export const WORKER_LOG_PREFIX = '__bioWorker'
export const DEFAULT_TIMEOUT_MS = 10000
export const MAX_RESTARTS = 3
export const BACKOFF_MS = Object.freeze([500, 1000, 2000])
export const STOP_GRACE_MS = 5000

let worker = null
let exitHookInstalled = false

function log(message) {
  try { console.error(WORKER_LOG_PREFIX + ' ' + message) } catch { /* ignore */ }
}

export class WorkerUnavailable extends Error {
  constructor(reason, detail) {
    super(String(reason || 'worker_unavailable'))
    this.name = 'WorkerUnavailable'
    this.reason = String(reason || 'worker_unavailable')
    this.detail = detail ? String(detail) : ''
  }
}

function pythonCommand() {
  return process.env.BIOCORE_PYTHON || 'python'
}

function workerEnv(dataDir) {
  let embedding = {}
  try { embedding = dataDir ? embeddingEnv(dataDir) : {} } catch { embedding = {} }
  return Object.assign({}, process.env, embedding, {
    BIOCORE_PLUGIN_DIR: PYTHON_DIR,
    BIOCORE_ADAPTER_DIR: ADAPTER_DIR,
    PYTHONIOENCODING: 'utf-8',
  })
}

function isAlive(handle) {
  return Boolean(handle && handle.child && handle.child.exitCode === null
    && handle.child.signalCode === null && !handle.stopping)
}

function rejectPending(handle, reason, detail) {
  if (!handle || !handle.pending) return
  for (const [id, entry] of handle.pending) {
    clearTimeout(entry.timer)
    entry.reject(new WorkerUnavailable(reason, detail))
    handle.pending.delete(id)
  }
}

function handleLine(handle, line) {
  const text = String(line || '').trim()
  if (!text) return
  let message = null
  try { message = JSON.parse(text) } catch {
    log('bad-json ' + text.slice(0, 120))
    return
  }
  if (!message || typeof message !== 'object') return
  const id = message.id == null ? '' : String(message.id)
  const entry = handle.pending.get(id)
  if (!entry) return
  handle.pending.delete(id)
  clearTimeout(entry.timer)
  if (message.ok === true) entry.resolve(message.result === undefined ? {} : message.result)
  else entry.reject(new WorkerUnavailable(message.reason || 'service_error',
    message.detail || ''))
}

function attach(handle) {
  const child = handle.child
  let buffer = ''
  child.stdout.on('data', (chunk) => {
    buffer += String(chunk)
    let index = buffer.indexOf('\n')
    while (index >= 0) {
      const line = buffer.slice(0, index)
      buffer = buffer.slice(index + 1)
      handleLine(handle, line)
      index = buffer.indexOf('\n')
    }
    if (buffer.length > 1024 * 1024) buffer = ''
  })
  child.stderr.on('data', (chunk) => {
    const text = String(chunk).trim()
    if (text) log('stderr ' + text.split('\n').slice(-3).join(' | ').slice(0, 500))
  })
  child.on('error', (error) => {
    log('error ' + String(error && error.message || error).slice(0, 200))
    onGone(handle, 'spawn_error', String(error && error.message || error))
  })
  // A sandbox may refuse piped stdio (EPIPE on write). Swallow the stream error
  // so the host process survives; callers fall back to spawn-per-call.
  try { child.stdin.on('error', (error) => onGone(handle, 'stdin_error', String(error && error.message || error))) } catch { /* noop */ }
  try { child.stdout.on('error', (error) => onGone(handle, 'stdout_error', String(error && error.message || error))) } catch { /* noop */ }
  child.on('exit', (code, signal) => {
    onGone(handle, 'exit', 'code=' + code + ' signal=' + signal)
  })
}

function onGone(handle, reason, detail) {
  if (handle.gone) return
  handle.gone = true
  if (handle.stopping) {
    rejectPending(handle, 'worker_stopped', '')
    return
  }
  log('down reason=' + reason + ' ' + String(detail || '').slice(0, 160))
  rejectPending(handle, 'worker_crashed', reason + ' ' + String(detail || ''))
  if (handle.restarts >= MAX_RESTARTS) {
    log('giving_up restarts=' + handle.restarts)
    return
  }
  const delay = BACKOFF_MS[Math.min(handle.restarts, BACKOFF_MS.length - 1)]
  handle.restarts += 1
  log('restart=' + handle.restarts + ' delay_ms=' + delay)
  handle.restartTimer = setTimeout(() => {
    handle.restartTimer = null
    if (handle.stopping || worker !== handle) return
    spawnChild(handle)
  }, delay)
  if (typeof handle.restartTimer.unref === 'function') handle.restartTimer.unref()
}

function spawnChild(handle) {
  handle.gone = false
  let child = null
  try {
    child = spawn(pythonCommand(), [SERVICE_SCRIPT], {
      cwd: ADAPTER_DIR,
      env: workerEnv(handle.dataDir),
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })
  } catch (error) {
    log('spawn_failed ' + String(error && error.message || error))
    handle.child = null
    onGone(handle, 'spawn_failed', String(error && error.message || error))
    return
  }
  handle.child = child
  attach(handle)
  log('started pid=' + (child.pid || '?') + ' dataDir=' + String(handle.dataDir || ''))
}

/** Start (or reuse) the singleton worker. Idempotent while it is alive. */
export function startCoreWorker(dataDir) {
  const dir = String(dataDir || '')
  if (worker && isAlive(worker) && worker.dataDir === dir) return workerApi(worker)
  if (worker) stopCoreWorker()
  worker = {
    dataDir: dir,
    child: null,
    pending: new Map(),
    seq: 0,
    restarts: 0,
    stopping: false,
    gone: false,
    restartTimer: null,
    startedAt: Date.now(),
  }
  spawnChild(worker)
  if (!exitHookInstalled) {
    exitHookInstalled = true
    try {
      process.once('exit', () => {
        try { if (worker && worker.child) worker.child.kill() } catch { /* ignore */ }
      })
    } catch { /* ignore */ }
  }
  return workerApi(worker)
}

export function isWorkerRunning() {
  return isAlive(worker)
}

export function workerPid() {
  return worker && worker.child ? worker.child.pid : null
}

export function callCoreWorker(action, payload, options) {
  const opts = options || {}
  const timeoutMs = Number.isFinite(Number(opts.timeoutMs))
    ? Math.max(1, Number(opts.timeoutMs)) : DEFAULT_TIMEOUT_MS
  return new Promise((resolve, reject) => {
    if (!isAlive(worker)) {
      reject(new WorkerUnavailable('worker_down', ''))
      return
    }
    const id = 'w' + (++worker.seq)
    const entry = { resolve, reject, timer: null }
    entry.timer = setTimeout(() => {
      worker.pending.delete(id)
      reject(new WorkerUnavailable('timeout', 'action=' + action))
    }, timeoutMs)
    if (typeof entry.timer.unref === 'function') entry.timer.unref()
    worker.pending.set(id, entry)
    const line = JSON.stringify({
      id,
      action: String(action || ''),
      payload: payload && typeof payload === 'object' ? payload : {},
    }) + '\n'
    try {
      worker.child.stdin.write(line)
    } catch (error) {
      worker.pending.delete(id)
      clearTimeout(entry.timer)
      reject(new WorkerUnavailable('write_failed', String(error && error.message || error)))
    }
  })
}

/** Stop the worker: SIGTERM, then force-kill after 5s. Never throws. */
export function stopCoreWorker() {
  const handle = worker
  if (!handle) return
  handle.stopping = true
  if (handle.restartTimer) {
    clearTimeout(handle.restartTimer)
    handle.restartTimer = null
  }
  rejectPending(handle, 'worker_stopped', '')
  const child = handle.child
  worker = null
  if (!child) return
  try { child.kill('SIGTERM') } catch { /* ignore */ }
  const timer = setTimeout(() => {
    try { if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL') } catch { /* ignore */ }
  }, STOP_GRACE_MS)
  if (typeof timer.unref === 'function') timer.unref()
  child.once('exit', () => clearTimeout(timer))
}

/** Test seam: kill the current process without marking it stopped (crash). */
export function killWorkerForTest(signal) {
  if (!isAlive(worker)) return false
  try { worker.child.kill(signal || 'SIGKILL') } catch { return false }
  return true
}

function workerApi(handle) {
  return {
    start: () => startCoreWorker(handle.dataDir),
    call: (action, payload, options) => callCoreWorker(action, payload, options),
    stop: () => stopCoreWorker(),
    get pid() { return handle.child ? handle.child.pid : null },
    get running() { return isAlive(handle) },
    get restarts() { return handle.restarts },
  }
}
