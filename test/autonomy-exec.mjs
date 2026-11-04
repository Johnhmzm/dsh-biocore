/** task-222: adapter executors for autonomous actions (no UI). */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const mod = await import(pathToFileURL(join(root, 'lib/modules/autonomy.js')).href)
const dataDir = mkdtempSync(join(tmpdir(), 'biocore-auto-'))
let passed = 0
const ok = (name) => { passed += 1; console.log('ok - ' + name) }

try {
  const calls = []
  const memoryTool = async (store, session, name, args) => {
    calls.push({ name, args })
    return name === 'biocore_recall'
      ? { ok: true, results: [{ id: 'a' }, { id: 'b' }] }
      : { ok: true, id: 'new' }
  }
  const write = await mod.executeAction(null,
    { kind: 'write', text: 'note', session_id: 's1' },
    { memoryTool, store: 'memory' })
  assert.equal(write.ok, true)
  assert.equal(calls[0].name, 'biocore_remember')
  ok('write action -> memoryTool remember')

  const retrieve = await mod.executeAction(null,
    { kind: 'retrieve', query: '最近', session_id: 's1' },
    { memoryTool, store: 'memory' })
  assert.equal(retrieve.ok, true)
  assert.equal(retrieve.result.count, 2)
  ok('retrieve action -> recall + result count')

  const send = await mod.executeAction(null,
    { kind: 'send', text: 'hi', session_id: 's1' }, {})
  assert.equal(send.ok, false)
  assert.equal(send.result.delivered, false)
  ok('send without host channel is recorded as not delivered')

  const tick = await mod.runAutonomyTick(null, {
    dataDir,
    store: 'memory',
    memoryTool,
    runAction: async () => ({ ok: true, reason: 'self_note',
      action: { kind: 'write', text: 'tick note', session_id: 's1' } }),
  })
  assert.equal(tick.ok, true)
  const lines = readFileSync(join(dataDir, 'actions.jsonl'), 'utf8')
    .trim().split('\n')
  assert.equal(lines.length, 1)
  const row = JSON.parse(lines[0])
  assert.equal(row.kind, 'write')
  assert.equal(row.reason, 'self_note')
  assert.equal(row.result.ok, true)
  ok('tick executes and appends {ts,kind,args,reason,result}')

  await mod.runAutonomyTick(null, {
    dataDir, store: 'memory',
    runAction: async () => { throw new Error('core down') },
  })
  const rows = readFileSync(join(dataDir, 'actions.jsonl'), 'utf8')
    .trim().split('\n').map(JSON.parse)
  assert.equal(rows[rows.length - 1].kind, 'none')
  ok('core failure is recorded, never thrown')
} finally {
  rmSync(dataDir, { recursive: true, force: true })
}

console.log('all ' + passed + ' autonomy-exec checks passed')
