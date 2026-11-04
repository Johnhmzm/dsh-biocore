/** Prompt contexts for the Python-produced expression block (task-204).
 *
 * Node contribution is transport only: python (biocore_runtime/pipeline.py,
 * action "inject") decides the text; the async agent/pre-step hook refreshes a
 * per-session cache and these synchronous DSH context providers return it.
 *
 *   boot  order -200  phase=boot  (stable intro from python)
 *   turn  order -190  phase=turn  (state block + expression constraints)
 *
 * All four flags off means empty contexts and no injected text (old prompt).
 * No truncation, no local formatting policy lives here.
 */
export const BOOT_CONTEXT_NAME = 'biocore-expression-boot'
export const TURN_CONTEXT_NAME = 'biocore-expression-turn'
export const BOOT_ORDER = -200
export const TURN_ORDER = -190

const PROMPT_CACHE = new Map()
const CACHE_MAX = 200

function keyOf(sessionId, phase) {
  return String(phase || 'turn') + ':' + String(sessionId == null ? '' : sessionId)
}

/** Session id from the prompt-assembly context (same shape as the reference). */
export function sessionIdOf(context) {
  try {
    const agent = context && context.agent
    if (!agent) return ''
    const raw = agent.sessionId != null ? agent.sessionId : (agent.session && agent.session.id)
    return raw == null ? '' : String(raw)
  } catch { return '' }
}

/** Cache one python-produced prompt text ('' clears it). */
export function rememberPromptText(sessionId, phase, text) {
  try {
    const id = String(sessionId == null ? '' : sessionId)
    if (!id) return false
    const key = keyOf(id, phase)
    if (!text) {
      PROMPT_CACHE.delete(key)
      return true
    }
    PROMPT_CACHE.delete(key)
    PROMPT_CACHE.set(key, String(text))
    while (PROMPT_CACHE.size > CACHE_MAX) PROMPT_CACHE.delete(PROMPT_CACHE.keys().next().value)
    return true
  } catch { return false }
}

/** Cached python text for (session, phase); '' when absent. */
export function promptTextFor(sessionId, phase) {
  try { return PROMPT_CACHE.get(keyOf(sessionId, phase)) || '' } catch { return '' }
}

export function clearPromptText(sessionId) {
  try {
    const id = String(sessionId == null ? '' : sessionId)
    PROMPT_CACHE.delete(keyOf(id, 'boot'))
    PROMPT_CACHE.delete(keyOf(id, 'turn'))
    return true
  } catch { return false }
}

export function resetPromptTexts() { PROMPT_CACHE.clear() }

/** Register both prompt contexts; text comes verbatim from python. */
export function registerExpressionInjections(ctx, options) {
  const opts = options || {}
  const getSwitches = typeof opts.getSwitches === 'function' ? opts.getSwitches : () => ({})
  const getText = typeof opts.getText === 'function'
    ? opts.getText
    : (phase, sessionId) => promptTextFor(sessionId, phase)
  const disposers = []
  try {
    if (!ctx || typeof ctx.inject !== 'function') return () => {}
    ctx.inject(['systemPrompt'], (serviceCtx) => {
      const systemPrompt = serviceCtx && serviceCtx.systemPrompt
      if (!systemPrompt || typeof systemPrompt.context !== 'function') return
      const providers = [
        [BOOT_CONTEXT_NAME, BOOT_ORDER, 'boot'],
        [TURN_CONTEXT_NAME, TURN_ORDER, 'turn'],
      ]
      for (const [name, order, phase] of providers) {
        try {
          const dispose = systemPrompt.context({
            name,
            order,
            text: (context) => {
              try {
                const sessionId = sessionIdOf(context)
                if (!sessionId) return ''
                return String(getText(phase, sessionId) || '')
              } catch { return '' }
            },
          })
          if (typeof dispose === 'function') disposers.push(dispose)
        } catch { /* context rejected */ }
      }
    })
  } catch { /* cordis inject unavailable */ }
  return () => {
    for (const dispose of disposers) {
      try { if (typeof dispose === 'function') dispose() } catch { /* ignore */ }
    }
  }
}
