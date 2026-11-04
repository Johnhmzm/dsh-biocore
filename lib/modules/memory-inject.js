/** Thin injection forwarding: pipeline.inject_block owns the text/budget.
 *
 * DSH asks for prompt context synchronously, so this module only caches the
 * text returned by the python bridge per session; formatting and truncation
 * never happen in Node.
 */
import { memoryInject } from '../py-bridge.js'

export const CONTEXT_NAME = 'biocore-memory'
export const CONTEXT_ORDER = -190

export function createInjectionState() {
  return new Map()
}

/** Current session id from the prompt assembly context (same rule as reference). */
export function sessionIdOf(context) {
  try {
    const agent = context && context.agent
    if (!agent) {
      const scope = context && context.scope
      const raw = scope && (scope.sessionId || (scope.session && scope.session.id))
      return raw == null ? '' : String(raw)
    }
    const raw = agent.sessionId != null
      ? agent.sessionId
      : (agent.session && agent.session.id)
    return raw == null ? '' : String(raw)
  } catch { return '' }
}

/** Reload one session summary from the python pipeline and cache it. */
export async function refreshInjection(storeDir, sessionId, config, state) {
  if (!state || typeof state.set !== 'function') return ''
  if (config && config.memoryEnabled === false) {
    state.set(String(sessionId), '')
    return ''
  }
  const result = await memoryInject(storeDir, sessionId,
    config && config.injectMaxChars, 5)
  const text = result && result.ok === true ? String(result.text || '') : ''
  state.set(String(sessionId), text)
  return text
}

/** Register the per-session cached text as a dynamic system prompt context. */
export function registerMemoryInjection(ctx, getConfig, storeDir, state) {
  if (!ctx || typeof ctx.inject !== 'function') return null
  const cfg = () => {
    try { return (typeof getConfig === 'function' ? getConfig() : getConfig) || {} } catch { return {} }
  }
  const disposers = []
  try {
    ctx.inject(['systemPrompt'], (serviceCtx) => {
      const systemPrompt = serviceCtx && serviceCtx.systemPrompt
      if (!systemPrompt || typeof systemPrompt.context !== 'function') return
      try {
        const dispose = systemPrompt.context({
          name: CONTEXT_NAME,
          order: CONTEXT_ORDER,
          text: (context) => {
            const config = cfg()
            if (config.memoryEnabled === false) return ''
            const sessionId = sessionIdOf(context)
            if (!sessionId || !state || typeof state.get !== 'function') return ''
            return String(state.get(sessionId) || '')
          },
        })
        if (typeof dispose === 'function') disposers.push(dispose)
      } catch { /* prompt context rejected */ }
    })
  } catch { /* cordis inject unavailable */ }
  return () => { for (const dispose of disposers) { try { dispose() } catch { /* ignore */ } } }
}
