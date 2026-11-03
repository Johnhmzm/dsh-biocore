/** dsh-biocore-ui host half.
 *
 * Registers one read-only endpoint:
 *   GET /api/biocore/state -> projection of the dsh_adapter state JSON
 *
 * No writes, no model calls, no network. Missing state is reported as
 * { available: false, reason: 'no_state_yet' } instead of throwing.
 */
import { DEFAULT_STATE_FILE, readStateResponse } from './modules/state.js'

export const STATE_ROUTE_PATH = '/api/biocore/state'
export const inject = ['webServer']

export function resolveStateFile(config) {
  const cfg = config && typeof config === 'object' ? config : {}
  return process.env.BIOCORE_STATE_FILE || cfg.stateFile || DEFAULT_STATE_FILE
}

function handleState(req, res, stateFile) {
  const send = (code, payload) => {
    res.writeHead(code, {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    })
    res.end(JSON.stringify(payload))
  }
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    return send(405, { available: false, reason: 'method_not_allowed' })
  }
  try {
    return send(200, readStateResponse(stateFile))
  } catch {
    return send(200, { available: false, reason: 'state_unreadable' })
  }
}

export function apply(ctx, config) {
  const stateFile = resolveStateFile(config)
  const disposers = []
  try {
    if (ctx && typeof ctx.inject === 'function') {
      ctx.inject(['webServer'], (webCtx) => {
        const webServer = webCtx && webCtx.webServer
        if (!webServer || typeof webServer.register !== 'function') return
        disposers.push(webServer.register({
          kind: 'exact',
          path: STATE_ROUTE_PATH,
          handler: (req, res) => handleState(req, res, stateFile),
        }))
      })
    }
  } catch {
    /* headless: the UI still loads without the route */
  }
  return () => {
    for (const dispose of disposers) {
      try { if (typeof dispose === 'function') dispose() } catch { /* ignore */ }
    }
  }
}

// Cordis resolves the default export before reading plugin.inject, so attach
// the dependency map to the exported function as well (same as the reference).
export default Object.assign(apply, { inject, resolveStateFile })
