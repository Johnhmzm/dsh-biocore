/** 压缩记录落库：主路径读会话对象，兜底才扫日志。
 *
 * 背景：session/event 只把 surface 类事件（user/assistant 消息）送到插件，
 * compaction/summary 到不了；但宿主会话对象里有完整事件表（内存快照），
 * 读它是毫秒级、不用解压日志。所以分两级：
 *
 *   一级（默认，每回合都跑，不节流）
 *     从 session.snapshotEvents(fromSeq) 取增量事件（退而求其次 ownEvents()），
 *     挑 type === 'compaction/summary' 的交给桥落库；每会话记住已处理的
 *     compactionId 与最后处理的 seq，重复输入不会再调一次桥。
 *   二级（兜底，保留 60 秒节流 + 日志 mtime 判定）
 *     只有「一级一条事件都拿不到」或「一级拿到事件但里面没有 compaction/summary，
 *     而日志 mtime 变了」才解压会话日志——因为压缩确实发生了、内存里却没看到。
 *
 * 三条纪律不变：fire-and-forget（回合不等它）、二级节流、失败静默
 * （BIOCORE_DEBUG_CAPTURE=1 时打一行自检，主路径的格式是
 *  [scan] mem events=<n> compactions=<m>）。
 */
import { statSync } from 'node:fs'
import { compactionRecord, compactionScan } from '../py-bridge.js'
import { compactionPayload, sessionIdOf } from './memory-capture.js'

export const SCAN_INTERVAL_MS = 60000
/** 触发的会话事件：一个回合一开头看一次，看的是上一个回合写完的压缩。
 *
 * 两个都收，因为不确定哪个能到：turn/start 是语义上正确的那个（日志里确实有
 * {"type":"turn/start","seq":5,"data":{"turn":1}}），但 web/desktop 拓扑下
 * 它是否经 session/event 广播到插件，没重启实例就验不了；user/message 是确定
 * 能到的（capture 一直靠它）。两者落在同一个回合的开头，一级不节流，
 * 一级靠 compactionId 去重，二级靠 60 秒节流——顺序反过来也不会重复落库。
 */
export const SCAN_TRIGGER_TYPES = Object.freeze(['turn/start', 'user/message'])
/** 要收的事件类型：DSH 官方压缩写下的摘要。 */
export const COMPACTION_EVENT = 'compaction/summary'
const SESSION_CAP = 200
const ID_CAP = 500

/** stat 一份日志（拿不到返回 null：拿不到就不拿它做跳过依据）。 */
export function statOf(path) {
  try {
    if (!path) return null
    const stats = statSync(String(path))
    return { mtimeMs: stats.mtimeMs, size: stats.size }
  } catch { return null }
}

/** 该不该走二级（日志）：没有记录 -> 扫；60 秒内 -> 不扫；日志没变 -> 不扫。 */
export function shouldScan(state, now, stats) {
  if (!state || !Number.isFinite(Number(state.at))) return true
  if (Number(now) - Number(state.at) < SCAN_INTERVAL_MS) return false
  if (stats && Number.isFinite(stats.mtimeMs)
    && state.mtimeMs === stats.mtimeMs && state.size === stats.size) return false
  return true
}

/** 会话对象里的完整事件表（内存快照，不解压日志）。
 *
 * 返回 {events, source}：events 为 null 表示宿主两种 API 都没有（只能走日志）。
 * 宿主把这两个标了 deprecated，但 web/desktop 上仍可用；谁抛异常就换下一个。
 */
export function memoryEventsOf(session, fromSeq) {
  const from = Number.isFinite(Number(fromSeq)) ? Number(fromSeq) : 0
  try {
    if (session && typeof session.snapshotEvents === 'function') {
      const list = session.snapshotEvents(from)
      if (Array.isArray(list)) return { events: list, source: 'snapshot' }
    }
  } catch { /* 退到下一个 */ }
  try {
    if (session && typeof session.ownEvents === 'function') {
      const list = session.ownEvents()
      if (Array.isArray(list)) return { events: list, source: 'own' }
    }
  } catch { /* 两条都没有 */ }
  return { events: null, source: '' }
}

/** 事件里的 compaction/summary（顺序保持不变）。 */
export function compactionsOf(events) {
  if (!Array.isArray(events)) return []
  const out = []
  for (const event of events) {
    if (event && event.type === COMPACTION_EVENT) out.push(event)
  }
  return out
}

/** 事件表里最大的 seq（拿不到给 null：调用方据此决定要不要推进游标）。 */
export function lastSeqOf(events) {
  if (!Array.isArray(events)) return null
  let last = null
  for (const event of events) {
    const seq = Number(event && event.seq)
    if (Number.isFinite(seq) && (last === null || seq > last)) last = seq
  }
  return last
}

function trimSet(set, cap) {
  while (set.size > cap) set.delete(set.values().next().value)
}

function trimStates(states) {
  while (states.size > SESSION_CAP) states.delete(states.keys().next().value)
}

/** Subscribe to session/event: memory events first, the session log as fallback. */
export function registerCompactionScan(ctx, getConfig, storeDir, options = {}) {
  if (!ctx || typeof ctx.on !== 'function') return null
  const opts = options || {}
  const cfg = () => {
    try { return (typeof getConfig === 'function' ? getConfig() : getConfig) || {} } catch { return {} }
  }
  const now = typeof opts.now === 'function' ? opts.now : () => Date.now()
  const record = typeof opts.record === 'function'
    ? opts.record
    : (dir, sessionId, payload) => compactionRecord(dir, sessionId, payload)
  const scan = typeof opts.scan === 'function'
    ? opts.scan
    : (dir, sessionId) => compactionScan(dir, sessionId)
  const stat = typeof opts.stat === 'function' ? opts.stat : statOf
  const triggers = Array.isArray(opts.triggerTypes) && opts.triggerTypes.length
    ? opts.triggerTypes : SCAN_TRIGGER_TYPES
  const states = new Map()
  let chain = Promise.resolve()
  const debug = (line) => {
    if (!process.env.BIOCORE_DEBUG_CAPTURE) return
    try { console.log('[scan] ' + line) } catch { /* logging must not throw */ }
  }

  const stateOf = (sessionId) => {
    let state = states.get(sessionId)
    if (!state) {
      state = { at: NaN, log: '', mtimeMs: undefined, size: undefined, seq: null, ids: new Set() }
      states.set(sessionId, state)
      trimStates(states)
    }
    return state
  }

  const trigger = (session, event) => {
    try {
      const type = event && event.type
      if (typeof type !== 'string' || !triggers.includes(type)) return false
      if (cfg().compactionScan === false) return false
      const sessionId = sessionIdOf(session)
        || (event && event.data && event.data.sessionId) || ''
      if (!sessionId) return false
      const state = stateOf(sessionId)

      // ---- 一级：会话对象的事件表（内存，毫秒级，不节流）
      const fromSeq = Number.isFinite(state.seq) ? state.seq + 1 : 0
      const memory = memoryEventsOf(session, fromSeq)
      const events = memory.events
      const compactions = compactionsOf(events)
      debug('mem events=' + (Array.isArray(events) ? events.length : 0)
        + ' compactions=' + compactions.length)
      if (events === null) debug('mem source=none（snapshotEvents/ownEvents 都不可用）')

      const fresh = []
      for (const item of compactions) {
        const payload = compactionPayload(item, sessionId)
        const key = String(payload.compaction_id || '')
        if (!key || state.ids.has(key)) continue
        state.ids.add(key)
        trimSet(state.ids, ID_CAP)
        fresh.push(payload)
      }
      const lastSeq = lastSeqOf(events)
      if (lastSeq !== null) state.seq = lastSeq
      if (fresh.length) {
        chain = chain.then(async () => {
          for (const payload of fresh) {
            const result = await record(storeDir, sessionId, payload)
            debug('mem record ' + sessionId + ' ' + payload.compaction_id
              + ' recorded=' + String(result && result.recorded))
          }
          return null
        }).catch((error) => {
          debug('mem record error ' + sessionId + ' ' + String((error && error.message) || error))
          return null
        })
      }

      // ---- 二级：只有一级够不着、或者够着却没有 compaction 才回头看日志
      const needLog = events === null || compactions.length === 0
      if (needLog) {
        const stats = state.log ? stat(state.log) : null
        if (shouldScan(state, now(), stats)) {
          // 立刻记时刻与扫描前的日志签名：失败也只等满 60 秒，不会每回合重试
          state.at = Number(now())
          state.mtimeMs = stats ? stats.mtimeMs : undefined
          state.size = stats ? stats.size : undefined
          chain = chain.then(async () => {
            const result = await scan(storeDir, sessionId)
            if (result && result.ok === true) {
              state.log = String(result.log || '')
              const current = state.log ? stat(state.log) : null
              if (current) {
                state.mtimeMs = current.mtimeMs
                state.size = current.size
              }
            }
            debug('log scanned ' + sessionId + ' inserted=' + String(result && result.inserted)
              + ' reason=' + String((result && result.reason) || ''))
            return result
          }).catch((error) => {
            debug('log scan error ' + sessionId + ' ' + String((error && error.message) || error))
            return null
          })
        } else {
          debug('log skipped ' + sessionId + ' (throttled)')
        }
      }
      return true
    } catch { return false }
  }

  let dispose
  try {
    dispose = ctx.on('session/event', trigger)
  } catch { return null }
  return {
    trigger,
    states,
    dispose: () => { try { if (typeof dispose === 'function') dispose() } catch { /* ignore */ } },
    drain: () => chain,
  }
}
