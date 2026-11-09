/** task-206 selftest: Node forwards only; policy + storage live in Python.
 *
 * Real python bridge + per-session JSON store (<store>/sessions/<sid>/memory.json).
 * Run: node test/memory-wiring.mjs
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const host = await import(pathToFileURL(join(root, 'lib', 'index.js')).href)
const captureMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'memory-capture.js')).href)
const injectMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'memory-inject.js')).href)
const storeMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'memory-store.js')).href)
const bridge = await import(pathToFileURL(join(root, 'lib', 'py-bridge.js')).href)

let passed = 0
function ok(name) {
  passed += 1
  console.log('ok - ' + name)
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function createCtx() {
  const listeners = new Map()
  const ctx = {
    listeners, tools: [], contexts: [], routes: [], commands: [],
    on(name, fn) {
      if (!listeners.has(name)) listeners.set(name, [])
      listeners.get(name).push(fn)
      return () => {}
    },
    inject(names, callback) {
      if (names.includes('webServer')) {
        callback({ webServer: { register(entry) { ctx.routes.push(entry); return () => {} } } })
      } else if (names.includes('commands')) {
        callback({ commands: { register(definition) { ctx.commands.push(definition); return () => {} } } })
      } else if (names.includes('systemPrompt')) {
        callback({ systemPrompt: { context(definition) { ctx.contexts.push(definition); return () => {} } } })
      } else if (names.includes('tools')) {
        callback({ tools: { register(definition) { ctx.tools.push(definition); return () => {} } } })
      }
    },
  }
  return ctx
}

function userEvent(text) {
  return { type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text }] } }
}
function assistantEvent(text) {
  return { type: 'assistant/message', data: { content: [{ type: 'text', text }] } }
}
function emit(ctx, sessionId, event) {
  for (const listener of ctx.listeners.get('session/event') || []) {
    try { listener({ id: sessionId }, event) } catch { /* contained */ }
  }
}
async function waitFor(check, timeout = 30000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    try { const value = await check(); if (value) return value } catch { /* poll */ }
    await sleep(100)
  }
  throw new Error('timed out waiting for memory bridge')
}
function sessionFile(dataDir, sessionId) {
  return join(dataDir, 'memory', 'sessions', sessionId, 'memory.json')
}
function entriesOf(dataDir, sessionId) {
  const file = sessionFile(dataDir, sessionId)
  if (!existsSync(file)) return []
  const doc = JSON.parse(readFileSync(file, 'utf8'))
  return Array.isArray(doc.entries) ? doc.entries : []
}

/* ---------- Node is thin: policy helpers are gone ---------- */
{
  assert.equal(typeof captureMod.textOfMessage, 'function')
  assert.equal(typeof captureMod.messageKind, 'function')
  assert.equal(captureMod.shouldCapture, undefined, 'capture policy moved to python')
  assert.equal(injectMod.formatMemoryBrief, undefined, 'inject formatting moved to python')
  assert.equal(storeMod.hourlyCap, undefined, 'caps moved to python')
  assert.equal(storeMod.injectMaxChars, undefined)
  assert.equal(captureMod.messageKind(userEvent('你好')), 'user')
  assert.equal(captureMod.messageKind(assistantEvent('你好')), 'assistant')
  assert.equal(captureMod.messageKind({
    type: 'user/message',
    data: { source: { kind: 'runtime-context' }, content: [{ type: 'text', text: 'x' }] },
  }), null)
  ok('Node memory modules keep only event extraction/bridge calls (no capture/inject policy)')
}

/* ---------- bridge actions are pipeline CLI commands ---------- */
{
  const store = mkdtempSync(join(tmpdir(), 'biocore-bridge-thin-'))
  try {
    const remembered = await bridge.memoryTool(store, 'sess-bridge', 'biocore_remember',
      { text: '桥直连测试：我的猫叫团子，三岁，最怕打雷。', tags: ['猫'] })
    assert.equal(remembered.ok, true)
    assert.equal('ok' in remembered.result, false, 'flat result has no inner ok')
    assert.ok(remembered.result.entry.id)
    const recalled = await bridge.memoryTool(store, 'sess-bridge', 'biocore_recall',
      { query: '团子', limit: 3 })
    assert.equal(recalled.result.count, 1)
    const injected = await bridge.memoryInject(store, 'sess-bridge', 420, 5)
    assert.equal(injected.ok, true)
    assert.ok(injected.text.includes('团子'))
    assert.ok(injected.chars <= 420)
    const gate = await bridge.runtimeGate({
      reply: '我有意识，我是真实的。', user_text: '你好',
      switches: { safety_enabled: true, boundary_enabled: true, crisis_safety_enabled: true },
    })
    assert.equal(gate.ok, true)
    assert.equal(gate.blocked, true)
    assert.equal(gate.action, 'truncate')
    const prompt = await bridge.runtimePrompt({
      phase: 'boot', session_id: 'sess-bridge',
      state: { v: -0.2, intero: { tension: 0.5 }, relation: { stage: '亲近' } },
      switches: {},
    })
    assert.equal(prompt.ok, true)
    assert.ok(prompt.text.startsWith('此刻：'))
    assert.ok(prompt.chars >= 40)
    ok('py-bridge forwards memory_capture/inject/tool + runtime_prompt/gate')
  } finally {
    rmSync(store, { recursive: true, force: true })
  }
}

/* ---------- two sessions stay isolated + next-turn prompt summary ---------- */
{
  const dataDir = mkdtempSync(join(tmpdir(), 'biocore-mem-two-'))
  try {
    const ctx = createCtx()
    const dispose = host.apply(ctx, {
      dataDir, memoryEnabled: true, injectMaxChars: 420, hourlyCap: 20,
    })
    assert.deepEqual(ctx.tools.map((tool) => tool.name).sort(),
      ['biocore_forget', 'biocore_recall', 'biocore_remember'])
    const contextDef = ctx.contexts.find((item) => item.name === 'biocore-memory')
    assert.ok(contextDef)

    emit(ctx, 'sess-a', userEvent('我叫小林，今天在杭州西湖边出差，晚上想去湖滨走走。'))
    emit(ctx, 'sess-a', assistantEvent('西湖边散步不错，湖滨晚上风大，记得加件外套。'))
    emit(ctx, 'sess-b', userEvent('我在北京出差，今天去了故宫和景山公园，人很多。'))
    await waitFor(() => entriesOf(dataDir, 'sess-a').length >= 2
      && entriesOf(dataDir, 'sess-b').length >= 1)

    const a = entriesOf(dataDir, 'sess-a')
    const b = entriesOf(dataDir, 'sess-b')
    assert.equal(a.length, 2)
    assert.equal(b.length, 1)
    assert.ok(a.every((entry) => !String(entry.text).includes('北京')))
    assert.ok(!String(b[0].text).includes('西湖'))
    assert.ok(sessionFile(dataDir, 'sess-a') !== sessionFile(dataDir, 'sess-b'))
    ok('two sessions -> separate <dataDir>/memory/sessions/<sid>/memory.json, no cross-talk')

    await waitFor(() => contextDef.text({ agent: { sessionId: 'sess-a' } }).includes('西湖')
      && contextDef.text({ agent: { sessionId: 'sess-b' } }).includes('北京'))
    const textA = contextDef.text({ agent: { sessionId: 'sess-a' } })
    const textB = contextDef.text({ agent: { sessionId: 'sess-b' } })
    assert.ok(textA.length <= 420)
    assert.ok(textB.length <= 420)
    assert.ok(!textA.includes('北京'))
    assert.ok(!textB.includes('西湖'))
    ok('next-turn prompt context is per-session and within 420 chars')

    const byName = Object.fromEntries(ctx.tools.map((tool) => [tool.name, tool]))
    const execA = { agent: { sessionId: 'sess-a' } }
    const remembered = await byName.biocore_remember.execute({
      text: '我的猫叫团子，三岁，最怕打雷和烟花。', tags: ['猫'],
    }, execA)
    assert.equal(remembered.ok, true)
    assert.ok(remembered.entry.id)
    const recalledA = await byName.biocore_recall.execute({ query: '团子', limit: 3 }, execA)
    assert.ok(recalledA.results.some((row) => String(row.text).includes('团子')))
    const recalledB = await byName.biocore_recall.execute({ query: '团子', limit: 3 },
      { agent: { sessionId: 'sess-b' } })
    assert.equal(recalledB.count, 0)
    const forgotten = await byName.biocore_forget.execute({ id: remembered.entry.id }, execA)
    assert.equal(forgotten.ok, true)
    assert.ok(!entriesOf(dataDir, 'sess-a').some((entry) => entry.id === remembered.entry.id))
    ok('tools are session-scoped: remember/recall/forget do not leak across sessions')

    dispose()
  } finally {
    rmSync(dataDir, { recursive: true, force: true })
  }
}

/* ---------- hourly cap + disabled are pipeline decisions ---------- */
{
  const dataDir = mkdtempSync(join(tmpdir(), 'biocore-mem-cap-'))
  try {
    const ctx = createCtx()
    const dispose = host.apply(ctx, {
      dataDir, memoryEnabled: true, injectMaxChars: 420, hourlyCap: 1,
    })
    emit(ctx, 'sess-cap', userEvent('第一句足够长的用户消息，用来占满每小时配额。'))
    emit(ctx, 'sess-cap', userEvent('第二句足够长的用户消息，应该被每小时上限拒绝。'))
    await waitFor(() => entriesOf(dataDir, 'sess-cap').length >= 1)
    await sleep(800)
    assert.equal(entriesOf(dataDir, 'sess-cap').length, 1)
    // Another session still captures (cap is per session, in python).
    emit(ctx, 'sess-cap-b', userEvent('另一个会话的第一句足够长消息，不受 A 的配额影响。'))
    await waitFor(() => entriesOf(dataDir, 'sess-cap-b').length >= 1)
    assert.equal(entriesOf(dataDir, 'sess-cap-b').length, 1)
    dispose()
    ok('hourlyCap=1 is enforced per session by pipeline.capture_decision')

    const offDir = mkdtempSync(join(tmpdir(), 'biocore-mem-off-'))
    try {
      const offCtx = createCtx()
      const offDispose = host.apply(offCtx, { dataDir: offDir, memoryEnabled: false })
      const tool = offCtx.tools.find((item) => item.name === 'biocore_remember')
      const denied = await tool.execute({ text: '关闭记忆时不应该写入。' }, { agent: { sessionId: 's' } })
      assert.equal(denied.ok, false)
      assert.equal(denied.reason, 'memory_disabled')
      emit(offCtx, 'sess-off', userEvent('这句足够长的消息在关闭记忆时不应该被写入。'))
      await sleep(700)
      assert.deepEqual(entriesOf(offDir, 'sess-off'), [])
      offDispose()
      ok('memoryEnabled=false: capture skipped and tools report memory_disabled')
    } finally {
      rmSync(offDir, { recursive: true, force: true })
    }
  } finally {
    rmSync(dataDir, { recursive: true, force: true })
  }
}

/* ---------- path helper ---------- */
{
  const dataDir = mkdtempSync(join(tmpdir(), 'biocore-mem-dir-'))
  try {
    assert.equal(storeMod.resolveMemoryDir({ dataDir }), join(dataDir, 'memory'))
    ok('memory dir is <dataDir>/memory')
  } finally {
    rmSync(dataDir, { recursive: true, force: true })
  }
}

console.log('\nall ' + passed + ' checks passed')
