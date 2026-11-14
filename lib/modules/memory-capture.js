/** Thin capture forwarding: the decision lives in pipeline.capture_decision.
 *
 * Node keeps only DSH event adaptation (which event is a message, where the
 * text is, which session it belongs to); thresholds, dedupe, hourly limits and
 * storage all run in the python bridge.
 */
import { compactionRecord, memoryCapture } from '../py-bridge.js'

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

/** compaction/summary 的正文：只拼 text 块，逐字保留（不折叠空白）。
 *
 * 刻意不学 textOfMessage：摘要要和日志原文逐字比对，多空格也是原文。
 */
export function compactionText(blocks) {
  const list = Array.isArray(blocks) ? blocks : []
  const parts = []
  for (const block of list) {
    if (block && block.type === 'text' && typeof block.text === 'string') parts.push(block.text)
  }
  return parts.join('\n')
}

/** DSH 官方压缩事件的搬运：只取字段，不做判定（判定在 python 侧）。 */
export function compactionPayload(event, sessionId) {
  const data = (event && event.data) || {}
  const range = data.shadowedRange || {}
  const seqs = Array.isArray(data.shadowedSeqs) ? data.shadowedSeqs : []
  const num = (value) => (typeof value === 'number' && Number.isFinite(value) ? value : null)
  return {
    session_id: String(sessionId || 'global'),
    compaction_id: typeof data.compactionId === 'string' ? data.compactionId : '',
    range_start: num(range.start),
    range_end: num(range.end),
    // 条数就是 shadowedSeqs 的长度：1827 条序号本身不搬（表只存条数）
    shadowed_count: seqs.length,
    shadowed_tokens: num(data.shadowedTokenCount),
    provider: typeof data.provider === 'string' ? data.provider : '',
    model: typeof data.model === 'string' ? data.model : '',
    usage: data.usage && typeof data.usage === 'object' ? data.usage : null,
    text: compactionText(data.summary),
    // 压缩发生的时刻（DSH 用毫秒）：召回时间窗的上界，缺了就只能退到落库时刻
    covered_at: num(event && event.time),
  }
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
      // 会话 id 兜底：宿主不同事件里 id 可能只挂在 session 或 event.data 上
      const sessionId = sessionIdOf(session)
        || (event && event.data && event.data.sessionId) || 'global'
      if (event && event.type === 'compaction/summary') {
        // DSH 官方压缩写下的摘要：正文照收，判定/落库在 python 侧（append-only）。
        // 与消息 capture 同一条 chain，同一份 config 开关，不另开旁路。
        const payload = compactionPayload(event, sessionId)
        if (!payload.compaction_id || !payload.text) return
        chain = chain.then(async () => {
          const result = await compactionRecord(storeDir, sessionId, payload)
          if (process.env.BIOCORE_DEBUG_CAPTURE) {
            console.log('[cap] compaction', sessionId, payload.compaction_id,
              result && result.recorded, result && result.reason)
          }
        }).catch((error) => {
          if (process.env.BIOCORE_DEBUG_CAPTURE) {
            console.log('[cap] compaction chain error', sessionId, error && error.message)
          }
        })
        return
      }
      const kind = messageKind(event)
      if (!kind) return
      const text = textOfMessage(event.data)
      if (!text) return
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
