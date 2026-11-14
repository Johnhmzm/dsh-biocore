/** 压缩记录补扫：不靠事件投递，靠会话日志。
 *
 * 背景：session/event 上的 compaction/summary 在 web/desktop 拓扑下会丢
 * （同一次会话压了 3 次，消息进库、压缩记录不进），但日志是全的。
 * 所以每个回合开头（turn/start 或 user/message，见下）顺手问一句 python：
 * 把这个会话的日志补扫一遍。
 *
 * 三条纪律：
 *   1) fire-and-forget：trigger 立刻返回，回合永远不等它；
 *   2) 每会话 60 秒一次，且日志没变就跳过（stat 得到才比，stat 不到就当变了）；
 *   3) 失败静默：补扫是尽力而为，出错只在 BIOCORE_DEBUG_CAPTURE 打开时打一行。
 */
import { statSync } from 'node:fs'
import { compactionScan } from '../py-bridge.js'
import { sessionIdOf } from './memory-capture.js'

export const SCAN_INTERVAL_MS = 60000
/** 触发的会话事件：一个回合一开头扫一次，扫到的是上一个回合写完的压缩。
 *
 * 两个都收，因为不确定哪个能到：turn/start 是语义上正确的那个（日志里确实有
 * {"type":"turn/start","seq":5,"data":{"turn":1}}），但 web/desktop 拓扑下
 * 它是否经 session/event 广播到插件，没重启实例就验不了；user/message 是确定
 * 能到的（capture 一直靠它）。两者落在同一个回合的开头，60 秒节流把第二次挡住，
 * 所以不会双扫——顺序反过来也一样。
 */
export const SCAN_TRIGGER_TYPES = Object.freeze(['turn/start', 'user/message'])
const STATE_CAP = 200

/** stat 一份日志（拿不到返回 null：拿不到就不拿它做跳过依据）。 */
export function statOf(path) {
  try {
    if (!path) return null
    const stats = statSync(String(path))
    return { mtimeMs: stats.mtimeMs, size: stats.size }
  } catch { return null }
}

/** 该不该扫：没有记录 -> 扫；60 秒内 -> 不扫；日志没变 -> 不扫。 */
export function shouldScan(state, now, stats) {
  if (!state || !Number.isFinite(Number(state.at))) return true
  if (Number(now) - Number(state.at) < SCAN_INTERVAL_MS) return false
  if (stats && Number.isFinite(stats.mtimeMs)
    && state.mtimeMs === stats.mtimeMs && state.size === stats.size) return false
  return true
}

function trimStates(states) {
  while (states.size > STATE_CAP) states.delete(states.keys().next().value)
}

/** Subscribe to session/event and rescan one session's log on turn/start. */
export function registerCompactionScan(ctx, getConfig, storeDir, options = {}) {
  if (!ctx || typeof ctx.on !== 'function') return null
  const opts = options || {}
  const cfg = () => {
    try { return (typeof getConfig === 'function' ? getConfig() : getConfig) || {} } catch { return {} }
  }
  const now = typeof opts.now === 'function' ? opts.now : () => Date.now()
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

  const trigger = (session, event) => {
    try {
      const type = event && event.type
      if (typeof type !== 'string' || !triggers.includes(type)) return false
      if (cfg().compactionScan === false) return false
      const sessionId = sessionIdOf(session)
        || (event && event.data && event.data.sessionId) || ''
      if (!sessionId) return false
      const state = states.get(sessionId) || null
      const stats = state && state.log ? stat(state.log) : null
      if (!shouldScan(state, now(), stats)) {
        debug('skip ' + sessionId + ' (throttled)')
        return false
      }
      // 立刻记下时刻：扫描失败也会等满 60 秒再来，不会每回合重试一次。
      // 日志的 mtime/size 先按扫描前那一刻记；扫完再用新的一份覆盖（见下）。
      const entry = {
        at: Number(now()),
        log: (state && state.log) || '',
        mtimeMs: stats ? stats.mtimeMs : undefined,
        size: stats ? stats.size : undefined,
      }
      states.set(sessionId, entry)
      trimStates(states)
      chain = chain.then(async () => {
        const result = await scan(storeDir, sessionId)
        if (result && result.ok === true) {
          entry.log = String(result.log || '')
          const fresh = entry.log ? stat(entry.log) : null
          if (fresh) {
            entry.mtimeMs = fresh.mtimeMs
            entry.size = fresh.size
          }
          debug('scanned ' + sessionId + ' inserted=' + result.inserted
            + ' skipped=' + result.skipped)
        } else {
          debug('scan skipped ' + sessionId + ' reason='
            + String((result && (result.reason || result.detail)) || 'unknown'))
        }
        return result
      }).catch((error) => {
        debug('scan error ' + sessionId + ' ' + String((error && error.message) || error))
        return null
      })
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
