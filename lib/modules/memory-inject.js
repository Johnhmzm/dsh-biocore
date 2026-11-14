/** Thin injection forwarding: pipeline.inject_block owns the text/budget.
 *
 * 时序（2026-11-14 实机抓到两次"晚一轮"之后定死）：
 *   - DSH 先 assemble 系统提示，**之后**才派发 agent/pre-step；user/message 事件更晚。
 *     所以"等一个别处触发的 pending"永远等不到——必须**在 assemble 里同步刷一次**。
 *   - assemble 是 waterfall：provider 的 text() 已按旧值求值，我们在 next() 之前
 *     把这条 context 的文本换成刚刷好的值（没有就按 order 插进去）。
 *   - 默认**先等召回、再让模型回复**：recall.inject_wait_ms 缺省 10s，
 *     0 = 不限时（一直等到召回返回）；夹紧 0–60000。超时只在配了有限值时才可能发生，
 *     且仍然用旧文本继续（日志里 timeout=1 很显眼），绝不影响回合。
 *   - 缓存只在"提问没变"时生效（recall.inject_ttl_ms 缺省 15s）：换了一句新话就必须
 *     重新召回并等它，不许拿旧结果顶上。
 *   - 召回返回空是正常结果（库里没东西），直接继续，不会傻等。
 *   - user/message 监听保留，但只作"预热"（同一句下一轮 TTL 命中时零等待）。
 */
import { memoryInject } from '../py-bridge.js'
import { readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

export const CONTEXT_NAME = 'biocore-memory'
export const CONTEXT_ORDER = 9510
export const DEFAULT_TOP_K = 5
/** 等召回完成的上限：缺省先等（用户明确要求"先等到召回，再让模型回复"）。
 *  0 = 不限时（一直等到召回返回）；超时只在显式配了有限值时才可能发生。 */
export const DEFAULT_INJECT_WAIT_MS = 10000
export const INJECT_WAIT_MAX_MS = 60000
/** 同一句提问的缓存有效期：这之内不重复召回。 */
export const DEFAULT_INJECT_TTL_MS = 15000
export const INJECT_TTL_MAX_MS = 600000

export function createInjectionState() {
  const map = new Map()
  // 同一个对象上挂三张表：文本缓存、最近一次刷新的 promise、最近一次成功刷新的时间与提问。
  map.pending = new Map()
  map.sequence = new Map()
  map.lastRefresh = new Map()
  return map
}

/** 当前会话 id（同一个规则：prompt 组装上下文优先，其次 scope）。 */
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

/** prompt 组装上下文里的 session 对象（拿不到就 null）。 */
export function sessionOf(context) {
  try {
    const agent = context && context.agent
    if (agent && agent.session) return agent.session
    return null
  } catch { return null }
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

/** 一条 user/message 事件里的文本（只认真人发的，来源 kind === 'user'）。 */
export function userTextOf(event) {
  try {
    if (!event || event.type !== 'user/message') return ''
    const data = event.data || {}
    const source = data.source || {}
    if (source.kind !== 'user') return ''
    return textOfMessage(data)
  } catch { return '' }
}

/**
 * 这一轮的提问：从会话表面往回找最近一条真人 user/message；表面拿不到就翻日志尾部。
 * 组装发生在用户消息落盘之后，所以这里能拿到"当前这一句"。
 */
export function latestUserQuery(session) {
  if (!session) return ''
  try {
    const nodes = session.surface && Array.isArray(session.surface.nodes) ? session.surface.nodes : []
    for (let index = nodes.length - 1; index >= 0; index -= 1) {
      const text = userTextOf(session.eventAt ? session.eventAt(nodes[index]) : undefined)
      if (text) return text
    }
    const total = Number(session.seq) || 0
    for (let seq = total - 1; seq >= 0; seq -= 1) {
      const text = userTextOf(session.eventAt ? session.eventAt(seq) : undefined)
      if (text) return text
    }
  } catch { /* 拿不到就当没有 */ }
  return ''
}

function topKOf(config) {
  const raw = Number(config && config.injectTopK)
  if (!Number.isFinite(raw)) return DEFAULT_TOP_K
  return Math.max(1, Math.min(10, Math.round(raw)))
}

function debugLine(line) {
  if (!process.env.BIOCORE_DEBUG_CAPTURE) return
  try { console.error(line) } catch { /* logging must not throw */ }
}

/** recall.inject_wait_ms / recall.inject_ttl_ms：读 core_config.json（或上一层），按 mtime 缓存。 */
export function injectSettings(store, cache) {
  const defaults = { waitMs: DEFAULT_INJECT_WAIT_MS, ttlMs: DEFAULT_INJECT_TTL_MS, path: '' }
  const base = String(store || '')
  if (!base) return defaults
  const seen = cache instanceof Map ? cache : new Map()
  for (const path of [join(base, 'core_config.json'), join(base, '..', 'core_config.json')]) {
    let stamp = 0
    try { stamp = statSync(path).mtimeMs } catch { continue }
    const hit = seen.get(path)
    if (hit && hit.stamp === stamp) return hit.value
    let value = { ...defaults, path }
    try {
      const doc = JSON.parse(readFileSync(path, 'utf8'))
      const recall = doc && typeof doc === 'object' && doc.recall && typeof doc.recall === 'object'
        ? doc.recall : {}
      const wait = Number(recall.inject_wait_ms)
      if (Number.isFinite(wait) && wait >= 0) {
        // 0 = 不限时（一直等），所以这里不做"0 当缺省"的处理
        value.waitMs = Math.max(0, Math.min(INJECT_WAIT_MAX_MS, Math.round(wait)))
      }
      const ttl = Number(recall.inject_ttl_ms)
      if (Number.isFinite(ttl) && ttl >= 0) {
        value.ttlMs = Math.max(0, Math.min(INJECT_TTL_MAX_MS, Math.round(ttl)))
      }
    } catch { /* 读不到就用缺省 */ }
    seen.set(path, { stamp, value })
    return value
  }
  return defaults
}

/** 兼容旧调用：只要等待上限。 */
export function injectWaitMs(store, cache) {
  return injectSettings(store, cache).waitMs
}

/**
 * 排一次刷新：同一会话串行，且只有最后一个排队的刷新能写文本（不会乱序覆盖）。
 * 成功会记 lastRefresh（时间 + 提问），供 TTL 判定。
 */
export function queueRefresh(state, sessionId, task, options = {}) {
  const key = String(sessionId)
  if (!state || typeof state.set !== 'function' || !key) return Promise.resolve('')
  const opts = options || {}
  const now = typeof opts.now === 'function' ? opts.now : () => Date.now()
  const query = opts.query === undefined ? '' : String(opts.query)
  if (!(state.sequence instanceof Map)) state.sequence = new Map()
  if (!(state.pending instanceof Map)) state.pending = new Map()
  if (!(state.lastRefresh instanceof Map)) state.lastRefresh = new Map()
  const sequence = (state.sequence.get(key) || 0) + 1
  state.sequence.set(key, sequence)
  const previous = state.pending.get(key) || Promise.resolve()
  const started = now()
  const next = previous.catch(() => {}).then(async () => {
    let text = null
    try {
      const result = await task()
      if (result && result.ok === true) text = String(result.text || '')
    } catch (error) {
      debugLine('[inj] refresh failed ' + String((error && error.message) || error))
      text = null
    }
    // 失败就不写：旧文本原样留着（宁可旧，不可空）。
    // 只有最新一次刷新能落地：前一次即使晚到也不覆盖。
    if (text !== null && state.sequence.get(key) === sequence) {
      state.set(key, text)
      state.lastRefresh.set(key, { at: now(), query })
    }
    debugLine('[inj] refresh chars=' + (text === null ? 0 : text.length)
      + ' ms=' + (now() - started))
    return text === null ? '' : text
  }).catch(() => '')
  state.pending.set(key, next)
  next.finally(() => {
    if (state.pending.get(key) === next) state.pending.delete(key)
  }).catch(() => {})
  return next
}

/** Reload one session summary from the python pipeline and cache it. */
export function refreshInjection(storeDir, sessionId, config, state, rerankConfig,
  query) {
  if (config && config.memoryEnabled === false) {
    if (state && typeof state.set === 'function') state.set(String(sessionId), '')
    return Promise.resolve('')
  }
  return queueRefresh(state, sessionId, async () => memoryInject(storeDir, sessionId,
    config && config.injectMaxChars, topKOf(config), rerankConfig, query), { query })
}

/** 等一个刷新（有上限）。超时/失败都不抛：调用方用旧文本继续。 */
export async function awaitPendingRefresh(state, sessionId, waitMs, options = {}) {
  const key = String(sessionId)
  const opts = options || {}
  const now = typeof opts.now === 'function' ? opts.now : () => Date.now()
  const pending = state && state.pending instanceof Map ? state.pending.get(key) : undefined
  const started = now()
  if (!pending) {
    debugLine('[inj] wait ms=0 timeout=0')
    return { waitedMs: 0, timedOut: false }
  }
  // 0 = 不限时：一直等召回返回（用户要求默认先等）
  const raw = Number(waitMs)
  const budget = Number.isFinite(raw) && raw > 0 ? Math.min(INJECT_WAIT_MAX_MS, raw) : 0
  let timedOut = false
  if (budget > 0) await raceTimeout(pending, budget, () => { timedOut = true })
  else await pending.catch(() => {})
  const waitedMs = now() - started
  debugLine('[inj] wait ms=' + waitedMs + ' timeout=' + (timedOut ? 1 : 0))
  return { waitedMs, timedOut }
}

/** promise 与计时器赛跑；计时器到点后返回，不取消 promise。 */
function raceTimeout(promise, budget, onTimeout) {
  return new Promise((resolve) => {
    let done = false
    const timer = setTimeout(() => {
      if (done) return
      done = true
      onTimeout()
      resolve()
    }, budget)
    Promise.resolve(promise).then(() => {
      if (done) return
      done = true
      clearTimeout(timer)
      resolve()
    }, () => {
      if (done) return
      done = true
      clearTimeout(timer)
      resolve()
    })
  })
}

/** 把缓存文本换成这一条 context 的文本（assembled 里没有就按 order 插进去）。 */
export function applyCachedText(assembly, state, sessionId) {
  try {
    if (!assembly || !Array.isArray(assembly.contexts)) return assembly
    const key = String(sessionId)
    const text = state && typeof state.get === 'function' ? String(state.get(key) || '') : ''
    const index = assembly.contexts.findIndex((entry) => entry && entry.name === CONTEXT_NAME)
    if (index >= 0) {
      assembly.contexts[index] = { ...assembly.contexts[index], text }
      return assembly
    }
    if (!text) return assembly
    const entry = { name: CONTEXT_NAME, order: CONTEXT_ORDER, text }
    const at = assembly.contexts.findIndex((item) => Number(item && item.order) > CONTEXT_ORDER)
    assembly.contexts.splice(at < 0 ? assembly.contexts.length : at, 0, entry)
    return assembly
  } catch { return assembly }
}

/** Register the per-session cached text + the user-message refresh listener. */
export function registerMemoryInjection(ctx, getConfig, storeDir, state, options) {
  const opts = options || {}
  if (!ctx || typeof ctx.inject !== 'function') return null
  const cfg = () => {
    try { return (typeof getConfig === 'function' ? getConfig() : getConfig) || {} } catch { return {} }
  }
  const settingsCache = new Map()
  const now = typeof opts.now === 'function' ? opts.now : () => Date.now()
  const refresh = typeof opts.refresh === 'function'
    ? opts.refresh
    : (sessionId, query) => refreshInjection(storeDir, sessionId, cfg(), state,
      typeof opts.getRerankConfig === 'function' ? opts.getRerankConfig() : null, query)
  const settings = () => {
    const config = cfg()
    const base = injectSettings(storeDir, settingsCache)
    const wait = Number(config.injectWaitMs)
    const ttl = Number(config.injectTtlMs)
    return {
      waitMs: Number.isFinite(wait) ? Math.max(0, Math.min(INJECT_WAIT_MAX_MS, wait)) : base.waitMs,
      ttlMs: Number.isFinite(ttl) ? Math.max(0, Math.min(INJECT_TTL_MAX_MS, ttl)) : base.ttlMs,
    }
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
  // 唯一在 prompt 定稿之前能刷新的接缝：在这里同步刷一次，再换文本。
  try {
    if (typeof ctx.on === 'function') {
      const dispose = ctx.on('system-prompt/assemble', async (assembly, context, next) => {
        try {
          const config = cfg()
          if (config.memoryEnabled !== false) {
            const sessionId = sessionIdOf(context)
            if (sessionId) {
              const { waitMs, ttlMs } = settings()
              const query = latestUserQuery(sessionOf(context)) || lastQueryOf(state, sessionId)
              let ttlHit = 1
              if (query) {
                const last = state.lastRefresh instanceof Map ? state.lastRefresh.get(sessionId) : undefined
                // 只有"提问没变"才认 TTL：换了一句新话就必须重新召回并等它
                ttlHit = last && last.query === query && (now() - last.at) < ttlMs ? 1 : 0
                if (!ttlHit) {
                  const started = now()
                  let timedOut = false
                  const pending = refresh(sessionId, query)
                  if (pending && typeof pending.then === 'function') {
                    if (waitMs > 0) {
                      await raceTimeout(pending, waitMs, () => { timedOut = true })
                    } else {
                      // 0 = 不限时：等到召回返回为止
                      await pending.catch(() => {})
                    }
                  }
                  const waited = now() - started
                  debugLine('[inj] wait ms=' + waited + ' timeout=' + (timedOut ? 1 : 0)
                    + (timedOut ? '  ← 超过 inject_wait_ms=' + waitMs + 'ms，用旧文本继续' : ''))
                }
              }
              applyCachedText(assembly, state, sessionId)
              const applied = state && typeof state.get === 'function'
                ? String(state.get(sessionId) || '').length : 0
              debugLine('[inj] apply chars=' + applied + ' ttl_hit=' + ttlHit)
            }
          }
        } catch { /* 任何异常都不影响本轮 */ }
        return next()
      }, { prepend: true })
      if (typeof dispose === 'function') disposers.push(dispose)
    }
  } catch { /* waterfall unavailable */ }
  // 预热：用户消息一到就先刷一次，下一轮 TTL 命中时零等待（不再是唯一来源）。
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
          void refresh(sessionId, text)
        } catch { /* a refresh never disturbs a turn */ }
      })
      if (typeof dispose === 'function') disposers.push(dispose)
    }
  } catch { /* session/event unavailable */ }
  return () => { for (const dispose of disposers) { try { dispose() } catch { /* ignore */ } } }
}

/** 上一轮用过的提问（拿不到"这一句"时退回它）。 */
function lastQueryOf(state, sessionId) {
  try {
    const last = state && state.lastRefresh instanceof Map
      ? state.lastRefresh.get(String(sessionId)) : undefined
    return last && typeof last.query === 'string' ? last.query : ''
  } catch { return '' }
}
