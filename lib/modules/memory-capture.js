/** Thin capture forwarding: the decision lives in pipeline.capture_decision.
 *
 * Node keeps only DSH event adaptation (which event is a message, where the
 * text is, which session it belongs to); thresholds, dedupe, hourly limits and
 * storage all run in the python bridge.
 */
import { memoryCapture } from '../py-bridge.js'
import { refreshInjection } from './memory-inject.js'

export function textOfMessage(message) {
  try {
    const blocks = message && Array.isArray(message.content) ? message.content : []
    const parts = []
    for (const block of blocks) {
      if (block && block.type === 'text' && typeof block.text === 'string') parts.push(block.text)
    }
    return parts.join('\n').replace(/\s+/g, ' ').trim().slice(0, 2000)
  } catch { return '' }
}

export function messageKind(event) {
  if (!event || typeof event.type !== 'string') return null
  const data = event.data || {}
  if (event.type === 'user/message') {
    const source = data.source || {}
    return source.kind === 'user' ? 'user' : null
  }
  if (event.type === 'assistant/message') return 'assistant'
  return null
}

export function sessionIdOf(session) {
  return String((session && session.id) || 'global')
}

/** Subscribe to session/event and forward to the python pipeline. */
export function registerMemoryCapture(ctx, getConfig, storeDir, options = {}) {
  if (!ctx || typeof ctx.on !== 'function') return null
  const cfg = () => {
    try { return (typeof getConfig === 'function' ? getConfig() : getConfig) || {} } catch { return {} }
  }
  const injectionState = options.injectionState || null
  let chain = Promise.resolve()
  const capture = (session, event) => {
    try {
      const config = cfg()
      if (config.memoryEnabled === false) return
      const kind = messageKind(event)
      if (!kind) return
      const text = textOfMessage(event.data)
      if (!text) return
      // 会话 id 兜底：宿主不同事件里 id 可能只挂在 session 或 event.data 上
      const sessionId = sessionIdOf(session)
        || (event && event.data && event.data.sessionId) || 'global'
      chain = chain.then(async () => {
        const result = await memoryCapture(storeDir, sessionId, kind, text, {
          enabled: config.memoryEnabled !== false,
          hourly_cap: config.hourlyCap,
          min_chars: config.memoryMinChars,
        })
        if (process.env.BIOCORE_DEBUG_CAPTURE) {
          console.log('[cap]', kind, sessionId, String(text).length,
            result && result.captured, result && result.reason)
        }
        if (result && result.ok === true && result.captured === true && injectionState) {
          const rerank = typeof options.getRerankConfig === 'function' ? options.getRerankConfig() : null
      await refreshInjection(storeDir, sessionId, config, injectionState, rerank)
        }
      }).catch((error) => {
        // 别只吞：debug 打开时把异常打出来（默认静默，capture 不打扰对话）
        if (process.env.BIOCORE_DEBUG_CAPTURE) {
          console.log('[cap] chain error', kind, sessionId, error && error.message)
        }
      })
    } catch { /* contained */ }
  }
  let dispose
  try {
    dispose = ctx.on('session/event', capture)
  } catch { return null }
  return {
    capture,
    dispose: () => { try { if (typeof dispose === 'function') dispose() } catch { /* ignore */ } },
    drain: () => chain,
  }
}
