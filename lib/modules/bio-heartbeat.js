/** DSH 侧常驻生命循环（对应 AstrBot _life_loop）。
 *
 * 不是"每 60s 加一次状态"，而是 10s 唤醒一次、内部数 tick：
 *   - 每 6 次唤醒（≈60s）调 python action "body_loop"：advance + idle +
 *     过阈判定 + pending_intent 文本（纯本地，0 模型调用）；
 *   - 每 360 次唤醒（≈1h）触发一次记忆维护（钩子先留空，明写"未接"）。
 * 只对最近活跃会话跳；body 关闭（core_config.switches.body / config.bodyEnabled）
 * 时整块跳过；异常全部吞掉，不影响主流程。
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { bodyLoop } from '../py-bridge.js'
import { resolveMemoryDir } from './memory-store.js'
import { getActiveSession, recordActivity } from './proactive.js'

export const WAKE_MS = 10 * 1000
export const TICKS_PER_BEAT = 6
export const MEMORY_EVERY_WAKES = 360
export const HEARTBEAT_ACTION = 'body_loop'

/** body 开关：core_config.json 的 switches.body 优先，其次 config.bodyEnabled。 */
export function bodyEnabled(config, dataDir) {
  const cfg = config || {}
  // 环境变量优先：面板会重写配置文件，环境变量不会莫名其妙消失。
  const env = String(process.env.BIOCORE_BODY || '').toLowerCase()
  if (env === '1' || env === 'true' || env === 'on') return true
  if (cfg.bodyEnabled === true) return true
  if (cfg.switches && cfg.switches.body === true) return true
  const dir = String(dataDir || cfg.dataDir || '').trim()
  if (!dir) return false
  try {
    const doc = JSON.parse(readFileSync(join(dir, 'core_config.json'), 'utf8'))
    if (doc && doc.switches && typeof doc.switches.body === 'boolean') {
      return doc.switches.body === true
    }
  } catch { /* missing/corrupt config -> off */ }
  return false
}

/** 最近动过的会话（按 mindbase_clock.json 的修改时间挑）。
 *
 * 没有这个回退，心跳只在"刚有人说话"时才跳 —— 一安静下来身体就停摆，
 * 与"每时每刻都在动"直接冲突。
 */
export function lastTouchedSession(memoryDir) {
  const base = join(String(memoryDir || ''), 'sessions')
  try {
    let best = ''
    let bestAt = 0
    for (const name of readdirSync(base)) {
      const clock = join(base, name, 'mindbase_clock.json')
      try {
        const at = statSync(clock).mtimeMs
        if (at > bestAt) { bestAt = at; best = name }
      } catch { /* 该会话没有 clock，跳过 */ }
    }
    return best
  } catch { return '' }
}

/** One heartbeat: forward to python whenever body is on, even with nobody talking. */
export async function runBeat(ctx, options = {}) {
  const { getConfig, dataDir, now = Date.now(), log, deps = {} } = options
  const cfg = (typeof getConfig === 'function' ? (getConfig() || {}) : {}) || {}
  const dir = String(dataDir || cfg.dataDir || '').trim()
  if (!bodyEnabled(cfg, dir)) return { ok: true, skipped: true, reason: 'body_off' }
  const findLast = typeof deps.lastTouchedSession === 'function'
    ? deps.lastTouchedSession : lastTouchedSession
  const sessionId = getActiveSession() || findLast(resolveMemoryDir(cfg))
  if (!sessionId) return { ok: true, skipped: true, reason: 'no_session' }
  const tick = typeof deps.bodyLoop === 'function' ? deps.bodyLoop : bodyLoop
  try {
    const out = await tick({
      session_id: sessionId,
      store: resolveMemoryDir(cfg),
      data_dir: dir,
      now: Number(now) / 1000,
      seed: Number(cfg.heartbeatSeed || 1),
    })
    if (out && out.ok === false) {
      return { ok: false, reason: String(out.reason || 'bridge_unavailable') }
    }
    try {
      const state = (out && out.state) || {}
      log?.info?.('[biocore] heartbeat session=' + sessionId
        + ' ticks=' + Number(out && out.ticks || 0)
        + ' over=' + ((out && out.over_threshold === true) ? 'true' : 'false')
        + ' v=' + Number(state.valence || 0).toFixed(3)
        + ' stress=' + Number(state.stress || 0).toFixed(3)
        + (out && out.intent_text ? (' intent=' + String(out.intent_text)) : ''))
    } catch { /* logging never breaks the beat */ }
    return { ok: true, ...(out || {}) }
  } catch (error) {
    try { ctx?.logger?.warn?.('[biocore] heartbeat failed: ' + String(error)) } catch {}
    return { ok: false, reason: 'error' }
  }
}

/** Install the 10s wake loop + activity hook. Returns a disposer. */
export function registerBioHeartbeat(ctx, options = {}) {
  const { getConfig, dataDir, deps = {}, wakeMs = WAKE_MS } = options
  let off = null
  try {
    if (ctx && typeof ctx.on === 'function') {
      off = ctx.on('session/event', (session, event) => {
        try {
          if (!event || event.type !== 'user/message') return
          const source = (event.data && event.data.source) || {}
          if (source.kind !== 'user') return
          recordActivity(session && session.id, Date.now())
        } catch { /* contained */ }
      })
    }
  } catch { off = null }

  let wake = 0
  let running = false
  let timer = null
  const step = async () => {
    if (running) return
    running = true
    try {
      wake += 1
      if (wake % TICKS_PER_BEAT !== 0) return
      try {
        await runBeat(ctx, { getConfig, dataDir, now: Date.now(), log: ctx && ctx.logger, deps })
      } catch { /* a beat never disturbs a turn */ }
      if (wake % MEMORY_EVERY_WAKES === 0) {
        // 记忆维护钩子：DSH 侧尚未接（AstrBot 是 _memory_maintenance_pass）
        try { ctx?.logger?.info?.('[biocore] heartbeat memory maintenance hook: not wired') } catch {}
      }
    } finally {
      running = false
    }
  }
  timer = setInterval(() => { void step() }, Number(wakeMs) || WAKE_MS)
  if (typeof timer.unref === 'function') timer.unref()
  return () => {
    if (timer !== null) { clearInterval(timer); timer = null }
    try { if (typeof off === 'function') off() } catch { /* ignore */ }
  }
}

export const __test = { bodyEnabled, runBeat }
