/** task-222: adapter side of autonomous actions -- execute + record only.
 *
 * The core (biocore_runtime.autonomy) decides WHAT and WHEN; this module just
 * performs the returned action and appends the result to <dataDir>/actions.jsonl.
 * No limit, drive or quiet-hour logic lives here.
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

export const ACTIONS_FILE = 'actions.jsonl'
export const ACTIONS_KEEP = 500

export function actionsPath(dataDir) {
  return join(String(dataDir || '').trim(), ACTIONS_FILE)
}

function appendAction(dataDir, row) {
  const path = actionsPath(dataDir)
  try {
    mkdirSync(dirname(path), { recursive: true })
    appendFileSync(path, JSON.stringify(row) + '\n', 'utf8')
    const lines = readFileSync(path, 'utf8').split('\n').filter(Boolean)
    if (lines.length > ACTIONS_KEEP) {
      writeFileSync(path, lines.slice(-ACTIONS_KEEP).join('\n') + '\n', 'utf8')
    }
    return true
  } catch {
    return false
  }
}

async function deliverSend(ctx, action, log) {
  try {
    const controller = ctx && ctx.sessionController
    if (!controller || typeof controller.resolveAgent !== 'function') {
      return { delivered: false, reason: 'no_session_controller' }
    }
    const resolved = await controller.resolveAgent(String(action.session_id || ''))
    const agent = resolved && resolved.agent
    if (!agent || typeof agent.followup !== 'function') {
      return { delivered: false, reason: 'no_agent' }
    }
    agent.followup({
      id: 'bio-auto-' + Date.now(),
      role: 'user',
      content: [{ type: 'text', text: String(action.text || '') }],
      source: { kind: 'biocore-autonomy' },
    })
    try { await ctx.sessions.flush(agent.session) } catch { /* best effort */ }
    return { delivered: true }
  } catch (error) {
    try { log?.warn?.('[biocore] autonomy send failed: ' + String(error)) } catch { /* ignore */ }
    return { delivered: false, reason: 'send_failed' }
  }
}

/** Execute one core action; returns {ok, kind, result}. Never throws. */
export async function executeAction(ctx, action, options) {
  const opts = options || {}
  const memory = typeof opts.memoryTool === 'function' ? opts.memoryTool : null
  const log = opts.log
  const kind = String((action && action.kind) || '')
  const sessionId = String((action && action.session_id) || 'autonomy')
  try {
    if (kind === 'send') {
      const result = await deliverSend(ctx, action, log)
      return { ok: result.delivered === true, kind, result }
    }
    if (kind === 'write' && memory) {
      const result = await memory(opts.store, sessionId, 'biocore_remember',
        { text: String(action.text || '') }, opts.rerankConfig)
      return { ok: Boolean(result && result.ok), kind, result: result || {} }
    }
    if (kind === 'retrieve' && memory) {
      const result = await memory(opts.store, sessionId, 'biocore_recall',
        { query: String(action.query || '最近'), limit: 3 }, opts.rerankConfig)
      const count = result && Array.isArray(result.results) ? result.results.length : 0
      return { ok: Boolean(result && result.ok), kind,
               result: { count } }
    }
    return { ok: false, kind, result: { reason: 'unsupported_action' } }
  } catch (error) {
    return { ok: false, kind, result: { reason: 'execution_failed',
      detail: String(error).slice(0, 120) } }
  }
}

/** One tick: ask the core, execute its single action, record the outcome. */
export async function runAutonomyTick(ctx, options) {
  const opts = options || {}
  const runAction = typeof opts.runAction === 'function' ? opts.runAction : null
  if (!runAction) return { ok: false, reason: 'no_core' }
  let decision = null
  try {
    decision = await runAction('autonomy_tick', {
      store: opts.store,
      data_dir: opts.dataDir,
      session_id: opts.sessionId || 'autonomy',
      last_human_ts: opts.lastHumanTs || 0,
    })
  } catch { decision = null }
  if (!decision || decision.ok !== true) {
    appendAction(opts.dataDir, { ts: Date.now(), kind: 'none',
      reason: 'core_unavailable', result: { ok: false } })
    return { ok: false, reason: 'core_unavailable' }
  }
  if (!decision.action) {
    const reason = String(decision.reason || 'idle')
    if (reason.indexOf('limit') >= 0 || reason.indexOf('decayed') >= 0
      || reason === 'disabled') {
      appendAction(opts.dataDir, { ts: Date.now(), kind: 'none',
        reason, result: { ok: true, stopped: true } })
    }
    return { ok: true, action: null, reason }
  }
  const executed = await executeAction(ctx, decision.action, opts)
  appendAction(opts.dataDir, {
    ts: Date.now(),
    kind: String(decision.action.kind || ''),
    args: decision.action,
    reason: String(decision.reason || ''),
    result: executed.result,
  })
  return { ok: true, action: decision.action, executed }
}
