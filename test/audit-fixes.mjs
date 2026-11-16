/** Regression tests for the 2026-11-16 audit fixes (docs/AUDIT-HOST.md).
 *
 * Each test pins one "silent failure" or "contract drift" that was found by
 * reading the code, so it cannot come back unnoticed. Tests never touch the
 * network, profiles/, or a running DSH instance.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { homedir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const load = (rel) => import(pathToFileURL(join(root, rel)).href)

// ---------------------------------------------------------------- trace store
test('trace: default store is the package data dir, not a hard-coded checkout', async () => {
  const mod = await load('lib/modules/trace.js')
  const dir = mod.defaultStoreDir()
  assert.equal(dir, mod.FALLBACK_STORE_DIR)
  assert.equal(resolve(dir), resolve(join(root, 'data', 'memory')))
  assert.ok(!dir.toLowerCase().includes('astrbot_plugin_biocore_standalone'))
  // BIOCORE_PLUGIN_DIR used to move it: it must not any more.
  const before = process.env.BIOCORE_PLUGIN_DIR
  process.env.BIOCORE_PLUGIN_DIR = join(root, 'python')
  try {
    assert.equal(mod.defaultStoreDir(), mod.FALLBACK_STORE_DIR)
  } finally {
    if (before === undefined) delete process.env.BIOCORE_PLUGIN_DIR
    else process.env.BIOCORE_PLUGIN_DIR = before
  }
})

test('trace: the route reads the store the host hands it (and degrades to ok:[] )', async () => {
  const mod = await load('lib/modules/trace.js')
  const { mkdtempSync, mkdirSync, writeFileSync, rmSync } = await import('node:fs')
  const tmp = mkdtempSync(join(root, 'data', 'trace-test-'))
  try {
    mkdirSync(join(tmp, 'traces'), { recursive: true })
    writeFileSync(join(tmp, 'traces', 's1.jsonl'),
      JSON.stringify({ ts: 1, intent: 'x' }) + '\n', 'utf8')
    const rows = mod.readTraceRows(tmp, 's1', 5)
    assert.equal(rows.length, 1)
    // missing dir -> empty list, not a throw
    assert.deepEqual(mod.readTraceRows(join(tmp, 'nope'), 's1', 5), [])
  } finally {
    rmSync(tmp, { recursive: true, force: true })
  }
})

// ------------------------------------------------------------- legacy wiring
test('expression-inject: legacy providers are opt-in, not silently doubled', async () => {
  const mod = await load('lib/modules/expression-inject.js')
  const registered = []
  const ctx = {
    inject(names, cb) {
      assert.deepEqual(names, ['systemPrompt'])
      cb({ systemPrompt: { context(def) { registered.push(def); return () => {} } } })
    },
  }
  const off = mod.registerExpressionInjections(ctx, { getSwitches: () => ({ core_enabled: true }) })
  assert.equal(registered.length, 0, 'default must not register the -190/-200 providers')
  off()
  const on = mod.registerExpressionInjections(ctx, { force: true, getSwitches: () => ({ core_enabled: true }) })
  assert.equal(registered.length, 2)
  assert.deepEqual(registered.map((d) => d.order), [mod.BOOT_ORDER, mod.TURN_ORDER])
  on()
})

test('safety-gate / expression-refresh: legacy listeners need an explicit opt-in', async () => {
  const gate = await load('lib/modules/safety-gate.js')
  const refresh = await load('lib/modules/expression-refresh.js')
  let gateHooks = 0
  let refreshHooks = 0
  gate.registerSafetyGate({ on() { gateHooks += 1; return () => {} } }, { runGate: async () => ({}) })
  refresh.registerContextRefresh({ on() { refreshHooks += 1; return () => {} } }, { callInject: async () => ({}) })
  assert.equal(gateHooks, 0)
  assert.equal(refreshHooks, 0)
  const forced = gate.registerSafetyGate({ on() { gateHooks += 1; return () => {} } },
    { force: true, runGate: async () => ({}) })
  assert.equal(gateHooks, 1)
  forced()
})

// ------------------------------------------------------------------ all-off gate
test('core-turn: switches off keep the resolve-time assembly untouched', async () => {
  const coreTurn = await load('lib/modules/core-turn.js')
  const inject = await load('lib/modules/expression-inject.js')
  inject.rememberPromptText('sess-1', 'turn', 'STALE BLOCK')
  let handler = null
  const ctx = {
    inject() {},
    on(name, fn) { if (name === 'system-prompt/assemble') handler = fn; return () => {} },
  }
  const dispose = coreTurn.registerCoreTurn(ctx, { getSwitches: () => ({ core_enabled: false }) })
  assert.equal(typeof handler, 'function')
  const assembly = { contexts: [{ name: coreTurn.TURN_CONTEXT_NAME, text: '' }] }
  const context = { agent: { sessionId: 'sess-1' } }
  const result = await handler(assembly, context, async () => assembly)
  assert.equal(result.contexts[0].text, '', 'cached text must not be patched back in when all switches are off')
  dispose()
  inject.clearPromptText('sess-1')
})

test('core-turn: switches on do patch the resolved context', async () => {
  const coreTurn = await load('lib/modules/core-turn.js')
  const inject = await load('lib/modules/expression-inject.js')
  inject.rememberPromptText('sess-2', 'turn', 'FRESH BLOCK')
  let handler = null
  const ctx = { inject() {}, on(name, fn) { if (name === 'system-prompt/assemble') handler = fn; return () => {} } }
  const dispose = coreTurn.registerCoreTurn(ctx, { getSwitches: () => ({ core_enabled: true }) })
  const assembly = { contexts: [{ name: coreTurn.TURN_CONTEXT_NAME, text: '' }] }
  const result = await handler(assembly, { agent: { sessionId: 'sess-2' } }, async () => assembly)
  assert.equal(result.contexts[0].text, 'FRESH BLOCK')
  dispose()
  inject.clearPromptText('sess-2')
})

// ------------------------------------------------------------------ worker lifecycle
test('core-worker: the host can replay configure after a crash restart', async () => {
  const worker = await load('lib/core-worker.js')
  assert.equal(typeof worker.onWorkerSpawn, 'function')
  const stats = worker.workerStats()
  assert.ok(stats && typeof stats === 'object')
  // The hook API is a registration, not a spawn: nothing runs here.
  const off = worker.onWorkerSpawn(() => {})
  off()
})

test('core-worker: a crash re-spawns and replays the host config', async () => {
  const worker = await load('lib/core-worker.js')
  const { mkdtempSync, rmSync } = await import('node:fs')
  const tmp = mkdtempSync(join(root, 'data', 'worker-test-'))
  const seen = []
  let fire = null
  const fired = new Promise((resolveHook) => { fire = resolveHook })
  const off = worker.onWorkerSpawn((dir) => { seen.push(dir); fire() })
  try {
    worker.startCoreWorker(tmp)
    assert.equal(worker.isWorkerRunning(), true)
    assert.equal(worker.killWorkerForTest('SIGKILL'), true)
    let timer = null
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('no re-spawn within 20s')), 20000)
      if (typeof timer.unref === 'function') timer.unref()
    })
    try {
      await Promise.race([fired, deadline])
    } finally {
      if (timer) clearTimeout(timer)
    }
    const stats = worker.workerStats()
    assert.ok(stats.spawns >= 2, 'expected a second spawn, got ' + stats.spawns)
    assert.equal(seen[0], tmp, 'the hook gets the data dir the worker was started with')
  } finally {
    off()
    worker.stopCoreWorker()
    rmSync(tmp, { recursive: true, force: true })
  }
})

// ------------------------------------------------------------------ client reasons
test('client: panel status speaks the server reasons, not adapter-era ones', async () => {
  let captured = null
  globalThis.window = { __ModuleLoader__: { load(spec) { captured = spec } } }
  await load('lib/client.js')
  assert.ok(captured && typeof captured.factory === 'function')
  const mod = captured.factory(() => ({ createElement: () => null }))
  const statusOf = mod.__test.statusOf
  const core = statusOf({ available: false, reason: 'core_unavailable' }, false, 's1')
  assert.equal(core.ok, false)
  assert.notEqual(core.label, '连不上宿主')
  assert.ok(core.suggestion.length > 0)
  const tooBig = statusOf({ available: false, reason: 'payload_too_large' }, false, 's1')
  assert.equal(tooBig.reason, 'payload_too_large')
  assert.notEqual(tooBig.label, '连不上宿主')
  const unknown = statusOf({ available: false, reason: 'weird_new_code' }, false, 's1')
  assert.ok(unknown.label.includes('weird_new_code'), 'unknown reasons are shown raw, not relabelled')
  const ok = statusOf({ available: true }, false, 's1')
  assert.equal(ok.ok, true)
})

// ------------------------------------------------------------------ python side
test('python: bridge defines _DSH_HOME and the pipeline compiles', async () => {
  const { readFileSync } = await import('node:fs')
  const { execFileSync } = await import('node:child_process')
  const bridge = readFileSync(join(root, 'lib', 'py', 'bridge.py'), 'utf8')
  assert.match(bridge, /^_DSH_HOME = Path\(/m, '_DSH_HOME must be defined, not just used')
  const pyDir = join(root, 'python')
  const files = ['biocore_runtime/pipeline.py', 'biocore_runtime/standby.py', 'biocore_runtime/needs4.py',
    'biocore_runtime/care_fatigue.py', 'biocore_runtime/attribution_style.py',
    'biocore_runtime/topic_closure.py']
  try {
    execFileSync('python', ['-m', 'py_compile', ...files], { cwd: pyDir, stdio: 'pipe' })
  } catch (error) {
    assert.fail('py_compile failed: ' + String(error && error.stderr || error).slice(0, 400))
  }
})
