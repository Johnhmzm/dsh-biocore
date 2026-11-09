/** DSH proactive push — host wiring only (task-205 follow-up).
 *
 * The DECISION moved into the core pipeline: biocore_runtime/pipeline.py
 * action "proactive_tick" owns interval / hourly cap / cooldown / trust /
 * pending-commitment checks and the message text. This module only:
 *   1) tracks the newest human session from 'session/event',
 *   2) ticks on an unref'd interval,
 *   3) forwards {session_id, last_human_ts, switches, store, now} to the
 *      python bridge,
 *   4) when should_send=true, delivers the returned text through the host's
 *      sanctioned path: sessionController.resolveAgent -> agent.followup.
 * No local condition is evaluated here, and no state file is written here.
 */
import { randomUUID } from 'node:crypto'
import { resolveMemoryDir } from './memory-store.js'
import { proactiveTick } from '../py-bridge.js'

export const SOURCE_KIND = 'biocore-proactive'
const activity = new Map()
const MAX_TRACKED = 64

/** Record one human activity timestamp for a session (called by the hook). */
export function recordActivity(sessionId, now = Date.now()) {
  const id = String(sessionId || '').trim()
  if (!id) return false
  activity.set(id, Number(now) || Date.now())
  if (activity.size > MAX_TRACKED) {
    const oldest = [...activity.entries()].sort((a, b) => a[1] - b[1])[0]
    if (oldest) activity.delete(oldest[0])
  }
  return true
}

/** Newest active session id, or '' when nobody has spoken yet. */
export function getActiveSession() {
  let best = ''
  let at = 0
  for (const [id, ts] of activity) {
    if (ts > at) { at = ts; best = id }
  }
  return best
}

export function resetActivity() { activity.clear() }

/** Deliver one identified user message into the session and wake its agent. */
export async function deliver(ctx, { sessionId, text, now, log }) {
  try {
    const resolved = await ctx.sessionController.resolveAgent(sessionId)
    if (!resolved || resolved.error) {
      return { send: false, reason: 'agent_unavailable', detail: String(resolved && resolved.error) }
    }
    const agent = resolved.agent
    if (!agent || typeof agent.followup !== 'function') {
      return { send: false, reason: 'agent_unavailable', detail: 'followup missing' }
    }
    const message = {
      id: randomUUID(),
      role: 'user',
      content: [{ type: 'text', text }],
      source: { kind: SOURCE_KIND }
    }
    agent.followup(message)
    try { await ctx.sessions.flush(agent.session) } catch { /* best-effort */ }
    try { log?.info?.('[biocore] proactive sent session=' + sessionId + ' chars=' + text.length) } catch {}
    return { send: true, reason: 'sent', text, at: now }
  } catch (error) {
    return { send: false, reason: 'agent_unavailable', detail: String(error) }
  }
}

/** Switches forwarded verbatim; ALL proactive policy is evaluated in python. */
export function tickPayload(cfg, sessionId, lastHumanTs, now) {
  return {
    session_id: sessionId,
    last_human_ts: Number(lastHumanTs) || 0,
    now,
    store: resolveMemoryDir(cfg),
    switches: {
      proactiveEnabled: cfg.proactiveEnabled === true,
      proactiveSend: cfg.proactiveSend === true,
      proactiveIntervalMinutes: cfg.proactiveIntervalMinutes,
      proactiveMinGapMinutes: cfg.proactiveMinGapMinutes,
      proactivePerHour: cfg.proactivePerHour,
      proactiveMinTrust: cfg.proactiveMinTrust,
      proactiveMaxChars: cfg.proactiveMaxChars,
      proactiveRequireOpenLoop: cfg.proactiveRequireOpenLoop
    }
  }
}

/** One tick: ask the python pipeline, deliver only when it says should_send. */
export async function runOnce(ctx, { getConfig, now = Date.now(), log = ctx && ctx.logger, deps = {} } = {}) {
  const cfg = getConfig() || {}
  const sessionId = getActiveSession()
  if (!sessionId) return { send: false, reason: 'no_active_session' }
  const lastHumanTs = Number(activity.get(sessionId)) || 0
  const tick = typeof deps.tick === 'function' ? deps.tick : proactiveTick
  let decision
  try { decision = await tick(tickPayload(cfg, sessionId, lastHumanTs, now)) }
  catch { return { send: false, reason: 'bridge_unavailable' } }
  if (!decision || decision.ok !== true) {
    return { send: false, reason: 'bridge_unavailable', detail: String(decision && decision.reason) }
  }
  if (decision.should_send !== true) {
    return { send: false, reason: String(decision.reason || 'skip'), gap_minutes: decision.gap_minutes }
  }
  const delivered = await deliver(ctx, { sessionId, text: String(decision.text || ''), now, log })
  return { ...delivered, reason: delivered.send ? 'sent' : delivered.reason, gap_minutes: decision.gap_minutes }
}

/** Install the timer + activity hook only when config.proactiveEnabled is ON. */
export function registerProactive(ctx, getConfig) {
  const cfg = (typeof getConfig === 'function' ? getConfig() : {}) || {}
  if (cfg.proactiveEnabled !== true) return () => {}
  const rawInterval = Number(cfg.proactiveIntervalMinutes)
  const minutes = Math.max(5, Math.min(240, Number.isFinite(rawInterval) ? rawInterval : 30))
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

  let timer = null
  let running = false
  const tick = async () => {
    if (running) return
    running = true
    try { await runOnce(ctx, { getConfig, now: Date.now() }) }
    catch (error) { try { ctx.logger?.warn?.('[biocore] proactive tick failed: ' + String(error)) } catch {} }
    finally { running = false }
  }
  timer = setInterval(() => { void tick() }, minutes * 60 * 1000)
  if (typeof timer.unref === 'function') timer.unref()
  return () => {
    if (timer !== null) { clearInterval(timer); timer = null }
    try { if (typeof off === 'function') off() } catch { /* ignore */ }
  }
}
