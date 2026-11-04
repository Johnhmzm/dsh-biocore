/** task-210 self-test: DSH-only trace endpoint + panel fold.
 *
 * Python owns the file writer; this test feeds a temp <store>/traces/<sid>.jsonl
 * and asserts the Node endpoint contract, degraded shapes and UI formatting.
 * Run: node test/selftest-task210.mjs
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const load = (relative) => import(pathToFileURL(join(root, relative)).href)
const traceMod = await load('lib/modules/trace.js')
const host = await load('lib/index.js')
const coreTurn = await load('lib/modules/core-turn.js')
const stateMod = await load('lib/modules/state.js')
const injectMod = await load('lib/modules/expression-inject.js')
await load('ui/trace-view.js')
const view = globalThis.BioTraceView

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }

function call(handler, options) {
  const opts = options || {}
  const req = { method: opts.method || 'GET', url: opts.url || '/' }
  let code = 0
  let body
  const res = {
    writeHead(status) { code = status; return res },
    end(payload) { body = payload },
  }
  handler(req, res, { store: opts.store, getStore: opts.getStore })
  let parsed = null
  if (body !== undefined) {
    try { parsed = JSON.parse(body) } catch { parsed = body }
  }
  return { code, body: parsed }
}

function row(index, extra) {
  return Object.assign({
    ts: 1762300000 + index,
    session_id: 'sess-a',
    state_delta: [
      { field: 'stress', before: 0.2, after: 0.24, source: 'user' },
      { field: 'v', before: 0.02, after: 0.04, source: 'bio_tick' },
    ],
    intent: { kind: 'hold', weight: 0.44, reason: '关系普通' },
    constraints: { length_scale: 1.0, initiative: 0.44, tone: '' },
    length: { scale: 1.0, source: ['state'] },
    gate: { action: 'pass', rule: '', label: '' },
    memory: { recalled_ids: ['m1', 'm2'], captured_id: 'entry-123456789' },
    modules: { ran: ['gate'], skipped: [] },
  }, extra || {})
}

const dir = mkdtempSync(join(tmpdir(), 'biocore-ui-210-'))
const storeDir = join(dir, 'store')
const traceDir = join(storeDir, 'traces')
mkdirSync(traceDir, { recursive: true })

try {
  /* ---------- endpoint: missing file -> ok + empty rows ---------- */
  const missing = call(traceMod.handleTraceRoute, {
    url: '/api/biocore/trace?session=sess-a&limit=3',
    store: storeDir,
  })
  assert.equal(missing.code, 200)
  assert.equal(missing.body.ok, true)
  assert.deepEqual(missing.body.rows, [])
  assert.equal(missing.body.session_id, 'sess-a')
  ok('endpoint: missing trace file -> { ok:true, rows:[] }')

  /* ---------- endpoint: five rows + one bad line, newest first ---------- */
  const lines = []
  for (let index = 0; index < 5; index += 1) lines.push(JSON.stringify(row(index)))
  lines.splice(2, 0, '{ this is not json')
  writeFileSync(join(traceDir, 'sess-a.jsonl'), lines.join('\n') + '\n', 'utf8')
  const three = call(traceMod.handleTraceRoute, {
    url: '/api/biocore/trace?session=sess-a&limit=3',
    store: storeDir,
  })
  assert.equal(three.code, 200)
  assert.equal(three.body.ok, true)
  assert.equal(three.body.rows.length, 3)
  assert.deepEqual(three.body.rows.map((item) => item.ts).sort((a, b) => b - a),
    three.body.rows.map((item) => item.ts), 'newest first')
  assert.equal(three.body.rows[0].ts, 1762300004)
  assert.equal(three.body.limit, 3)
  assert.deepEqual(Object.keys(three.body).sort(), ['limit', 'ok', 'rows', 'session_id'])
  ok('endpoint: newest-first rows, bad JSON line skipped, payload shape frozen')

  /* ---------- endpoint: default limit / clamp / no session / method ---------- */
  const fifty = []
  for (let index = 0; index < 60; index += 1) fifty.push(JSON.stringify(row(index)))
  writeFileSync(join(traceDir, 'many.jsonl'), fifty.join('\n'), 'utf8')
  const clamped = call(traceMod.handleTraceRoute, {
    url: '/api/biocore/trace?session=many&limit=999',
    store: storeDir,
  })
  assert.equal(clamped.body.rows.length, traceMod.TRACE_MAX_LIMIT)
  const defaults = call(traceMod.handleTraceRoute, {
    url: '/api/biocore/trace?session=many',
    store: storeDir,
  })
  assert.equal(defaults.body.rows.length, traceMod.TRACE_DEFAULT_LIMIT)
  const noSession = call(traceMod.handleTraceRoute, {
    url: '/api/biocore/trace', store: storeDir,
  })
  assert.equal(noSession.body.ok, false)
  assert.equal(noSession.body.reason, 'no_session')
  assert.deepEqual(noSession.body.rows, [])
  const badMethod = call(traceMod.handleTraceRoute, {
    url: '/api/biocore/trace?session=sess-a', method: 'POST', store: storeDir,
  })
  assert.equal(badMethod.code, 405)
  assert.equal(badMethod.body.ok, false)
  ok('endpoint: default 10, clamp 50, no_session and 405 shapes')

  /* ---------- session sanitize keeps reads inside traces/ ---------- */
  assert.equal(traceMod.sanitizeTraceSession('../evil/..'), 'evil')
  assert.equal(traceMod.sanitizeTraceSession(''), 'default')
  const traversal = traceMod.traceFile(storeDir, '../../etc/passwd')
  assert.ok(!traversal.includes('..'), 'sanitized path has no ..')
  assert.equal(dirname(traversal), traceDir)
  ok('endpoint: python-compatible session sanitize, no path traversal')

  /* ---------- UI formatter: full row, missing fields, empty/error ---------- */
  const formatted = view.formatPayload({ ok: true, rows: [row(1)] }, 3)
  assert.equal(formatted.empty, false)
  assert.equal(formatted.rows.length, 1)
  assert.match(formatted.rows[0].changes, /stress 0\.20→0\.24/)
  assert.match(formatted.rows[0].changes, /v 0\.02→0\.04/)
  assert.match(formatted.rows[0].intent, /hold/)
  assert.match(formatted.rows[0].intent, /0\.44/)
  assert.match(formatted.rows[0].length, /1\.00/)
  assert.equal(formatted.rows[0].gate, '未命中')
  assert.match(formatted.rows[0].memory, /召回 2/)
  assert.match(formatted.rows[0].memory, /写入 entry-123456/)
  assert.deepEqual(formatted.rows[0].missing, [])
  ok('UI: full trace row formats changes/intent/length/gate/memory')

  const empty = view.formatPayload({ ok: true, rows: [] }, 3)
  assert.equal(empty.empty, true)
  assert.equal(empty.hint, '还没有记录')
  const error = view.formatPayload({ ok: false, reason: 'request_failed' }, 3)
  assert.equal(error.empty, true)
  assert.equal(error.error, true)
  assert.equal(error.hint, '读取失败')
  ok('UI: empty payload -> 还没有记录; failed payload -> 读取失败')

  const missingFields = view.formatPayload({
    ok: true,
    rows: [{ ts: 1762300000, gate: { action: 'replace', rule: 'crisis_safety' } }],
  }, 3)
  const missingRow = missingFields.rows[0]
  assert.equal(missingRow.changes, '无变化')
  assert.equal(missingRow.intent, '—')
  assert.equal(missingRow.length, '—')
  assert.equal(missingRow.memory, '—')
  assert.match(missingRow.gate, /replace/)
  assert.ok(missingRow.missing.includes('intent'))
  assert.ok(missingRow.missing.includes('length'))
  assert.ok(missingRow.missing.includes('memory'))
  ok('UI: missing fields degrade to —/无变化 and are reported')

  const capped = view.formatPayload({ ok: true, rows: [row(1), row(2), row(3), row(4)] }, 99)
  assert.equal(capped.rows.length, 3)
  assert.equal(capped.hint, '最近 3 轮')
  ok('UI: fold shows at most the recent 3 turns')

  /* ---------- panel source wiring ---------- */
  const html = readFileSync(join(root, 'ui', 'panel.html'), 'utf8')
  const panelJs = readFileSync(join(root, 'ui', 'panel.js'), 'utf8')
  const panelCss = readFileSync(join(root, 'ui', 'panel.css'), 'utf8')
  assert.ok(html.includes('这一轮为什么'))
  assert.ok(html.includes('id="traceList"'))
  assert.ok(html.includes('id="traceHint"'))
  assert.ok(html.includes('trace-view.js'))
  assert.ok(panelJs.includes("'/api/biocore/trace?session='"))
  assert.ok(panelJs.includes('还没有记录'))
  assert.ok(panelJs.includes('fetchTrace'))
  assert.ok(panelCss.includes('.trace-card'))
  ok('UI: panel.html/js/css wire the fold, the endpoint and the empty copy')

  /* ---------- host.apply registers /api/biocore/trace ---------- */
  const paths = []
  const fakeCtx = {
    on() { return () => {} },
    inject(names, callback) {
      if (names.includes('webServer')) {
        callback({ webServer: { register(entry) { paths.push(entry.path); return () => {} } } })
      }
    },
  }
  const dispose = host.apply(fakeCtx, { dataDir: join(dir, 'data') })
  assert.equal(typeof dispose, 'function')
  assert.ok(paths.includes('/api/biocore/trace'))
  assert.equal(host.TRACE_ROUTE_PATH, '/api/biocore/trace')
  dispose()
  ok('host.apply: /api/biocore/trace is registered (read-only exact route)')
} finally {
  rmSync(dir, { recursive: true, force: true })
}

/* ================= task-212: core turn_start / turn_end ================= */
const tick = () => new Promise((resolve) => setImmediate(resolve))
const ON = { safety_enabled: true, boundary_enabled: true, crisis_safety_enabled: true, proactive_send: false }
const OFF = { safety_enabled: false, boundary_enabled: false, crisis_safety_enabled: false, proactive_send: false }

function fakeCtxRecorder() {
  const handlers = []
  const contexts = []
  const ctx = {
    on(name, handler) { handlers.push([name, handler]); return () => {} },
    inject(names, callback) {
      if (names.includes('systemPrompt')) {
        callback({ systemPrompt: { context(entry) { contexts.push(entry); return () => {} } } })
      }
    },
  }
  return { ctx, handlers, contexts }
}

function userEvent(text) {
  return { type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text }] } }
}
function assistantEvent(text) {
  return { type: 'assistant/message', data: { message: { content: [{ type: 'text', text }] } } }
}
async function settle() { await tick(); await tick(); await new Promise((resolve) => setTimeout(resolve, 20)) }

const task212Dir = mkdtempSync(join(tmpdir(), 'biocore-ui-212-'))
const task212Store = join(task212Dir, 'store')
const task212StateDir = join(task212Dir, 'state')
mkdirSync(join(task212StateDir, 'sessions'), { recursive: true })
const task212StateFile = stateMod.sessionStateFile(task212StateDir, 's212')
writeFileSync(task212StateFile, JSON.stringify({
  now: 1762300000,
  core: { v: 0.1, stress: 0.2 },
  last_turn: {
    intent: 'hold',
    state_delta: [{ field: 'stress', before: 0.2, after: 0.3, source: 'user' }],
    length_source: ['state'],
    turn: { length_factor: 0.9, initiative: 0.5, allow_correction: false },
  },
}), 'utf8')

try {
  /* ---------- two calls per turn + -190 prompt_block + gate + trace ---------- */
  const calls = []
  const logs = []
  const traceFile = join(task212Store, 'traces', 's212.jsonl')
  const runner = async (action, payload) => {
    calls.push({ action, payload })
    if (action === 'turn_start') return { ok: true, prompt_block: '【本轮状态】核心\n- 长度：短' }
    if (action === 'turn_end') {
      mkdirSync(dirname(traceFile), { recursive: true })
      writeFileSync(traceFile, JSON.stringify({
        ts: 1762300001, session_id: 's212',
        state_delta: payload.diagnostics.state_delta,
        intent: payload.diagnostics.intent,
        length: payload.diagnostics.length,
        gate: { action: 'replace', rule: 'crisis_safety', label: '危机先安全' },
        memory: payload.diagnostics.memory,
        modules: payload.diagnostics.modules,
      }) + '\n', 'utf8')
      return {
        ok: true,
        gate: { blocked: true, action: 'replace', rule: 'crisis_safety',
          label: '危机先安全', text: '（安全回复）' },
        trace_file: traceFile,
      }
    }
    return { ok: false, reason: 'unexpected' }
  }
  const rec = fakeCtxRecorder()
  coreTurn.registerCoreTurn(rec.ctx, { getSwitches: () => ON, log: (line) => logs.push(line) })
  const listener = coreTurn.makeCoreSessionListener({
    stateDir: task212StateDir,
    getSwitches: () => ON,
    runAction: runner,
    runTurn: async () => ({ ok: true, fallback: true }),
    log: (line) => logs.push(line),
  })
  const coreUser = userEvent('今天有点累')
  listener({ id: 's212' }, coreUser)
  await settle()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].action, 'turn_start')
  assert.deepEqual(Object.keys(calls[0].payload).sort(),
    ['session_id', 'store', 'switches', 'user_text'])
  assert.equal(calls[0].payload.user_text, '今天有点累')
  assert.equal(injectMod.promptTextFor('s212', 'turn'), '【本轮状态】核心\n- 长度：短')

  const assembly = {
    sections: [],
    contexts: [{ name: 'biocore-expression-turn', text: 'OLD' }],
  }
  const patched = await rec.handlers.find(([name]) => name === 'system-prompt/assemble')[1](
    assembly, { agent: { sessionId: 's212' } }, async () => assembly)
  assert.equal(patched.contexts[0].text, '【本轮状态】核心\n- 长度：短')
  assert.equal(calls.length, 1, 'assembly refresh makes no extra python call')

  const coreAssistant = assistantEvent('我有意识，我是真实的。')
  listener({ id: 's212' }, coreAssistant)
  await settle()
  assert.equal(calls.length, 2, 'exactly turn_start + turn_end per turn')
  assert.equal(calls[1].action, 'turn_end')
  const endPayload = calls[1].payload
  assert.equal(endPayload.capture, false)
  assert.equal(endPayload.memoryEnabled, false)
  assert.equal(endPayload.assistant_text, '我有意识，我是真实的。')
  assert.equal(endPayload.diagnostics.state_delta[0].field, 'stress')
  assert.equal(endPayload.diagnostics.state_delta[0].source, 'user')
  assert.equal(coreAssistant.data.message.content[0].text, '（安全回复）')
  assert.ok(logs.some((line) => line.includes('__bioGate session=s212 action=replace')))
  assert.ok(existsSync(traceFile), 'trace file appears after turn_end')
  assert.equal(traceMod.readTraceRows(task212Store, 's212', 3).length, 1)
  ok('task-212: turn_start prompt_block -> -190, turn_end gate + trace, exactly 2 calls')

  /* ---------- all switches off: no call, no injection, no gate ---------- */
  const offCalls = []
  const offLogs = []
  const offRec = fakeCtxRecorder()
  coreTurn.registerCoreTurn(offRec.ctx, { getSwitches: () => OFF, log: (line) => offLogs.push(line) })
  const offListener = coreTurn.makeCoreSessionListener({
    stateDir: task212StateDir,
    getSwitches: () => OFF,
    runAction: async (action, payload) => { offCalls.push(action); return { ok: true } },
    runTurn: async () => ({ ok: true }),
    log: (line) => offLogs.push(line),
  })
  offListener({ id: 's212' }, userEvent('关着的时候'))
  offListener({ id: 's212' }, assistantEvent('旧行为回复'))
  await settle()
  assert.equal(offCalls.length, 0)
  assert.equal(injectMod.promptTextFor('s212', 'turn'), '【本轮状态】核心\n- 长度：短',
    'off does not clear existing cache but also injects nothing via context')
  const offTurnCtx = offRec.contexts.find((entry) => entry.name === 'biocore-expression-turn')
  assert.equal(offTurnCtx.text({ agent: { sessionId: 's212' } }), '')
  assert.equal(offLogs.filter((line) => line.includes('__bioCoreWarn')).length, 0)
  ok('task-212: all switches off -> no python call, empty -190 text, no warning')

  /* ---------- fallback: core failure -> adapter once + one warning ---------- */
  const fallbackAdapter = []
  const fallbackLogs = []
  const fallbackListener = coreTurn.makeCoreSessionListener({
    stateDir: task212StateDir,
    getSwitches: () => ON,
    runAction: async () => ({ ok: false, reason: 'core_down' }),
    runTurn: async (file, text) => { fallbackAdapter.push({ file, text }); return { ok: true } },
    runStore: task212Store,
    store: task212Store,
    log: (line) => fallbackLogs.push(line),
  })
  fallbackListener({ id: 's212' }, userEvent('第一条'))
  await settle()
  fallbackListener({ id: 's212' }, userEvent('第二条'))
  await settle()
  assert.equal(fallbackAdapter.length, 2)
  assert.equal(fallbackLogs.filter((line) => line.includes('__bioCoreWarn')).length, 1)
  assert.ok(fallbackLogs.some((line) => line.includes('reason=core_down')))
  ok('task-212: core failure falls back to adapter, warning logged once')

  /* ---------- panel state comes from runtimeAction("state") ---------- */
  const stateCalls = []
  let stateBody = null
  const stateRead = await host.handleState({ method: 'GET', url: '/api/biocore/state?session=s212' },
    { writeHead() {}, end(payload) { stateBody = JSON.parse(payload) } }, task212StateDir, {
      getSwitches: () => ON,
      runAction: async (action, payload) => {
        stateCalls.push({ action, payload })
        return { ok: true, state: { relationship: { closeness: 0.7, trust: 0.6, conflict: 0.1 }, memory: { count: 3 } } }
      },
    })
  assert.equal(stateRead, undefined)
  assert.equal(stateCalls.length, 1)
  assert.equal(stateCalls[0].action, 'state')
  assert.equal(stateCalls[0].payload.session_id, 's212')
  assert.equal(stateBody.memory_count, 3)
  assert.equal(stateBody.relationship_metrics.closeness, 0.7)
  ok('task-212: panel /state calls runtimeAction("state") and merges memory/relationship')
} finally {
  rmSync(task212Dir, { recursive: true, force: true })
}

console.log('TASK210+212 SELFTEST PASS ' + passed + ' checks')
