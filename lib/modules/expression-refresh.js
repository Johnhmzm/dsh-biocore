/** Per-step refresh of the python-produced expression prompt text (task-204).
 *
 * agent/pre-step is async, so it awaits this session's pending one_turn, reads
 * the current /state projection, asks python (action "inject") for the boot and
 * turn texts, and caches them for the synchronous prompt contexts.
 * No text formatting policy lives here: python returns the final strings.
 */
import { readStateFile, sessionStateFile, projectState } from './state.js'
import { readBodyState } from './body-state.js'
import {
  BOOT_CONTEXT_NAME,
  TURN_CONTEXT_NAME,
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
  // 全关时一次 python 也不调（#50：以前 boot+turn 无条件发两次，off 仍注入 2 次）
  const anyOn = Object.values(switches || {}).some(
    (value) => value === true || (typeof value === 'number' && value > 0))
  const userText = lastUserFor(sessionId)
  const state = statePayloadFor(dataDir, sessionId)
  const phases = []
  const calls = []
  // 全关时不发调用，但缓存不写不清（提前 return 会把 prompt 冲成空，task204:92 抓到过）
  if (anyOn && !promptTextFor(sessionId, 'boot')) {
    phases.push('boot')
    calls.push((async () => {
      try {
        const result = await callInject({
          phase: 'boot', session_id: sessionId, user_text: '', state, switches,
          body_state: readBodyState(dataDir, sessionId),
        })
        if (result && result.ok === true && typeof result.text === 'string') {
          rememberPromptText(sessionId, 'boot', result.text)
        }
      } catch { /* keep old prompt */ }
    })())
  }
  if (anyOn) {
  phases.push('turn')
  calls.push((async () => {
    try {
      const result = await callInject({
        phase: 'turn', session_id: sessionId, user_text: userText, state, switches,
          body_state: readBodyState(dataDir, sessionId),
      })
      if (result && result.ok === true && typeof result.text === 'string') {
        rememberPromptText(sessionId, 'turn', result.text)
      }
    } catch { /* keep old prompt */ }
  })())
  }
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
/**
 * Wiring switch for the *legacy* assemble waterfall.
 *
 * core-turn already registers one system-prompt/assemble listener; two of them
 * would each call python and each rewrite the same two entries. Opt-in only.
 */
export const LEGACY_REFRESH_ENV = 'BIOCORE_WIRE_LEGACY_REFRESH'

function legacyRefreshAllowed(env) {
  try {
    const source = env && typeof env === 'object' ? env : process.env
    const raw = String(source[LEGACY_REFRESH_ENV] || '').trim().toLowerCase()
    return raw === '1' || raw === 'true' || raw === 'on' || raw === 'yes'
  } catch { return false }
}

export function registerContextRefresh(ctx, options) {
  const opts = options || {}
  if (opts.force !== true && !legacyRefreshAllowed()) return () => {}
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
        // One turn may still be writing the state file; wait for it so python
        // sees the state the user message just produced.
        await pendingSessionTurn(sessionId)
        // 全关时连刷新都不做（与 refreshPromptCache 内的判断同一个表达式）
        const anyOnHere = Object.values(switches || {}).some(
          (value) => value === true || (typeof value === 'number' && value > 0))
        if (anyOnHere) await refreshPromptCache({ dataDir, sessionId, getSwitches, callInject })
        const boot = promptTextFor(sessionId, 'boot')
        const turn = promptTextFor(sessionId, 'turn')
        if (result && Array.isArray(result.contexts)) {
          result = Object.assign({}, result, {
            contexts: result.contexts.map((entry) => {
              if (!entry || typeof entry !== 'object') return entry
              // 没有新文本就不覆盖：全关时保留原有 text（task204:92 抓到过被冲成空串）
              if (entry.name === BOOT_CONTEXT_NAME) {
                return boot ? Object.assign({}, entry, { text: boot }) : entry
              }
              if (entry.name === TURN_CONTEXT_NAME) {
                return turn ? Object.assign({}, entry, { text: turn }) : entry
              }
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
