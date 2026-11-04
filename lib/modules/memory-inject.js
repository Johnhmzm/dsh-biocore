/** Thin injection forwarding: pipeline.inject_block owns the text/budget.
 *
 * DSH asks for prompt context synchronously, so this module only caches the
 * text returned by the python bridge per session; formatting and truncation
 * never happen in Node.
 *
 * task-221: refresh uses the user's own words (mode=query) so retrieval is a
 * query recall, not "newest N"; python falls back to the recent ordering when
 * the engine/embedding/rerank chain is unavailable.
 */
import { memoryInject } from '../py-bridge.js'

export const CONTEXT_NAME = 'biocore-memory'
export const CONTEXT_ORDER = 9510
export const DEFAULT_TOP_K = 5

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

function textOfMessage(message) {
  try {
    const blocks = message && Array.isArray(message.content) ? message.content : []
    const parts = []
    for (const block of blocks) {
      if (block && block.type === 'text' && typeof block.text === 'string') parts.push(block.text)
    }
    return parts.join('\n').replace(/\s+/g, ' ').trim().slice(0, 2000)
  } catch { return '' }
}

function topKOf(config) {
  const raw = Number(config && config.injectTopK)
  if (!Number.isFinite(raw)) return DEFAULT_TOP_K
  return Math.max(1, Math.min(10, Math.round(raw)))
}

/** Reload one session summary from the python pipeline and cache it. */
export async function refreshInjection(storeDir, sessionId, config, state, rerankConfig,
  query) {
  if (!state || typeof state.set !== 'function') return ''
  if (config && config.memoryEnabled === false) {
    state.set(String(sessionId), '')
    return ''
  }
  const result = await memoryInject(storeDir, sessionId,
    config && config.injectMaxChars, topKOf(config), rerankConfig, query)
  const text = result && result.ok === true ? String(result.text || '') : ''
  state.set(String(sessionId), text)
  return text
}

/** Register the per-session cached text + the user-message refresh listener. */
export function registerMemoryInjection(ctx, getConfig, storeDir, state, options) {
  const opts = options || {}
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
  try {
    if (typeof ctx.on === 'function') {
      const dispose = ctx.on('session/event', (session, event) => {
        try {
          if (!event || event.type !== 'user/message') return
          const data = event.data || {}
          const source = data.source || {}
          if (source.kind !== 'user') return
          const sessionId = String((session && session.id) || '')
          const text = textOfMessage(data)
          const config = cfg()
          if (!sessionId || !text || config.memoryEnabled === false) return
          const rerank = typeof opts.getRerankConfig === 'function'
            ? opts.getRerankConfig() : null
          void refreshInjection(storeDir, sessionId, config, state, rerank,
            text, 'query')
        } catch { /* a refresh never disturbs a turn */ }
      })
      if (typeof dispose === 'function') disposers.push(dispose)
    }
  } catch { /* session/event unavailable */ }
  return () => { for (const dispose of disposers) { try { dispose() } catch { /* ignore */ } } }
}
