/** Per-step refresh of the python-produced expression prompt text (task-204).
 *
 * agent/pre-step is async, so it awaits this session's pending one_turn, reads
 * the current /state projection, asks python (action "inject") for the boot and
 * turn texts, and caches them for the synchronous prompt contexts.
 * No text formatting policy lives here: python returns the final strings.
 */
import { readStateFile, sessionStateFile, projectState } from './state.js'
import {
  BOOT_CONTEXT_NAME,
  TURN_CONTEXT_NAME,
  anySwitchOn,
  promptTextFor,
  rememberPromptText,
  clearPromptText,
  sessionIdOf,
} from './expression-inject.js'
import { lastUserFor } from './safety-gate.js'
import { pendingSessionTurn } from './turn-queue.js'

/** Current /state projection for the prompt payload ({} when unavailable). */
export function statePayloadFor(dataDir, sessionId) {
  try {
    const read = readStateFile(sessionStateFile(dataDir, sessionId))
    if (!read || read.error || read.missing || !read.state) return {}
    return projectState(read.state, { mtimeMs: read.mtimeMs, sessionId })
  } catch { return {} }
}

/**
 * Refresh the cached prompt texts for one session.
 * @returns {Promise<{refreshed:boolean,reason?:string,phases?:string[]}>}
 */
export async function refreshPromptCache(options) {
  const opts = options || {}
  const dataDir = opts.dataDir
  const sessionId = opts.sessionId == null ? '' : String(opts.sessionId)
  const getSwitches = typeof opts.getSwitches === 'function' ? opts.getSwitches : () => ({})
  const callInject = typeof opts.callInject === 'function' ? opts.callInject : null
  if (!sessionId || !callInject) return { refreshed: false, reason: 'unavailable' }
  const switches = getSwitches()
  if (!anySwitchOn(switches)) {
    clearPromptText(sessionId)
    return { refreshed: false, reason: 'switches_off' }
  }
  const userText = lastUserFor(sessionId)
  const state = statePayloadFor(dataDir, sessionId)
  const phases = []
  const calls = []
  if (!promptTextFor(sessionId, 'boot')) {
    phases.push('boot')
    calls.push((async () => {
      try {
        const result = await callInject({
          phase: 'boot', session_id: sessionId, user_text: '', state, switches,
        })
        if (result && result.ok === true && typeof result.text === 'string') {
          rememberPromptText(sessionId, 'boot', result.text)
        }
      } catch { /* keep old prompt */ }
    })())
  }
  phases.push('turn')
  calls.push((async () => {
    try {
      const result = await callInject({
        phase: 'turn', session_id: sessionId, user_text: userText, state, switches,
      })
      if (result && result.ok === true && typeof result.text === 'string') {
        rememberPromptText(sessionId, 'turn', result.text)
      }
    } catch { /* keep old prompt */ }
  })())
  await Promise.all(calls)
  return { refreshed: true, phases }
}
/**
 * Register the system-prompt assembly waterfall.
 *
 * DSH resolves registered context providers *before* the agent/pre-step
 * waterfall, so a pre-step cache refresh would reach the prompt one turn late.
 * This listener is the earliest async point that still affects the same step:
 * it awaits the session's pending one_turn, asks python for the text, then
 * rewrites the two resolved context entries with the fresh python text.
 * Node still only transports strings; all wording comes from python.
 */
export function registerContextRefresh(ctx, options) {
  const opts = options || {}
  const dataDir = opts.dataDir
  const getSwitches = typeof opts.getSwitches === 'function' ? opts.getSwitches : () => ({})
  const callInject = typeof opts.callInject === 'function' ? opts.callInject : null
  const log = typeof opts.log === 'function' ? opts.log : (line) => {
    try { console.error(line) } catch { /* logging must not throw */ }
  }
  if (!ctx || typeof ctx.on !== 'function' || !callInject) return () => {}
  try {
    return ctx.on('system-prompt/assemble', async (assembly, context, next) => {
      let result = null
      try {
        result = await next()
      } catch (error) {
        throw error
      }
      if (!result || typeof result !== 'object') result = assembly
      try {
        const switches = getSwitches()
        const sessionId = sessionIdOf(context)
        if (!sessionId) return result
        if (!anySwitchOn(switches)) {
          clearPromptText(sessionId)
          return result
        }
        // One turn may still be writing the state file; wait for it so python
        // sees the state the user message just produced.
        await pendingSessionTurn(sessionId)
        await refreshPromptCache({ dataDir, sessionId, getSwitches, callInject })
        const boot = promptTextFor(sessionId, 'boot')
        const turn = promptTextFor(sessionId, 'turn')
        if (result && Array.isArray(result.contexts)) {
          result = Object.assign({}, result, {
            contexts: result.contexts.map((entry) => {
              if (!entry || typeof entry !== 'object') return entry
              if (entry.name === BOOT_CONTEXT_NAME) return Object.assign({}, entry, { text: boot })
              if (entry.name === TURN_CONTEXT_NAME) return Object.assign({}, entry, { text: turn })
              return entry
            }),
          })
        }
        if (turn) {
          log('__bioInject phase=turn session=' + sessionId + ' chars=' + turn.length + ' via=assemble')
        }
      } catch { /* keep the unmodified assembly */ }
      return result
    })
  } catch { return () => {} }
}
