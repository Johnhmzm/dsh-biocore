/** dsh-biocore-ui host half: four toggle switches persisted to flags.json.
 *
 * Same approach as the other DSH plugins (see dsh-biocore-memory): the plugin
 * owns one small JSON file instead of writing the host settings document.
 *
 *   file: <dataDir>/flags.json   (atomic write: temp file + rename)
 *   GET  /api/biocore/switch -> { ok, switches, namespace, revision }
 *   POST /api/biocore/switch { key, value, confirm: true }
 *        -> { ok, key, value, switches, namespace, revision }
 *
 * Only these four keys are accepted:
 *   safety_enabled, boundary_enabled, crisis_safety_enabled, proactive_send
 *
 * GET never creates or writes the file: a missing (or unreadable) file simply
 * projects the default four false values. POST validates the whitelist and
 * confirm === true before touching the file. No host settings service is
 * referenced, so a host without a settings row still works.
 */
import { existsSync, mkdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { resolveDataDir } from './data-dir.js'

export { resolveDataDir }

export const SWITCH_ROUTE_PATH = '/api/biocore/switch'
export const FLAGS_FILE_NAME = 'flags.json'
export const BODY_LIMIT = 4096

export const SWITCH_KEYS = Object.freeze([
  'safety_enabled',
  'boundary_enabled',
  'crisis_safety_enabled',
  'proactive_send',
])

export const SWITCH_LABELS = Object.freeze({
  safety_enabled: '安全闸',
  boundary_enabled: '底线',
  crisis_safety_enabled: '危机兜底',
  proactive_send: '主动回复',
})

export const DEFAULT_SWITCHES = Object.freeze({
  safety_enabled: false,
  boundary_enabled: false,
  crisis_safety_enabled: false,
  proactive_send: false,
})

/** Read-side aliases for renamed switches (writes always use the canonical key). */
export const SWITCH_READ_ALIASES = Object.freeze({
  proactive_send: 'proactive_autonomy',
})

const KEY_SET = new Set(SWITCH_KEYS)
const STATUS_BY_REASON = Object.freeze({
  bad_payload: 400,
  unknown_key: 400,
  bad_value: 400,
  confirm_required: 400,
  method_not_allowed: 405,
  payload_too_large: 413,
  write_failed: 500,
})

function statusOf(reason) {
  return STATUS_BY_REASON[String(reason || '')] || 500
}

/** <dataDir>/flags.json (single source of truth for switch persistence). */
export function flagsFilePath(dataDir) {
  const dir = String(dataDir || '').trim() || resolveDataDir({})
  return join(dir, FLAGS_FILE_NAME)
}

/** Validate one POST body; never throws. */
export function sanitizeSwitchRequest(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    return { ok: false, reason: 'bad_payload' }
  }
  const key = typeof input.key === 'string' ? input.key.trim() : ''
  if (!KEY_SET.has(key)) {
    return { ok: false, reason: 'unknown_key', allowed: SWITCH_KEYS.slice() }
  }
  if (typeof input.value !== 'boolean') {
    return { ok: false, reason: 'bad_value' }
  }
  if (input.confirm !== true) {
    return { ok: false, reason: 'confirm_required', hint: 'POST body needs confirm: true' }
  }
  return { ok: true, key, value: input.value }
}

/** Project one stored document onto the four boolean switches. */
export function readSwitchValues(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const out = { ...DEFAULT_SWITCHES }
  for (const key of SWITCH_KEYS) {
    if (typeof source[key] === 'boolean') {
      out[key] = source[key]
      continue
    }
    const alias = SWITCH_READ_ALIASES[key]
    if (alias && typeof source[alias] === 'boolean') out[key] = source[alias]
  }
  return out
}

/** Read <dataDir>/flags.json; missing / bad JSON degrade to {} (GET never throws). */
export function readFlagsDoc(file) {
  if (!existsSync(file)) return { missing: true, value: {} }
  try {
    const text = readFileSync(file, 'utf8')
    const data = JSON.parse(text)
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      return { missing: false, value: {}, error: 'bad_json' }
    }
    return { missing: false, value: data }
  } catch {
    return { missing: false, value: {}, error: 'read_failed' }
  }
}

function fileRevision(file) {
  try { return statSync(file).mtimeMs } catch { return null }
}

/** Read the four switches from flags.json (read-only; no file creation). */
export function readSwitches(dataDir) {
  const file = flagsFilePath(dataDir)
  const doc = readFlagsDoc(file)
  return {
    ok: true,
    switches: readSwitchValues(doc.value),
    namespace: 'biocore',
    revision: doc.missing ? null : fileRevision(file),
  }
}

/** Atomic JSON write: temp file first, then rename over the target. */
export function writeFlagsFileAtomic(file, value) {
  const dir = dirname(file)
  const tmp = file + '.tmp'
  try {
    mkdirSync(dir, { recursive: true })
    writeFileSync(tmp, JSON.stringify(value, null, 2), 'utf8')
    renameSync(tmp, file)
    return { ok: true, revision: fileRevision(file) }
  } catch {
    try { if (existsSync(tmp)) unlinkSync(tmp) } catch { /* best effort */ }
    return { ok: false }
  }
}

/** Apply one whitelisted switch to flags.json; never writes state files. */
export function applySwitch(dataDir, body) {
  const clean = sanitizeSwitchRequest(body)
  if (!clean.ok) return clean
  const file = flagsFilePath(dataDir)
  const doc = readFlagsDoc(file)
  const current = doc.value && typeof doc.value === 'object' && !Array.isArray(doc.value)
    ? doc.value
    : {}
  // Persist all four keys explicitly so flags.json is self-describing.
  const next = { ...current, ...readSwitchValues({ ...current, [clean.key]: clean.value }) }
  const written = writeFlagsFileAtomic(file, next)
  if (!written.ok) return { ok: false, reason: 'write_failed' }
  return {
    ok: true,
    key: clean.key,
    value: clean.value,
    switches: readSwitchValues(next),
    namespace: 'biocore',
    revision: written.revision,
  }
}

/** Read a request body; resolves null when it exceeds maxBytes. */
export function readBody(req, maxBytes) {
  return new Promise((resolveBody) => {
    let data = ''
    let done = false
    const finish = (value) => {
      if (done) return
      done = true
      resolveBody(value)
    }
    try {
      req.on('data', (chunk) => {
        data += chunk
        if (data.length > maxBytes) {
          finish(null)
          try { req.destroy?.() } catch { /* client already gone */ }
        }
      })
      req.on('end', () => finish(data))
      req.on('error', () => finish(''))
    } catch {
      finish('')
    }
  })
}

function dataDirOf(options) {
  const opts = options && typeof options === 'object' ? options : {}
  if (typeof opts.getDataDir === 'function') {
    try { return String(opts.getDataDir() || '') } catch { return '' }
  }
  return typeof opts.dataDir === 'string' ? opts.dataDir : ''
}

function send(res, code, payload) {
  try {
    res.writeHead(code, {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    })
    if (payload === undefined) res.end()
    else res.end(JSON.stringify(payload))
  } catch { /* client already gone */ }
  return payload
}

/** HTTP handler for /api/biocore/switch (GET read-only, POST write). */
export async function handleSwitchRoute(req, res, options) {
  const dataDir = dataDirOf(options)
  const method = String((req && req.method) || 'GET').toUpperCase()
  if (method === 'GET' || method === 'HEAD') {
    const result = readSwitches(dataDir)
    return send(res, 200, method === 'HEAD' ? undefined : result)
  }
  if (method !== 'POST') {
    return send(res, 405, { ok: false, reason: 'method_not_allowed', allowed: ['GET', 'POST'] })
  }
  const raw = await readBody(req, BODY_LIMIT)
  if (raw === null) {
    return send(res, 413, { ok: false, reason: 'payload_too_large', limit: BODY_LIMIT })
  }
  let body
  try {
    body = JSON.parse(raw || '{}')
  } catch {
    return send(res, 400, { ok: false, reason: 'bad_payload' })
  }
  const result = applySwitch(dataDir, body)
  return send(res, result.ok ? 200 : statusOf(result.reason), result)
}
