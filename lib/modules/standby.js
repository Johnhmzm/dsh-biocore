/** task-215: adapter side of standby -- read outbox, record results only.
 *
 * The core writes <dataDir>/proactive/outbox.jsonl. This module never decides
 * whether to speak and never sends externally; it records one execution result
 * per outbox row into <dataDir>/proactive/results.jsonl.
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

export function proactiveDir(dataDir) {
  return join(String(dataDir || '').trim(), 'proactive')
}

export function outboxPath(dataDir) {
  return join(proactiveDir(dataDir), 'outbox.jsonl')
}

export function resultsPath(dataDir) {
  return join(proactiveDir(dataDir), 'results.jsonl')
}

function readLines(path) {
  try {
    if (!existsSync(path)) return []
    return readFileSync(path, 'utf8').split('\n').filter(Boolean)
  } catch {
    return []
  }
}

/** Record never-seen outbox rows; returns how many were recorded. */
export function drainOutbox(dataDir, options) {
  const opts = options || {}
  const rows = readLines(outboxPath(dataDir))
  const done = readLines(resultsPath(dataDir)).length
  if (rows.length <= done) return { ok: true, recorded: 0, pending: 0 }
  const path = resultsPath(dataDir)
  try {
    mkdirSync(dirname(path), { recursive: true })
    for (let index = done; index < rows.length; index += 1) {
      let row = {}
      try { row = JSON.parse(rows[index]) } catch { row = {} }
      appendFileSync(path, JSON.stringify({
        ts: row.ts || 0,
        index,
        delivered: false,
        reason: 'outbox_only',
        result: { ok: true, external: false },
      }) + '\n', 'utf8')
    }
    return { ok: true, recorded: rows.length - done, pending: 0 }
  } catch {
    return { ok: false, recorded: 0, pending: rows.length - done }
  }
}

export function standbySummary(dataDir) {
  const outbox = readLines(outboxPath(dataDir)).length
  const results = readLines(resultsPath(dataDir)).length
  return { pending: Math.max(0, outbox - results), outbox, results }
}
