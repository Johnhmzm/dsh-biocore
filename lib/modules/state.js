/** BioCore state projection for the DSH web host (pure, read-only).
 *
 * Reads the JSON produced by deepseek/dsh_adapter (save_state) and projects the
 * small subset the right-sidebar tab needs. No writes, no network, no LLM.
 */
import { readFileSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const STATE_LIMIT = 16 * 1024
export const DEFAULT_STATE_FILE = join(
  homedir(), 'Desktop', 'deepseek', 'dsh_adapter', 'data', 'demo_state.json')

const TONE_LABELS = {
  tension: '先接住情绪',
  heaviness: '慢一点说',
  warmth: '亲近一点',
  clarity: '没听清先确认',
}
const STAGES = ['陌生', '熟悉', '亲近', '稳定']

function num(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function clamp(value, low = 0, high = 1) {
  return Math.max(low, Math.min(high, num(value)))
}

function interoChannels(raw) {
  const source = raw && typeof raw === 'object' ? raw : {}
  const intero = source.intero && typeof source.intero === 'object' ? source.intero : source
  return {
    tension: clamp(intero.tension),
    warmth: clamp(intero.warmth),
    heaviness: clamp(intero.heaviness),
    clarity: clamp(intero.clarity),
  }
}

function hasIntero(raw) {
  const source = raw && typeof raw === 'object' ? raw : {}
  const intero = source.intero && typeof source.intero === 'object' ? source.intero : source
  return ['tension', 'warmth', 'heaviness', 'clarity'].some((key) => key in intero)
}

/** Mirror of biocore_model/intero_style.tone -> the five label slots. */
export function toneLabel(raw) {
  if (!hasIntero(raw)) return ''
  const felt = interoChannels(raw)
  if (felt.clarity < 0.275) return TONE_LABELS.clarity
  const ranked = Object.keys(felt).sort((a, b) => felt[b] - felt[a])
  const name = ranked[0]
  if (felt[name] < 0.55) return ''
  if (name === 'clarity') return ''
  return TONE_LABELS[name] || ''
}

function ratioPart(count, full) {
  const value = Math.max(0, num(count))
  return Math.min(1, Math.log1p(value) / Math.log1p(Math.max(1, num(full))))
}

/** Mirror of biocore_model/relation_stage.progress -> stage word. */
export function relationStage(state) {
  const core = state && typeof state === 'object' ? state : {}
  const rel = core.relationship && typeof core.relationship === 'object' ? core.relationship : {}
  const closeness = clamp(rel.closeness, 0, 1)
  const trust = clamp(rel.trust, 0, 1)
  const conflict = clamp(rel.conflict, 0, 1)
  const interactions = closeness * 8
  const disclosures = trust * 5
  const repairs = Math.max(0, 1 - conflict) * 1.2
  const age = Math.max(0, num(core.days_known, 30))
  const raw = 0.34 * ratioPart(interactions, 50)
    + 0.30 * ratioPart(disclosures, 12)
    + 0.14 * ratioPart(repairs, 3)
    + 0.12 * ratioPart(age, 180)
    + 0.10 * 1
    - 0.30 * conflict
  const progress = clamp(raw, 0, 1)
  if (progress < 0.30) return STAGES[0]
  if (progress < 0.60) return STAGES[1]
  if (progress < 0.85) return STAGES[2]
  return STAGES[3]
}

/** Mirror of the adapter autonomy projection -> one of 静/等/想联系/冷却. */
export function autonomyState(state, now) {
  const core = state && typeof state === 'object' ? state : {}
  const auto = core.autonomy && typeof core.autonomy === 'object' ? core.autonomy : {}
  const rel = core.relationship && typeof core.relationship === 'object' ? core.relationship : {}
  const coreState = core.core && typeof core.core === 'object' ? core.core : {}
  const at = num(now, num(core.now, 0))
  const cooldown = num(auto.cooldown_until, 0)
  if (cooldown > at) return '冷却'
  let ready = auto.ready
  if (!Number.isFinite(Number(ready))) {
    ready = clamp(
      0.30
      + 0.45 * (1 - num(coreState.social, 0.55))
      + 0.25 * num(coreState.stress, 0.2)
      - 0.20 * num(rel.closeness, 0.5)
      + 0.10 * Math.max(0, -num(coreState.v, 0)),
      0, 1)
  }
  ready = clamp(ready)
  if (ready < 0.40) return '静'
  if (ready < 0.60) return '等'
  return '想联系'
}

/** The documented subset returned by GET /api/biocore/state. */
export function projectState(state, meta) {
  const core = state && typeof state === 'object' ? state : {}
  const inner = core.core && typeof core.core === 'object' ? core.core : {}
  const intero = interoChannels(inner.intero)
  const memory = core.memory && typeof core.memory === 'object' ? core.memory : {}
  const updatedAt = Math.round(num(meta && meta.mtimeMs,
    Math.max(0, num(core.now, 0)) * 1000))
  return {
    available: true,
    v: Math.round(clamp(inner.v, -1, 1) * 100) / 100,
    intero,
    tone: { label: toneLabel(inner.intero) },
    relation: { stage: relationStage(core) },
    autonomy: { state: autonomyState(core) },
    memory_count: Math.max(0, Math.round(num(memory.count, 0))),
    updated_at: updatedAt,
  }
}

/** Read one state file and return the /state response body (never throws). */
export function readStateResponse(stateFile) {
  const file = String(stateFile || DEFAULT_STATE_FILE)
  let text
  try {
    text = readFileSync(file, 'utf8')
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return { available: false, reason: 'no_state_yet' }
    }
    return { available: false, reason: 'state_unreadable' }
  }
  let raw
  try {
    raw = JSON.parse(text)
  } catch {
    return { available: false, reason: 'bad_state' }
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { available: false, reason: 'bad_state' }
  }
  let mtimeMs = 0
  try { mtimeMs = statSync(file).mtimeMs } catch { mtimeMs = 0 }
  const body = projectState(raw, { mtimeMs })
  const size = Buffer.byteLength(JSON.stringify(body), 'utf8')
  if (size > STATE_LIMIT) {
    return { available: false, reason: 'payload_too_large', limit: STATE_LIMIT }
  }
  return body
}
