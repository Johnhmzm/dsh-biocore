/** task-223: persistent core worker self-test (real python processes). */
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const workerMod = await import(pathToFileURL(join(root, 'lib', 'core-worker.js')).href)
const bridge = await import(pathToFileURL(join(root, 'lib', 'py-bridge.js')).href)

let passed = 0
const ok = (name) => { passed += 1; console.log('ok - ' + name) }
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const dataDir = mkdtempSync(join(tmpdir(), 'biocore-worker-'))
const store = join(dataDir, 'memory')
const errors = []
const originalError = console.error
console.error = (...args) => { errors.push(args.join(' ')); originalError(...args) }

try {
  workerMod.startCoreWorker(dataDir)
  const firstPid = workerMod.workerPid()
  assert.ok(firstPid > 0)
  ok('start spawns one python service with a pid')

  const second = workerMod.startCoreWorker(dataDir)
  assert.equal(second.pid, firstPid)
  ok('second start is idempotent (same pid)')

  const ping = await workerMod.callCoreWorker('ping', { n: 1 }, { timeoutMs: 5000 })
  assert.equal(ping.pong, true)
  assert.equal(ping.pid, firstPid)
  ok('roundtrip ping returns the service pid')

  const stats = await workerMod.callCoreWorker('stats', { store }, { timeoutMs: 5000 })
  assert.equal(stats.ok, true)
  assert.ok(stats.embed && typeof stats.embed === 'object')
  ok('dispatch action (stats) roundtrip')

  const unknown = await workerMod.callCoreWorker('nope', {}, { timeoutMs: 5000 })
  assert.equal(unknown.ok, false)
  assert.equal(unknown.reason, 'unknown_action')
  ok('unknown action answers with ok:false without killing the loop')

  await assert.rejects(
    () => workerMod.callCoreWorker('sleep', { ms: 500 }, { timeoutMs: 60 }),
    (error) => error && error.reason === 'timeout',
  )
  ok('timeout rejects the pending promise')

  const big = 'x'.repeat(200 * 1024)
  const bigReply = await workerMod.callCoreWorker('ping', { big }, { timeoutMs: 8000 })
  assert.ok(bigReply.echo_len > 200000)
  ok('large payload (200KB) roundtrip')

  const replies = await Promise.all(Array.from({ length: 10 }, (_, index) =>
    workerMod.callCoreWorker('ping', { n: index }, { timeoutMs: 8000 })))
  assert.equal(replies.length, 10)
  assert.ok(replies.every((row) => row.pong === true && row.pid === firstPid))
  ok('10 concurrent requests correlate by id, no cross-talk')

  const beforeCrash = workerMod.workerPid()
  assert.equal(workerMod.killWorkerForTest('SIGKILL'), true)
  await sleep(1200)
  const recovered = await workerMod.callCoreWorker('ping', {}, { timeoutMs: 8000 })
  const afterCrash = workerMod.workerPid()
  assert.equal(recovered.pong, true)
  assert.ok(afterCrash && afterCrash !== beforeCrash)
  assert.ok(errors.some((line) => line.includes('__bioWorker')))
  ok('crash restarts automatically (new pid) and logs __bioWorker')

  workerMod.startCoreWorker(dataDir)
  const pidForStop = workerMod.workerPid()
  const pending = workerMod.callCoreWorker('sleep', { ms: 3000 }, { timeoutMs: 5000 })
  workerMod.stopCoreWorker()
  await assert.rejects(pending, (error) => error && error.reason === 'worker_stopped')
  await assert.rejects(
    () => workerMod.callCoreWorker('ping', {}, { timeoutMs: 1000 }),
    (error) => error && error.reason === 'worker_down',
  )
  await sleep(200)
  assert.equal(workerMod.isWorkerRunning(), false)
  ok('stop rejects in-flight work and later calls cannot reach the worker')

  bridge.configureBridge(dataDir)
  const fallback = await bridge.runtimeAction('stats', { store })
  assert.equal(fallback.ok, true)
  assert.ok(fallback.embed && typeof fallback.embed === 'object')
  ok('worker down -> py-bridge falls back to spawn-per-call')

  workerMod.startCoreWorker(dataDir)
  assert.ok(workerMod.isWorkerRunning())
  workerMod.stopCoreWorker()
  await sleep(150)
  assert.equal(workerMod.isWorkerRunning(), false)
  ok('dispose path (stop) leaves no running worker')
} finally {
  console.error = originalError
  try { workerMod.stopCoreWorker() } catch { /* ignore */ }
  rmSync(dataDir, { recursive: true, force: true })
}

console.log('all ' + passed + ' worker checks passed')
