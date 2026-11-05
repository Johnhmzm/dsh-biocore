/** task-215: adapter drains standby outbox into results only. */
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const mod = await import(pathToFileURL(join(root, 'lib/modules/standby.js')).href)
const dataDir = mkdtempSync(join(tmpdir(), 'biocore-standby-'))
let passed = 0
const ok = (name) => { passed += 1; console.log('ok - ' + name) }

try {
  mkdirSync(join(dataDir, 'proactive'), { recursive: true })
  writeFileSync(join(dataDir, 'proactive', 'outbox.jsonl'),
    JSON.stringify({ ts: 1, text: '在吗？', reason: 'ok' }) + '\n' +
    JSON.stringify({ ts: 2, text: '今天还好吗？', reason: 'template_fallback' }) + '\n', 'utf8')

  const first = mod.drainOutbox(dataDir)
  assert.equal(first.recorded, 2)
  const rows = readFileSync(join(dataDir, 'proactive', 'results.jsonl'), 'utf8')
    .trim().split('\n').map(JSON.parse)
  assert.equal(rows.length, 2)
  assert.equal(rows[0].delivered, false)
  assert.equal(rows[0].reason, 'outbox_only')
  ok('drain records one result per outbox row')

  const second = mod.drainOutbox(dataDir)
  assert.equal(second.recorded, 0)
  ok('second drain is idempotent')

  const summary = mod.standbySummary(dataDir)
  assert.equal(summary.pending, 0)
  assert.equal(summary.outbox, 2)
  assert.equal(summary.results, 2)
  ok('summary reports pending/outbox/results')
} finally {
  rmSync(dataDir, { recursive: true, force: true })
}

console.log('all ' + passed + ' standby-exec checks passed')
