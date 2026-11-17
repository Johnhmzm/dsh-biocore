/**
 * Read the substrate body state written by python (mindbase_body.json).
 *
 * The body lives in python and persists next to the session; the host only
 * needs to hand it back as payload.body_state so the existing state renderer
 * can put one line in the prompt. No python round-trip for this read.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

export function readBodyDoc(store, sessionId) {
  try {
    const sid = String(sessionId || '').replace(/[^0-9A-Za-z_.-]/g, '_').slice(0, 60)
    if (!store || !sid) return null
    const raw = readFileSync(join(String(store), 'sessions', sid, 'mindbase_body.json'), 'utf8')
    const data = JSON.parse(raw)
    return data && typeof data === 'object' ? data : null
  } catch { return null }
}

export function readBodyState(store, sessionId) {
  return readBodyDoc(store, sessionId)
}

/** 0.6 reverse injection: python feeds her own sentence back into the body and
 *  records push_source ("我上一句") plus push_dims {valence, stress}.
 *
 *  The core's /state projection keeps numbers only, so the string source and the
 *  dims object never reach the panel. Read the same body file once more here —
 *  display only, nothing is written — and hand the marker to the UI. */
export function readReversePush(store, sessionId) {
  const doc = readBodyDoc(store, sessionId)
  if (!doc) return null
  const source = typeof doc.push_source === 'string' ? doc.push_source.trim().slice(0, 20) : ''
  const dims = doc.push_dims && typeof doc.push_dims === 'object' ? doc.push_dims : null
  if (!source || !dims) return null
  const valence = Number(dims.valence)
  const stress = Number(dims.stress)
  if (!Number.isFinite(valence) && !Number.isFinite(stress)) return null
  return {
    source,
    valence: Number.isFinite(valence) ? Math.round(valence * 10000) / 10000 : null,
    stress: Number.isFinite(stress) ? Math.round(stress * 10000) / 10000 : null,
  }
}
