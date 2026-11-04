/** Node wrapper around lib/py/bridge.py (python subprocess, never in-process). */
import { execFile, spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const BRIDGE_PATH = fileURLToPath(new URL('./py/bridge.py', import.meta.url))
export const PACKAGE_ROOT = fileURLToPath(new URL('..', import.meta.url))
export const BUNDLED_PYTHON_DIR = join(PACKAGE_ROOT, 'python')
export const DEFAULT_ADAPTER_DIR = join(BUNDLED_PYTHON_DIR, 'dsh_adapter')
export const BRIDGE_TIMEOUT_MS = 20000

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
        env: Object.assign({}, process.env, {
          BIOCORE_ADAPTER_DIR: cwd,
          BIOCORE_PLUGIN_DIR: pluginDir(),
          PYTHONIOENCODING: 'utf-8',
        }),
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
        env: Object.assign({}, process.env, {
          BIOCORE_ADAPTER_DIR: cwd,
          BIOCORE_PLUGIN_DIR: pluginDir(),
          PYTHONIOENCODING: 'utf-8',
        }),
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

export function memoryInject(storeRoot, sessionId, maxChars, topK) {
  return runBridge(['memory_inject', String(storeRoot), String(sessionId || 'default'),
    maxChars === undefined ? '' : String(maxChars),
    topK === undefined ? '' : String(topK)])
}

export function memoryTool(storeRoot, sessionId, name, args) {
  return runBridge(['memory_tool', String(storeRoot), String(sessionId || 'default'),
    String(name || ''), JSON.stringify(args && typeof args === 'object' ? args : {})])
}

export function runtimePrompt(payload) {
  return runBridge(['runtime_prompt',
    JSON.stringify(payload && typeof payload === 'object' ? payload : {})])
}

export function runtimeGate(payload) {
  return runBridge(['runtime_gate',
    JSON.stringify(payload && typeof payload === 'object' ? payload : {})])
}

/** Contract entry for boot/turn_start/turn_end/state/switches_get/switches_set. */
export function runtimeAction(action, payload) {
  return runBridge(['runtime_action', String(action || ''),
    JSON.stringify(payload && typeof payload === 'object' ? payload : {})])
}

// ---- proactive (task-205): policy lives in biocore_runtime/pipeline.py ----
export function proactiveTick(payload) {
  return runBridge(['proactive_tick',
    JSON.stringify(payload && typeof payload === 'object' ? payload : {})])
}
