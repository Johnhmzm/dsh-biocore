/**
 * Read the substrate body state written by python (mindbase_body.json).
 *
 * The body lives in python and persists next to the session; the host only
 * needs to hand it back as payload.body_state so the existing state renderer
 * can put one line in the prompt. No python round-trip for this read.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

export function readBodyState(store, sessionId) {
  try {
    const sid = String(sessionId || '').replace(/[^0-9A-Za-z_.-]/g, '_').slice(0, 60)
    if (!store || !sid) return null
    const raw = readFileSync(join(String(store), 'sessions', sid, 'mindbase_body.json'), 'utf8')
    const data = JSON.parse(raw)
    return data && typeof data === 'object' ? data : null
  } catch { return null }
}
