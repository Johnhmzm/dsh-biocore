/** Self-test for dsh-biocore-ui (no network, no browser).
 *
 * Covers: state projection shape, missing/bad state files, host route handler
 * wiring, and client module load + pure panel render.
 *
 * Run: node test/selftest.mjs
 */
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const stateMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'state.js')).href)
const hostMod = await import(pathToFileURL(join(root, 'lib', 'index.js')).href)

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }

/* ---------- projection ---------- */
const sample = {
  now: 1762300000,
  turn: 4,
  days_known: 60,
  core: {
    v: 0.42, social: 0.58, stress: 0.24,
    intero: { tension: 0.70, warmth: 0.20, heaviness: 0.50, clarity: 0.40 },
  },
  relationship: { closeness: 0.62, trust: 0.55, conflict: 0.05 },
  autonomy: { ready: 0.52 },
  memory: { count: 3 },
}
const body = stateMod.projectState(sample, { mtimeMs: 1762300123000 })
assert.equal(body.available, true)
assert.equal(body.v, 0.42)
assert.deepEqual(body.intero, { tension: 0.7, warmth: 0.2, heaviness: 0.5, clarity: 0.4 })
assert.equal(typeof body.tone.label, 'string')
assert.equal(body.tone.label, '先接住情绪')
assert.ok(['陌生', '熟悉', '亲近', '稳定'].includes(body.relation.stage))
assert.ok(['静', '等', '想联系', '冷却'].includes(body.autonomy.state))
assert.equal(body.memory_count, 3)
assert.equal(body.updated_at, 1762300123000)
for (const key of ['v', 'intero', 'tone', 'relation', 'autonomy', 'memory_count', 'updated_at']) {
  assert.ok(key in body, 'missing ' + key)
}
ok('projectState returns the documented subset')

const empty = stateMod.projectState({})
assert.equal(empty.available, true)
assert.equal(empty.v, 0)
assert.equal(empty.tone.label, '')
ok('projectState tolerates an empty state')

/* ---------- state file read ---------- */
const tmp = mkdtempSync(join(tmpdir(), 'biocore-ui-'))
try {
  const missing = stateMod.readStateResponse(join(tmp, 'nope.json'))
  assert.equal(missing.available, false)
  assert.equal(missing.reason, 'no_state_yet')
  ok('missing file -> available:false reason no_state_yet')

  const badFile = join(tmp, 'bad.json')
  writeFileSync(badFile, '{not json', 'utf8')
  assert.equal(stateMod.readStateResponse(badFile).reason, 'bad_state')
  ok('malformed file -> available:false reason bad_state')

  const goodFile = join(tmp, 'state.json')
  writeFileSync(goodFile, JSON.stringify(sample), 'utf8')
  const good = stateMod.readStateResponse(goodFile)
  assert.equal(good.available, true)
  assert.ok(Buffer.byteLength(JSON.stringify(good), 'utf8') <= stateMod.STATE_LIMIT)
  ok('state file projection stays under the 16KB limit')
} finally {
  rmSync(tmp, { recursive: true, force: true })
}

/* ---------- host route wiring ---------- */
const registered = []
const fakeCtx = {
  inject(names, callback) {
    assert.deepEqual(names, ['webServer'])
    callback({ webServer: { register(entry) { registered.push(entry); return () => {} } } })
  },
}
const dispose = hostMod.apply(fakeCtx, { stateFile: '' })
assert.equal(registered.length, 1)
assert.equal(registered[0].path, '/api/biocore/state')
assert.equal(registered[0].kind, 'exact')
assert.equal(typeof registered[0].handler, 'function')
assert.equal(typeof dispose, 'function')
ok('host apply registers GET /api/biocore/state')

const missingFile = hostMod.resolveStateFile({ stateFile: '' })
assert.ok(missingFile.endsWith('demo_state.json'))
ok('default state file points at dsh_adapter/data/demo_state.json')

/* ---------- client module load + pure render ---------- */
const fakeReact = {
  createElement(type, props, ...children) {
    return { type, props: Object.assign({}, props || {}, { children }) }
  },
  useState(initial) {
    return [typeof initial === 'function' ? initial() : initial, () => {}]
  },
  useEffect(run) { const cleanup = run(); return cleanup },
}
const tabs = []
const slots = []
const clientCtx = {
  effect(run) { return run() },
  sidebarRightTabs: { register(definition) { tabs.push(definition); return () => {} } },
  slots: {
    inject(name, callback) { callback() },
    register(definition) { slots.push(definition); return () => {} },
  },
}
globalThis.window = {
  __ModuleLoader__: {
    load(definition) {
      const module = definition.factory((id) => {
        if (id === 'react') return fakeReact
        throw new Error('unexpected require ' + id)
      })
      clientCtx.exports = module
    },
  },
}
await import(pathToFileURL(join(root, 'lib', 'client.js')).href + '?t=' + Date.now())
const client = clientCtx.exports
assert.equal(typeof client.apply, 'function')
assert.deepEqual(client.inject, ['slots', 'sidebarRightTabs'])
client.apply(clientCtx)
assert.equal(tabs.length, 1)
assert.equal(tabs[0].kind, 'biocore')
assert.equal(tabs[0].title(), '生物核')
assert.ok(tabs[0].guide.length >= 1)
assert.equal(slots.length, 2)
assert.deepEqual(slots.map((item) => item.name).sort(),
  ['sidebar.right.pane.tab', 'sidebar.right.pane.tab.title'])
ok('client registers the 生物核 right-sidebar tab and its slots')

const tree = client.__test.renderPanel(body, false)
const flat = JSON.stringify(tree)
for (const needle of ['生物核', '绷紧', '暖', '沉', '清', '语气', '关系', '自主', '已连接']) {
  assert.ok(flat.includes(needle), 'render missing ' + needle)
}
const disconnected = JSON.stringify(client.__test.renderPanel(null, true))
assert.ok(disconnected.includes('未连接'))
ok('panel renders 5 numbers + 3 words, and 未连接 on failure')

console.log('\nall ' + passed + ' checks passed')
