/** Thin Node -> Python runtime client (task-204 contract).
 *
 * Policy lives in python: <plugin>/biocore_runtime/pipeline.py.
 * Node only forwards JSON to it and returns the parsed reply.
 *
 *   python <plugin>/biocore_runtime/pipeline.py <action>
 *     JSON body on stdin, one JSON line on stdout
 *     actions: inject | gate | capture | tool
 *
 * Everything is best-effort: a missing/unavailable pipeline returns
 * { ok:false, reason } and the caller keeps the old behaviour.
 */
import { execFile } from 'node:child_process'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const ACTIONS = Object.freeze(['inject', 'gate', 'capture', 'tool'])
export const DEFAULT_PLUGIN_DIR = join(homedir(), 'Desktop', 'deepseek', 'astrbot_plugin_biocore_standalone')
export const PIPELINE_TIMEOUT_MS = 15000

export function pluginDir() {
  const value = String(process.env.BIOCORE_PLUGIN_DIR || '').trim()
  return value || DEFAULT_PLUGIN_DIR
}

export function pipelinePath() {
  return join(pluginDir(), 'biocore_runtime', 'pipeline.py')
}

export function pythonCommand() {
  const value = String(process.env.BIOCORE_PYTHON || '').trim()
  return value || 'python'
}

/** Forward one action + JSON payload to the python pipeline. Never throws. */
export function callPipeline(action, payload, options) {
  const opts = options && typeof options === 'object' ? options : {}
  const name = String(action || '').trim()
  const timeoutMs = Number.isFinite(Number(opts.timeoutMs))
    ? Number(opts.timeoutMs) : PIPELINE_TIMEOUT_MS
  if (!ACTIONS.includes(name)) {
    return Promise.resolve({ ok: false, reason: 'bad_action', action: name })
  }
  return new Promise((resolve) => {
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    let child
    try {
      child = execFile(
        pythonCommand(),
        [pipelinePath(), name],
        {
          timeout: timeoutMs,
          maxBuffer: 1024 * 1024,
          env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }),
        },
        (error, stdout) => {
          if (error) {
            return finish({
              ok: false,
              reason: 'pipeline_failed',
              action: name,
              detail: String((error && error.message) || '').slice(0, 300),
            })
          }
          const lines = String(stdout || '').trim().split('\n').filter(Boolean)
          const last = lines.length ? lines[lines.length - 1] : ''
          try {
            const parsed = JSON.parse(last)
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
              return finish({ ok: false, reason: 'pipeline_bad_output', action: name })
            }
            return finish(parsed)
          } catch {
            return finish({ ok: false, reason: 'pipeline_bad_output', action: name })
          }
        },
      )
    } catch (error) {
      return finish({
        ok: false,
        reason: 'pipeline_spawn_failed',
        action: name,
        detail: String((error && error.message) || '').slice(0, 300),
      })
    }
    try {
      child.stdin.end(JSON.stringify(payload && typeof payload === 'object' ? payload : {}))
    } catch {
      return finish({ ok: false, reason: 'pipeline_stdin_failed', action: name })
    }
  })
}

export const runtimePromptClient = (payload) => callPipeline('inject', payload)
export const runtimeGateClient = (payload) => callPipeline('gate', payload)
