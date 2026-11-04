/** Assistant-output safety gate shell (task-204).
 *
 * Node does not decide whether a reply crosses a boundary: the python
 * pipeline (action "gate") returns { ok, blocked, action, text, rule, label }.
 * This module only extracts the text, forwards it, and executes the action:
 *   pass              -> leave the message untouched
 *   replace/truncate  -> write the returned text verbatim
 *
 * All four flags off -> no call, no log, no mutation (old behaviour).
 */
export const GATE_LOG_PREFIX = '__bioGate'
export const ASSISTANT_EVENT = 'assistant/message'
export const USER_EVENT = 'user/message'

const LAST_USER = new Map()
const LAST_USER_MAX = 200

function textBlocksOf(message) {
  const content = message && Array.isArray(message.content) ? message.content : []
  return content.filter((block) => block && block.type === 'text' && typeof block.text === 'string')
}

/** Plain text of one assistant/message event ('' when there is none). */
export function assistantTextOf(event) {
  try {
    const message = event && event.data && event.data.message
    return textBlocksOf(message).map((block) => block.text).join('\n').trim()
  } catch { return '' }
}

/** Plain text of one user/message event. */
export function userTextOf(event) {
  try {
    const message = event && event.data && event.data.message
    return textBlocksOf(message).map((block) => block.text).join('\n').trim().slice(0, 2000)
  } catch { return '' }
}

export function rememberLastUser(sessionId, text) {
  try {
    const id = String(sessionId == null ? '' : sessionId)
    const value = String(text || '').trim()
    if (!id || !value) return false
    LAST_USER.delete(id)
    LAST_USER.set(id, value.slice(0, 2000))
    while (LAST_USER.size > LAST_USER_MAX) LAST_USER.delete(LAST_USER.keys().next().value)
    return true
  } catch { return false }
}

export function lastUserFor(sessionId) {
  try { return LAST_USER.get(String(sessionId == null ? '' : sessionId)) || '' } catch { return '' }
}

/** Replace the text block(s) of an assistant message; non-text blocks survive. */
export function applyGateText(event, text) {
  try {
    const message = event && event.data && event.data.message
    if (!message || !Array.isArray(message.content)) return false
    let written = false
    message.content = message.content.map((block) => {
      if (!block || block.type !== 'text' || typeof block.text !== 'string') return block
      if (written) return null
      written = true
      return Object.assign({}, block, { text: String(text) })
    }).filter(Boolean)
    return written
  } catch { return false }
}

/** One line for the host log; bounded metadata only. */
export function gateLogLine(decision, info) {
  const data = decision && typeof decision === 'object' ? decision : {}
  const extra = info && typeof info === 'object' ? info : {}
  return GATE_LOG_PREFIX
    + ' session=' + String(extra.sessionId || '-')
    + ' action=' + String(data.action || 'pass')
    + ' blocked=' + (data.blocked === true ? '1' : '0')
    + ' rule=' + String(data.rule || '')
    + ' chars=' + String(extra.before || 0) + '->' + String(extra.after || 0)
}

/** Execute a python gate reply against one assistant event. */
export function applyGateDecision(event, decision) {
  const data = decision && typeof decision === 'object' ? decision : {}
  const action = String(data.action || 'pass')
  if (action !== 'replace' && action !== 'truncate') return false
  if (typeof data.text !== 'string' || !data.text) return false
  return applyGateText(event, data.text)
}

/** Register the assistant gate; returns the disposer. */
export function registerSafetyGate(ctx, options) {
  const opts = options || {}
  const getSwitches = typeof opts.getSwitches === 'function' ? opts.getSwitches : () => ({})
  const runGate = typeof opts.runGate === 'function' ? opts.runGate : null
  const log = typeof opts.log === 'function' ? opts.log : (line) => {
    try { console.error(line) } catch { /* logging must not throw */ }
  }
  if (!ctx || typeof ctx.on !== 'function' || !runGate) return () => {}
  try {
    return ctx.on('session/event', async (session, event) => {
      try {
        const kind = event && event.type
        const sessionId = session && session.id != null ? String(session.id) : ''
        if (kind === USER_EVENT) {
          const text = userTextOf(event)
          if (sessionId && text) rememberLastUser(sessionId, text)
          return
        }
        if (kind !== ASSISTANT_EVENT) return
        const switches = getSwitches()
        const reply = assistantTextOf(event)
        if (!reply) return
        const decision = await runGate({
          reply,
          user_text: lastUserFor(sessionId),
          switches,
        })
        if (!decision || decision.ok !== true) return
        const before = reply.length
        const action = String(decision.action || 'pass')
        if (action !== 'replace' && action !== 'truncate') return
        if (typeof decision.text !== 'string' || !decision.text) return
        const applied = applyGateText(event, decision.text)
        log(gateLogLine(decision, {
          sessionId,
          before,
          after: decision.text.length,
          applied,
        }))
      } catch { /* never disturb the turn */ }
    })
  } catch { return () => {} }
}

export function resetGateState() { LAST_USER.clear() }
