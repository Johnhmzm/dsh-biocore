/** task-223: GET /api/biocore/series -- pure forward to the core.
 *
 * The core assembles the points (global history + growth history). This file
 * only forwards the request and returns the reply unchanged; when the core is
 * unreachable it answers an empty series so the panel never sees a 404.
 */
import { runtimeAction } from '../py-bridge.js'

export const SERIES_ROUTE_PATH = '/api/biocore/series'
export const SERIES_DEFAULT_LIMIT = 24
export const SERIES_MAX_LIMIT = 288

function sendJson(res, code, payload) {
  try {
    res.writeHead(code, {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    })
    res.end(JSON.stringify(payload))
  } catch { /* client gone */ }
  return payload
}

export async function handleSeriesRoute(req, res, options) {
  const opts = options || {}
  const method = String((req && req.method) || 'GET').toUpperCase()
  if (method !== 'GET' && method !== 'HEAD') {
    return sendJson(res, 405, { ok: false, reason: 'method_not_allowed' })
  }
  let limit = SERIES_DEFAULT_LIMIT
  try {
    const url = new URL(String(req.url || ''), 'http://local')
    const raw = Number(url.searchParams.get('limit'))
    if (Number.isFinite(raw)) {
      limit = Math.max(1, Math.min(SERIES_MAX_LIMIT, Math.round(raw)))
    }
  } catch { /* default limit */ }
  let reply = null
  try {
    const runAction = typeof opts.runAction === 'function' ? opts.runAction : runtimeAction
    reply = await runAction('series', {
      store: opts.store,
      data_dir: opts.dataDir,
      limit,
    })
  } catch { reply = null }
  if (reply && typeof reply === 'object') return sendJson(res, 200, reply)
  return sendJson(res, 200, {
    ok: true,
    series: { ts: [], v: [], tension: [], warmth: [], heaviness: [], clarity: [] },
    points: [],
    source: 'unavailable',
  })
}
