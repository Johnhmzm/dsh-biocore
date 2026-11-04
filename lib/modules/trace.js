/** Read-only "why this turn" trace endpoint (task-210).
 *
 * Python owns trace production and persistence:
 *   <store>/traces/<session_id>.jsonl   (newest last, <= 50 rows)
 * This module only reads that file for the DSH UI. Missing file / bad lines
 * degrade to an empty list; never writes and never calls the python pipeline.
 */
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { pluginDir } from './runtime-client.js'

export const TRACE_ROUTE_PATH = '/api/biocore/trace'
export const TRACE_DEFAULT_LIMIT = 10
export const TRACE_MAX_LIMIT = 50
export const TRACE_FILE_NAME = 'traces'
export const TRACE_EMPTY = Object.freeze({ ok: true, rows: [] })

const SESSION_MAX_LEN = 80
const UNSAFE = /[^A-Za-z0-9._-]+/g

/** Python pipeline.sanitize_session_id-compatible name (no path traversal). */
export function sanitizeTraceSession(sessionId) {
  const raw = String(sessionId == null ? '' : sessionId).trim().slice(0, SESSION_MAX_LEN)
  return raw.replace(UNSAFE, '_').replace(/^[._-]+|[._-]+$/g, '') || 'default'
}

/** Default pipeline store: env BIOCORE_MEMORY_STORE > <plugin>/data/memory. */
export function defaultStoreDir() {
  const env = String(process.env.BIOCORE_MEMORY_STORE || '').trim()
  if (env) return env
  return join(pluginDir(), 'data', 'memory')
}

export function traceFile(store, sessionId) {
  return join(String(store || '').trim() || defaultStoreDir(), TRACE_FILE_NAME,
    sanitizeTraceSession(sessionId) + '.jsonl')
}

function parseLimit(value, fallback) {
  if (value == null || value === '') return fallback
  const number = Number(value)
  if (!Number.isFinite(number)) return fallback
  return Math.max(1, Math.min(TRACE_MAX_LIMIT, Math.floor(number)))
}

/** Parse one JSONL trace file; invalid lines are skipped (never throws). */
export function parseTraceLines(text, limit) {
  const rows = []
  const max = parseLimit(limit, TRACE_DEFAULT_LIMIT)
  for (const line of String(text || '').split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try {
      const row = JSON.parse(trimmed)
      if (row && typeof row === 'object' && !Array.isArray(row)) rows.push(row)
    } catch { /* skip bad line */ }
  }
  return rows.slice(-max).reverse()
}

/** Newest-first rows for one session; missing/broken file -> []. */
export function readTraceRows(store, sessionId, limit) {
  try {
    const file = traceFile(store, sessionId)
    if (!existsSync(file)) return []
    return parseTraceLines(readFileSync(file, 'utf8'), limit)
  } catch { return [] }
}

function sendJson(res, code, payload) {
  try {
    res.writeHead(code, {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    })
    res.end(JSON.stringify(payload))
  } catch { /* client already gone */ }
  return payload
}

/** GET /api/biocore/trace?session=<id>&limit=10 -> { ok, rows } */
export function handleTraceRoute(req, res, options) {
  const opts = options && typeof options === 'object' ? options : {}
  const method = String((req && req.method) || 'GET').toUpperCase()
  if (method !== 'GET' && method !== 'HEAD') {
    return sendJson(res, 405, { ok: false, reason: 'method_not_allowed', rows: [] })
  }
  let url = null
  try { url = new URL(String(req && req.url || ''), 'http://local') } catch { url = null }
  const session = url ? String(url.searchParams.get('session') || '').trim() : ''
  if (!session) return sendJson(res, 200, { ok: false, reason: 'no_session', rows: [] })
  const store = typeof opts.getStore === 'function' ? opts.getStore() : (opts.store || defaultStoreDir())
  const limit = parseLimit(url ? url.searchParams.get('limit') : null, TRACE_DEFAULT_LIMIT)
  const rows = readTraceRows(store, session, limit)
  const payload = { ok: true, rows, session_id: sanitizeTraceSession(session), limit }
  return sendJson(res, 200, method === 'HEAD' ? undefined : payload)
}
