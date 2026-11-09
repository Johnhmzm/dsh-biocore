// task-205 follow-up: Node proactive wiring tests.
// Decision policy is python (biocore_runtime/pipeline.py proactive_tick); here
// we verify Node only forwards the tick payload and executes should_send.
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  runOnce, deliver, registerProactive, recordActivity, getActiveSession,
  resetActivity, tickPayload, SOURCE_KIND
} from '../lib/modules/proactive.js'

const NOW = 1_800_000_000_000
const CFG = {
  proactiveEnabled: true,
  proactiveSend: true,
  proactiveIntervalMinutes: 30,
  proactiveMinGapMinutes: 180,
  proactivePerHour: 2,
  proactiveMinTrust: 0.6,
  proactiveMaxChars: 60,
  proactiveRequireOpenLoop: true,
  dataDir: '/tmp/biocore-proactive-test'
}

function fakeCtx(calls = {}) {
  calls.messages = []
  calls.flushed = 0
  calls.hooks = []
  const ctx = {
    logger: { info() {}, warn() {} },
    sessionController: {
      resolveAgent: async (sessionId) => ({
        agent: {
          session: { id: sessionId },
          followup(message) { calls.messages.push(message) }
        }
      })
    },
    sessions: { flush: async () => { calls.flushed += 1; return true } },
    on(name, handler) {
      calls.hooks.push({ name, handler })
      return () => { calls.hookDisposed = true }
    }
  }
  Object.defineProperty(ctx, 'messages', { get: () => calls.messages })
  Object.defineProperty(ctx, 'flushed', { get: () => calls.flushed })
  return ctx
}

test('activity registry tracks the newest human session', () => {
  resetActivity()
  assert.equal(getActiveSession(), '')
  recordActivity('s1', NOW - 1000)
  recordActivity('s2', NOW)
  assert.equal(getActiveSession(), 's2')
  recordActivity('s1', NOW + 5000)
  assert.equal(getActiveSession(), 's1')
  resetActivity()
})

test('no active session: no tick, no delivery', async () => {
  resetActivity()
  let called = 0
  const out = await runOnce(fakeCtx(), {
    getConfig: () => CFG,
    deps: { tick: async () => { called += 1; return { ok: true, should_send: true, text: 'x' } } }
  })
  assert.equal(out.reason, 'no_active_session')
  assert.equal(called, 0)
})

test('Node forwards the tick payload and obeys should_send=false', async () => {
  resetActivity()
  recordActivity('s1', NOW - 4 * 3600 * 1000)
  const ctx = fakeCtx()
  let payload = null
  const out = await runOnce(ctx, {
    getConfig: () => CFG,
    now: NOW,
    deps: {
      tick: async (value) => {
        payload = value
        return { ok: true, should_send: false, reason: 'too_soon', text: '', gap_minutes: 10 }
      }
    }
  })
  assert.equal(out.send, false)
  assert.equal(out.reason, 'too_soon')
  assert.equal(ctx.messages.length, 0)
  assert.equal(payload.session_id, 's1')
  assert.equal(payload.last_human_ts, NOW - 4 * 3600 * 1000)
  assert.equal(payload.now, NOW)
  assert.ok(payload.store.endsWith('memory') || payload.store.endsWith('memory/'))
  assert.equal(payload.switches.proactiveEnabled, true)
  assert.equal(payload.switches.proactiveSend, true)
  assert.equal(payload.switches.proactiveMinTrust, 0.6)
})

test('Node does not decide: switches are forwarded verbatim', () => {
  const payload = tickPayload({ ...CFG, proactiveSend: false }, 's9', 123, 456)
  assert.equal(payload.switches.proactiveSend, false)
  assert.equal(payload.last_human_ts, 123)
  assert.equal(payload.now, 456)
})

test('should_send=true delivers one identified user message', async () => {
  resetActivity()
  recordActivity('s1', NOW - 4 * 3600 * 1000)
  const ctx = fakeCtx()
  const out = await runOnce(ctx, {
    getConfig: () => CFG,
    now: NOW,
    deps: {
      tick: async () => ({
        ok: true, should_send: true, reason: 'ok', gap_minutes: 240,
        text: '你之前提过「体检」，后来怎么样了？'
      })
    }
  })
  assert.equal(out.send, true)
  assert.equal(out.reason, 'sent')
  assert.equal(ctx.messages.length, 1)
  const msg = ctx.messages[0]
  assert.equal(msg.role, 'user')
  assert.equal(typeof msg.id, 'string')
  assert.ok(msg.id.length > 0)
  assert.equal(msg.source.kind, SOURCE_KIND)
  assert.equal(msg.content[0].type, 'text')
  assert.match(msg.content[0].text, /体检/)
  assert.equal(ctx.flushed, 1)
  resetActivity()
})

test('tick failure or ok=false never delivers', async () => {
  resetActivity()
  recordActivity('s1', NOW - 4 * 3600 * 1000)
  const ctx = fakeCtx()
  const failed = await runOnce(ctx, {
    getConfig: () => CFG,
    deps: { tick: async () => ({ ok: false, reason: 'pipeline_failed' }) }
  })
  assert.equal(failed.reason, 'bridge_unavailable')
  const thrown = await runOnce(ctx, {
    getConfig: () => CFG,
    deps: { tick: async () => { throw new Error('spawn failed') } }
  })
  assert.equal(thrown.reason, 'bridge_unavailable')
  assert.equal(ctx.messages.length, 0)
  resetActivity()
})

test('deliver reports unavailable agents without throwing', async () => {
  const ctx = { sessions: { flush: async () => true }, logger: {} }
  ctx.sessionController = { resolveAgent: async () => ({ error: 'not_found' }) }
  const out = await deliver(ctx, { sessionId: 'gone', text: 'hi', now: 1 })
  assert.equal(out.send, false)
  assert.equal(out.reason, 'agent_unavailable')
})

test('disabled register installs no timer and returns a disposer', () => {
  const dispose = registerProactive({ logger: {} }, () => ({ proactiveEnabled: false }))
  assert.equal(typeof dispose, 'function')
  dispose()
})

test('enabled register installs the session hook; disposer clears it', () => {
  const calls = {}
  const ctx = fakeCtx(calls)
  const dispose = registerProactive(ctx, () => CFG)
  assert.equal(typeof dispose, 'function')
  assert.equal(calls.hooks.length, 1)
  assert.equal(calls.hooks[0].name, 'session/event')
  calls.hooks[0].handler({ id: 'live' }, {
    type: 'user/message', data: { source: { kind: 'user' }, content: [] }
  })
  assert.equal(getActiveSession(), 'live')
  dispose()
  assert.equal(calls.hookDisposed, true)
  resetActivity()
})

test('non-human session events do not mark activity', () => {
  resetActivity()
  const calls = {}
  const ctx = fakeCtx(calls)
  const dispose = registerProactive(ctx, () => CFG)
  calls.hooks[0].handler({ id: 'live' }, {
    type: 'user/message', data: { source: { kind: SOURCE_KIND } }
  })
  assert.equal(getActiveSession(), '')
  dispose()
  resetActivity()
})
