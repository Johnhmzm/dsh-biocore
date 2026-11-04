/** Node wrapper around lib/py/bridge.py (python subprocess, never in-process). */
import { execFile, spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { embeddingEnv } from './modules/python-env.js'
import { WorkerUnavailable, callCoreWorker, isWorkerRunning } from './core-worker.js'

export const BRIDGE_PATH = fileURLToPath(new URL('./py/bridge.py', import.meta.url))
export const PACKAGE_ROOT = fileURLToPath(new URL('..', import.meta.url))
export const BUNDLED_PYTHON_DIR = join(PACKAGE_ROOT, 'python')
export const DEFAULT_ADAPTER_DIR = join(BUNDLED_PYTHON_DIR, 'dsh_adapter')
export const BRIDGE_TIMEOUT_MS = 20000

let bridgeDataDir = ''
/** Configure embedding env for all later python subprocesses. */
export function configureBridge(dataDir) {
  bridgeDataDir = String(dataDir || '').trim()
}

/** task-223: worker first, spawn-per-call only as a fallback. */
async function workerOrFallback(action, payload) {
  if (isWorkerRunning()) {
    try {
      return await callCoreWorker(action, payload || {})
    } catch (error) {
      if (!(error instanceof WorkerUnavailable)) throw error
      try {
        console.error('__bioWorker fallback action=' + String(action)
          + ' reason=' + String(error.reason || error.message))
      } catch { /* ignore */ }
    }
  }
  return runBridge(['runtime_action', String(action || ''),
    JSON.stringify(payload && typeof payload === 'object' ? payload : {})])
}

function bridgeEnv(base) {
  let embedding = {}
  try { embedding = bridgeDataDir ? embeddingEnv(bridgeDataDir) : {} } catch { embedding = {} }
  return Object.assign({}, base, embedding)
}

export function adapterDir() {
  return process.env.BIOCORE_ADAPTER_DIR || DEFAULT_ADAPTER_DIR
}

export function pluginDir() {
  return process.env.BIOCORE_PLUGIN_DIR || BUNDLED_PYTHON_DIR
}

export function pythonCommand() {
  return process.env.BIOCORE_PYTHON || 'python'
}

function runBridge(args, timeoutMs) {
  const cwd = adapterDir()
  return new Promise((resolve) => {
    execFile(
      pythonCommand(),
      [BRIDGE_PATH, ...args],
      {
        cwd,
        timeout: Number(timeoutMs) || BRIDGE_TIMEOUT_MS,
        maxBuffer: 1024 * 1024,
        env: bridgeEnv(Object.assign({}, process.env, {
          BIOCORE_ADAPTER_DIR: cwd,
          BIOCORE_PLUGIN_DIR: pluginDir(),
          PYTHONIOENCODING: 'utf-8',
        })),
      },
      (error, stdout, stderr) => {
        if (error) {
          return resolve({
            ok: false,
            reason: 'bridge_failed',
            detail: String(stderr || error.message || '').slice(0, 300),
          })
        }
        const last = String(stdout || '').trim().split('\n').pop() || ''
        try {
          resolve(JSON.parse(last))
        } catch {
          resolve({ ok: false, reason: 'bridge_bad_output', detail: last.slice(0, 300) })
        }
      },
    )
  })
}

export function defaultState() {
  return runBridge(['default'])
}

export function ensureState(stateFile, profilePath) {
  const args = ['ensure', String(stateFile)]
  if (profilePath) args.push(String(profilePath))
  return runBridge(args)
}

export function runTurn(stateFile, text, profilePath) {
  const args = ['turn', String(stateFile), String(text || '')]
  if (profilePath) args.push(String(profilePath))
  return runBridge(args)
}

/** Compile persona text via stdin (args can exceed the Windows command line). */
export function compilePersona(profilePath, personaPath, text) {
  const cwd = adapterDir()
  return new Promise((resolve) => {
    const child = spawn(
      pythonCommand(),
      [BRIDGE_PATH, 'persona_compile', String(profilePath), String(personaPath)],
      {
        cwd,
        env: bridgeEnv(Object.assign({}, process.env, {
          BIOCORE_ADAPTER_DIR: cwd,
          BIOCORE_PLUGIN_DIR: pluginDir(),
          PYTHONIOENCODING: 'utf-8',
        })),
      },
    )
    let stdout = ''
    let stderr = ''
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      try { child.kill() } catch { /* ignore */ }
      resolve({ ok: false, reason: 'compile_timeout' })
    }, 60000)
    child.stdout.on('data', (chunk) => { stdout += chunk })
    child.stderr.on('data', (chunk) => { stderr += chunk })
    child.on('error', (error) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve({ ok: false, reason: 'bridge_failed', detail: String(error.message || error).slice(0, 300) })
    })
    child.on('close', (code) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      const last = String(stdout || '').trim().split('\n').pop() || ''
      try {
        resolve(JSON.parse(last))
      } catch {
        resolve({ ok: false, reason: code === 0 ? 'bridge_bad_output' : 'compile_failed',
          detail: String(stderr || last).slice(0, 300) })
      }
    })
    child.stdin.end(String(text || ''))
  })
}

// ---- thin bridge: policy lives in biocore_runtime/pipeline.py ----------
export function memoryCapture(storeRoot, sessionId, role, text, config) {
  return runBridge(['memory_capture', String(storeRoot), String(sessionId || 'default'),
    String(role || 'user'), String(text || ''),
    JSON.stringify(config && typeof config === 'object' ? config : {})])
}

export async function memoryInject(storeRoot, sessionId, maxChars, topK, rerankConfig,
  query, mode) {
  const payload = {
    session_id: String(sessionId || 'default'),
    store: String(storeRoot),
    max_chars: maxChars,
    top_k: topK,
    rerank_config: rerankConfig && typeof rerankConfig === 'object' ? rerankConfig : undefined,
  }
  if (query !== undefined && query !== null && String(query).trim()) {
    payload.query = String(query).slice(0, 2000)
  }
  if (mode !== undefined && mode !== null && String(mode).trim()) {
    payload.mode = String(mode).trim().toLowerCase()
  }
  // Query recall / rerank need the JSON action; the legacy argv form stays for
  // the old 5-argument call so nothing else changes.
  if (payload.rerank_config || payload.query || payload.mode) {
    return workerOrFallback('memory_inject', payload)
  }
  return runBridge(['memory_inject', String(storeRoot), String(sessionId || 'default'),
    maxChars === undefined ? '' : String(maxChars),
    topK === undefined ? '' : String(topK)])
}

export async function memoryTool(storeRoot, sessionId, name, args, rerankConfig) {
  const merged = Object.assign({}, args && typeof args === 'object' ? args : {})
  if (rerankConfig && typeof rerankConfig === 'object') merged.rerank_config = rerankConfig
  if (isWorkerRunning()) {
    try {
      return await callCoreWorker('tool', {
        store: String(storeRoot),
        session_id: String(sessionId || 'default'),
        name: String(name || ''),
        args: merged,
      })
    } catch (error) {
      if (!(error instanceof WorkerUnavailable)) throw error
      try { console.error('__bioWorker fallback action=tool reason=' + String(error.reason)) } catch { /* ignore */ }
    }
  }
  return runBridge(['memory_tool', String(storeRoot), String(sessionId || 'default'),
    String(name || ''), JSON.stringify(merged)])
}

export function runtimePrompt(payload) {
  return workerOrFallback('inject', payload)
}

export function runtimeGate(payload) {
  return workerOrFallback('gate', payload)
}

/** task-223: whole config arrives once; the core owns every decision. */
export function configureCore(payload) {
  return workerOrFallback('configure', payload)
}

/** Contract entry for boot/turn_start/turn_end/state/switches_get/switches_set. */
export function runtimeAction(action, payload) {
  return workerOrFallback(action, payload)
}

// ---- proactive (task-205): policy lives in biocore_runtime/pipeline.py ----
export function proactiveTick(payload) {
  return workerOrFallback('proactive_tick', payload)
}
