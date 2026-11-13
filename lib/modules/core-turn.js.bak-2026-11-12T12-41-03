/** Core milestones for DSH (task-212): turn_start + turn_end + panel state.
 *
 * Per turn the plugin makes at most two python calls, both through
 * biocore_runtime.pipeline (via py-bridge runtimeAction):
 *
 *   user/message      -> turn_start {session_id,user_text,switches,store}
 *                        -> prompt_block used as the -190 prompt context
 *   assistant/message -> turn_end   {session_id,assistant_text,switches,store,
 *                                    capture:false,memoryEnabled:false,
 *                                    diagnostics:{...}}
 *                        -> gate.action executed, trace written by python
 *
 * Node no longer calls adapter.one_turn as the normal path. If turn_start is
 * unavailable it falls back to the old adapter bridge once and logs a single
 * warning; all switches off means no python call, no prompt text, no gate.
 */
import { readFileSync } from 'node:fs'
import { readBodyState } from './body-state.js'
import { join } from 'node:path'
import { readStateFile, sessionStateFile } from './state.js'
import { pendingSessionTurn, queueSessionTurn } from './turn-queue.js'
import {
  promptTextFor,
  rememberPromptText,
  sessionIdOf,
} from './expression-inject.js'
import { applyGateText, assistantTextOf } from './safety-gate.js'
import { defaultStoreDir } from './trace.js'
import { runtimeAction, runTurn } from '../py-bridge.js'
import { EXPRESSION_DEFAULTS, readExpressionSettings } from './expression-config.js'
import {
  readSanitizeReplySetting,
  sanitizeReply,
  sanitizeReplyEnabled,
} from './sanitize-reply.js'

export const TURN_CONTEXT_NAME = 'biocore-expression-turn'
export const TURN_CONTEXT_ORDER = 9500
export const CORE_WARN_PREFIX = '__bioCoreWarn'
export const CORE_GATE_PREFIX = '__bioGate'
export const CORE_TRACE_PREFIX = '__bioTrace'
export const CORE_INJECT_PREFIX = '__bioInject'

function stringId(value) {
  return value == null ? '' : String(value)
}

/** DSH parses prompt text as a template; strip unknown {{...}} variables. */
function sanitizeTemplateVars(value) {
  return String(value == null ? '' : value).replace(/\{\{\s*[^{}]*?\s*\}\}/g, '')
}

function logLine(logger, line) {
  try { logger(line) } catch { /* logging must not throw */ }
}

/** Pull the adapter/state diagnostics python turns into trace fields. */
export function diagnosticsFromState(state, userText) {
  const raw = state && typeof state === 'object' ? state : {}
  const last = raw.last_turn && typeof raw.last_turn === 'object' ? raw.last_turn : {}
  const turn = last.turn && typeof last.turn === 'object' ? last.turn : {}
  const labels = raw.core && raw.core.labels && typeof raw.core.labels === 'object'
    ? raw.core.labels : {}
  const deltas = Array.isArray(last.state_delta) ? last.state_delta.map((item) => {
    if (!item || typeof item !== 'object') return null
    return {
      field: stringId(item.field).slice(0, 40),
      before: Number(item.before),
      after: Number(item.after),
      source: stringId(item.source || 'bio_tick').slice(0, 16),
    }
  }).filter(Boolean) : []
  const sources = Array.isArray(last.length_source)
    ? last.length_source.map((item) => stringId(item)).filter(Boolean) : []
  const constraints = {
    length_scale: Number.isFinite(Number(turn.length_factor)) ? Number(turn.length_factor) : 1.0,
    initiative: Number.isFinite(Number(turn.initiative)) ? Number(turn.initiative) : 0.5,
    tone: '',
    allow_correction: turn.allow_correction === true,
    sources: sources.slice(0, 10),
  }
  return {
    state_delta: deltas,
    intent: {
      kind: stringId(last.intent || labels.intent || '').slice(0, 20),
      weight: Number.isFinite(Number(turn.initiative)) ? Number(turn.initiative) : 0,
      reason: stringId(last.metacog_band || sources.join(',') || userText || '').slice(0, 120),
    },
    constraints,
    length: {
      scale: Number.isFinite(Number(turn.length_factor)) ? Number(turn.length_factor) : 1.0,
      source: sources.join(',') || 'state',
    },
    memory: { recalled_ids: [] },
    modules: { ran: [], skipped: [] },
  }
}

/** Final assistant text: core clean_text wins, JS sanitizer is the fallback.
 * sanitizeEnabled=false keeps the original (user disabled the switch). */
export function chooseCleanReply(original, coreCleanText, options) {
  const opts = options || {}
  const source = String(original == null ? '' : original)
  if (opts.sanitizeEnabled === false) return source
  if (typeof coreCleanText === 'string') return coreCleanText
  const sanitize = typeof opts.sanitize === 'function' ? opts.sanitize : sanitizeReply
  return String(sanitize(source))
}

/** Delegation depth from the DSH session header; missing -> 0 (main). */
export function delegationDepthOf(session) {
  const header = session && session.header && typeof session.header === 'object'
    ? session.header : {}
  const fromHeader = Number(header.delegationDepth)
  if (Number.isFinite(fromHeader)) return Math.max(0, Math.trunc(fromHeader))
  const direct = Number(session && session.delegationDepth)
  if (Number.isFinite(direct)) return Math.max(0, Math.trunc(direct))
  return 0
}

export function isSubagentSession(session) {
  return delegationDepthOf(session) >= 1
}

export function personaApplied(dataDir) {
  try {
    const status = JSON.parse(readFileSync(join(String(dataDir || '').trim(), 'persona_preset.json'), 'utf8'))
    return status && status.applied === true
  } catch { return false }
}

export function turnStartPayload(options) {
  const opts = options || {}
  const expression = opts.expression && typeof opts.expression === 'object'
    ? opts.expression : EXPRESSION_DEFAULTS
  return {
    session_id: stringId(opts.sessionId),
    user_text: stringId(opts.userText).slice(0, 2000),
    switches: opts.switches || {},
    store: opts.store || defaultStoreDir(),
    capture: true,
    memoryEnabled: true,
    persona_prompt: opts.personaPrompt !== false,
    length_hint: expression.length_hint === true,
    length_target: Number.isFinite(Number(expression.length_target))
      ? Number(expression.length_target) : EXPRESSION_DEFAULTS.length_target,
    length_max: Number.isFinite(Number(expression.length_max))
      ? Number(expression.length_max) : EXPRESSION_DEFAULTS.length_max,
    body_state: readBodyState(opts.store || defaultStoreDir(), opts.sessionId),
  }
}

/** turn_end payload: capture disabled, all diagnostics included both flat and nested. */
export function turnEndPayload(options) {
  const opts = options || {}
  const diagnostics = diagnosticsFromState(opts.state, opts.userText)
  return Object.assign({
    session_id: stringId(opts.sessionId),
    assistant_text: stringId(opts.assistantText).slice(0, 4000),
    user_text: stringId(opts.userText).slice(0, 2000),
    switches: opts.switches || {},
    store: opts.store || defaultStoreDir(),
    capture: true,
    memoryEnabled: true,
    sanitize_reply: opts.sanitizeReply !== false,
    diagnostics,
  }, diagnostics, {
    modules_trace: diagnostics.modules,
    memory: diagnostics.memory,
  })
}

/** One session/event listener: turn_start on user, turn_end on assistant. */
export function makeCoreSessionListener(options) {
  const opts = options || {}
  const stateDir = opts.stateDir
  const getSwitches = typeof opts.getSwitches === 'function' ? opts.getSwitches : () => ({})
  const runAction = typeof opts.runAction === 'function' ? opts.runAction : runtimeAction
  const runAdapter = typeof opts.runTurn === 'function' ? opts.runTurn : runTurn
  const getExpression = typeof opts.getExpression === 'function'
    ? opts.getExpression
    : () => readExpressionSettings(stateDir)
  const getSanitizeReply = typeof opts.getSanitizeReply === 'function'
    ? opts.getSanitizeReply
    : () => readSanitizeReplySetting(stateDir)
  const store = opts.store || defaultStoreDir()
  const log = typeof opts.log === 'function' ? opts.log : (line) => {
    try { console.error(line) } catch { /* noop */ }
  }
  logLine(log, '__bioStore ' + store)
  const lastUser = new Map()
  let warned = false
  // Core failure -> adapter fallback; exactly one warning per plugin lifetime.
  const warnOnce = (reason) => {
    if (warned) return
    warned = true
    logLine(log, CORE_WARN_PREFIX + ' fallback=adapter reason=' + stringId(reason).slice(0, 160))
  }
  return function onCoreSessionEvent(session, event) {
    try {
      const type = event && event.type
      if (type !== 'user/message' && type !== 'assistant/message') return
      const sessionId = session && session.id != null ? stringId(session.id) : ''
      if (!sessionId) return
      const subagent = isSubagentSession(session)
      const switches = getSwitches()
      if (type === 'user/message') {
        const data = event.data || {}
        const source = data.source || {}
        if (source.kind !== 'user') return
        const text = textOfContent(data.content)
        if (!text) return
        lastUser.set(sessionId, text)
        if (lastUser.size > 200) lastUser.delete(lastUser.keys().next().value)
        queueSessionTurn(sessionId, async () => {
          let core = null
          try {
            core = await runAction('turn_start', turnStartPayload({
              sessionId, userText: text, switches, store, expression: getExpression(),
              personaPrompt: !personaApplied(stateDir),
            }))
          } catch (error) {
            core = { ok: false, reason: 'core_exception' }
          }
          if (core && core.ok === true && typeof core.prompt_block === 'string') {
            // Subagent sessions must not receive persona/state/thinking rules:
            // keep turn_start for state capture, but blank the -190 prompt.
            rememberPromptText(sessionId, 'turn', subagent ? '' : core.prompt_block)
            return core
          }
          if (subagent) {
            rememberPromptText(sessionId, 'turn', '')
            return null
          }
          warnOnce((core && (core.reason || core.detail)) || 'turn_start_failed')
          try {
            const stateFile = sessionStateFile(stateDir, sessionId)
            const profilePath = joinProfile(stateDir)
            return await runAdapter(stateFile, text, profilePath)
          } catch { return null }
        }).catch(() => null)
        return
      }
      // assistant/message: sanitize -> gate + trace via one turn_end call.
      const original = assistantTextOf(event)
      if (!original) return
      const sanitizeEnabled = sanitizeReplyEnabled(getSanitizeReply())
      const cleaned = sanitizeEnabled ? sanitizeReply(original) : original
      const userText = lastUser.get(sessionId) || ''
      void (async () => {
        try {
          await pendingSessionTurn(sessionId)
          const stateFile = sessionStateFile(stateDir, sessionId)
          const read = readStateFile(stateFile)
          const state = read && !read.error && !read.missing ? read.state : {}
          const payload = turnEndPayload({
            sessionId,
            assistantText: cleaned,
            userText,
            switches,
            store,
            state,
            sanitizeReply: sanitizeEnabled,
          })
          const result = await runAction('turn_end', payload)
          if (!result || result.ok !== true) return
          const reply = result.reply && typeof result.reply === 'object' ? result.reply : {}
          const coreCleanText = typeof reply.clean_text === 'string'
            ? reply.clean_text
            : (typeof result.clean_text === 'string' ? result.clean_text : null)
          const finalText = chooseCleanReply(original, coreCleanText, {
            sanitizeEnabled,
            sanitize: sanitizeReply,
          })
          const gate = result.gate && typeof result.gate === 'object' ? result.gate : {}
          const action = stringId(gate.action || 'pass')
          if ((action === 'replace' || action === 'truncate')
              && typeof gate.text === 'string' && gate.text) {
            applyGateText(event, gate.text)
            logLine(log, CORE_GATE_PREFIX + ' session=' + sessionId
              + ' action=' + action
              + ' blocked=' + (gate.blocked === true ? '1' : '0')
              + ' rule=' + stringId(gate.rule || '')
              + ' chars=' + original.length + '->' + gate.text.length)
          } else if (finalText !== original) {
            // No gate rewrite: write the core-cleaned / JS-fallback text back.
            applyGateText(event, finalText)
          }
          if (result.trace_file) {
            logLine(log, CORE_TRACE_PREFIX + ' session=' + sessionId
              + ' file=' + stringId(result.trace_file))
          }
        } catch { /* diagnostics never disturb the turn */ }
      })()
    } catch { /* contained */ }
  }
}

/** Register the turn context + assembly refresh (+ optional session listener). */
export function registerCoreTurn(ctx, options) {
  const opts = options || {}
  const getSwitches = typeof opts.getSwitches === 'function' ? opts.getSwitches : () => ({})
  const log = typeof opts.log === 'function' ? opts.log : (line) => {
    try { console.error(line) } catch { /* noop */ }
  }
  const disposers = []
  try {
    if (ctx && typeof ctx.inject === 'function') {
      ctx.inject(['systemPrompt'], (serviceCtx) => {
        const systemPrompt = serviceCtx && serviceCtx.systemPrompt
        if (!systemPrompt || typeof systemPrompt.context !== 'function') return
        try {
          const dispose = systemPrompt.context({
            name: TURN_CONTEXT_NAME,
            order: TURN_CONTEXT_ORDER,
            text: (context) => {
              try {
                const sessionId = sessionIdOf(context)
                return sessionId ? sanitizeTemplateVars(promptTextFor(sessionId, 'turn')) : ''
              } catch { return '' }
            },
          })
          if (typeof dispose === 'function') disposers.push(dispose)
        } catch { /* context rejected */ }
      })
    }
  } catch { /* prompt service unavailable */ }
  try {
    if (ctx && typeof ctx.on === 'function') {
      const dispose = ctx.on('system-prompt/assemble', async (assembly, context, next) => {
        let result = await next()
        if (!result || typeof result !== 'object') result = assembly
        try {
          const sessionId = sessionIdOf(context)
          if (!sessionId) return result
          // turn_start was queued by session/event; wait for it, then patch the
          // already-resolved -190 context (DSH resolves providers before this).
          await pendingSessionTurn(sessionId)
          const text = sanitizeTemplateVars(promptTextFor(sessionId, 'turn'))
          if (result && Array.isArray(result.contexts)) {
            result = Object.assign({}, result, {
              contexts: result.contexts.map((entry) => {
                if (!entry || typeof entry !== 'object') return entry
                if (entry.name !== TURN_CONTEXT_NAME) return entry
                return Object.assign({}, entry, { text })
              }),
            })
          }
          if (text) {
            logLine(log, CORE_INJECT_PREFIX + ' session=' + sessionId
              + ' chars=' + text.length + ' via=turn_start')
          }
        } catch { /* keep assembly */ }
        return result
      })
      if (typeof dispose === 'function') disposers.push(dispose)
    }
  } catch { /* assembly waterfall unavailable */ }
  return () => {
    for (const dispose of disposers) {
      try { if (typeof dispose === 'function') dispose() } catch { /* ignore */ }
    }
  }
}

function textOfContent(content) {
  try {
    const blocks = Array.isArray(content) ? content : []
    const parts = []
    for (const block of blocks) {
      if (block && block.type === 'text' && typeof block.text === 'string') {
        parts.push(block.text)
      }
    }
    return parts.join('\n').replace(/\s+/g, ' ').trim().slice(0, 2000)
  } catch { return '' }
}

function joinProfile(stateDir) {
  try {
    return stateDir ? String(stateDir).replace(/[\\/]+$/, '') + '/persona_profile.json' : ''
  } catch { return '' }
}