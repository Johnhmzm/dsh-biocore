/** dsh-biocore-ui host half: per-session state + one-turn updates.
 *
 * Endpoint:
 *   GET /api/biocore/state?session=<id>
 *     - session missing  -> { available:false, reason:'no_session' }
 *     - state missing    -> lazily create from dsh_adapter default_state, then serve
 *     - bad JSON         -> { available:false, reason:'bad_state' }
 *
 * State really changes through two paths, both using the python adapter bridge
 * (lib/py/bridge.py -> dsh_adapter one_turn, subprocess only):
 *   1) host hook 'session/event' (DSH session feed): every direct human
 *      user/message runs one_turn and writes the session state back;
 *   2) fallback slash command '/bio sync <一句话>' updates the current session.
 */
import { chmodSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import {
  cleanSessionId,
  payloadWithinLimit,
  projectState,
  readStateFile,
  sessionStateFile,
  STATE_LIMIT,
} from './modules/state.js'
import { compilePersona, ensureState, runTurn, runtimeAction } from './py-bridge.js'
import { SWITCH_ROUTE_PATH, handleSwitchRoute, readSwitches } from './modules/switch.js'
import { TRACE_ROUTE_PATH, defaultStoreDir, handleTraceRoute } from './modules/trace.js'
import { queueSessionTurn } from './modules/turn-queue.js'
import { anySwitchOn } from './modules/expression-inject.js'
import { makeCoreSessionListener, registerCoreTurn } from './modules/core-turn.js'
import { migrateLegacyPluginData, PLUGIN_DATA_DIR, resolveDataDir } from './modules/data-dir.js'
import {
  PANEL_MOUNT_PATH,
  PANEL_ROUTE_PREFIX,
  buildPanelExtras,
  servePanelPage,
} from './modules/panel-data.js'
import { resolveMemoryDir } from './modules/memory-store.js'
import { registerMemoryCapture } from './modules/memory-capture.js'
import {
  createInjectionState,
  registerMemoryInjection,
} from './modules/memory-inject.js'
import { registerMemoryTools } from './modules/memory-tools.js'
import { registerProactive } from './modules/proactive.js'
import {
  applyPersonaPreset,
  personaPresetStatus,
  readPersonaText,
  rollbackPersonaPreset,
} from './modules/persona-preset.js'
import {
  EXPRESSION_DEFAULTS,
  normalizeExpression,
  readConfig,
} from './modules/expression-config.js'
import {
  migratePersonaToShared,
  profilePersonaPaths,
  resolvePersonaPaths,
  syncPersonaToShared,
} from './modules/persona-store.js'

export const STATE_ROUTE_PATH = '/api/biocore/state'
export const PERSONA_ROUTE_PATH = '/api/biocore/persona'
export const PERSONA_APPLY_ROUTE_PATH = '/api/biocore/persona/apply'
export const SETTINGS_ROUTE_PATH = '/api/biocore/settings'
export const SETTINGS_FILE_NAME = 'config.json'
export const COMMAND_NAME = 'bio'
export const PERSONA_TEXT_LIMIT = 20000
export const DEFAULT_STATE_DIR = PLUGIN_DATA_DIR
export const inject = ['webServer']
export { SWITCH_ROUTE_PATH, TRACE_ROUTE_PATH, resolveDataDir, PANEL_ROUTE_PREFIX, PANEL_MOUNT_PATH, resolveMemoryDir }

/** Persona files live next to flags.json / sessions under the same dataDir. */
export function personaPaths(dataDir) {
  return profilePersonaPaths(String(dataDir || '').trim() || DEFAULT_STATE_DIR)
}

/** Persona profile actually used for one profile's sessions (shared wins). */
export function personaProfilePathFor(dataDir) {
  return resolvePersonaPaths(dataDir).profile
}

/** <dataDir>/config.json holds embedding/model settings (task-216). */
export function settingsPaths(dataDir) {
  const dir = String(dataDir || '').trim() || DEFAULT_STATE_DIR
  return { file: join(dir, SETTINGS_FILE_NAME), dir }
}

export const EMBEDDING_MODES = Object.freeze(['auto', 'remote', 'local', 'off'])

function emptyEmbedding() {
  return { mode: 'auto', base_url: '', model: '', api_key: '', local_path: '' }
}

function normalizeEmbedding(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const rawMode = String(source.mode || 'auto').trim()
  return {
    mode: EMBEDDING_MODES.includes(rawMode) ? rawMode : null,
    base_url: String(source.base_url || '').trim().slice(0, 2000),
    model: String(source.model || '').trim().slice(0, 200),
    api_key: typeof source.api_key === 'string' ? source.api_key.trim().slice(0, 4000) : '',
    local_path: String(source.local_path || '').trim().slice(0, 2000),
  }
}

export const RERANK_DEFAULTS = Object.freeze({
  enabled: false,
  base_url: 'https://api.jina.ai/v1',
  model: 'jina-reranker-v2-base-multilingual',
  api_key: '',
  top_n: 20,
})

function normalizeRerank(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const rawTop = Number(source.top_n)
  const top = Number.isFinite(rawTop) ? Math.max(1, Math.min(100, Math.round(rawTop))) : RERANK_DEFAULTS.top_n
  return {
    enabled: source.enabled === true,
    base_url: String(source.base_url || RERANK_DEFAULTS.base_url).trim().slice(0, 2000),
    model: String(source.model || RERANK_DEFAULTS.model).trim().slice(0, 200),
    api_key: typeof source.api_key === 'string' ? source.api_key.trim().slice(0, 4000) : '',
    top_n: top,
  }
}

/** GET must never leak the plaintext key. */
export function maskApiKey(key) {
  const text = String(key || '')
  if (!text) return ''
  if (text.length <= 6) return '••••'
  return text.slice(0, 2) + '••••••' + text.slice(-2)
}

function maskedEmbedding(embedding) {
  return Object.assign({}, embedding, { api_key: maskApiKey(embedding.api_key) })
}

function maskedRerank(rerank) {
  return Object.assign({}, rerank, { api_key: maskApiKey(rerank.api_key) })
}

function defaultEmbedding() {
  const embedding = emptyEmbedding()
  embedding.mode = 'auto'
  return embedding
}

function storedEmbedding(stored) {
  const source = stored && stored.embedding
  const embedding = source ? normalizeEmbedding(source) : defaultEmbedding()
  if (!embedding.mode) embedding.mode = 'auto'
  return embedding
}

/** Full config.json projection with defaults for all three sections. */
export function readFullConfig(dataDir) {
  let doc = null
  try {
    doc = JSON.parse(readFileSync(join(String(dataDir || '').trim(), SETTINGS_FILE_NAME), 'utf8'))
  } catch { doc = null }
  const source = doc && typeof doc === 'object' && !Array.isArray(doc) ? doc : {}
  return {
    embedding: storedEmbedding(source),
    rerank: normalizeRerank(source.rerank),
    expression: normalizeExpression(source.expression),
    sanitize_reply: source.sanitize_reply !== false,
  }
}

function writeFullConfig(file, config) {
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, JSON.stringify({
    embedding: config.embedding,
    rerank: config.rerank,
    expression: config.expression,
    sanitize_reply: config.sanitize_reply !== false,
  }, null, 2) + '\n', { encoding: 'utf8', mode: 0o600 })
  try { chmodSync(file, 0o600) } catch { /* Windows may ignore POSIX modes */ }
}

/** GET/POST /api/biocore/settings: embedding + rerank + expression. */
export async function handleSettings(req, res, dataDir) {
  const paths = settingsPaths(dataDir)
  const method = String((req && req.method) || 'GET').toUpperCase()
  if (method === 'GET') {
    const config = readFullConfig(paths.dir)
    return sendJson(res, 200, {
      ok: true,
      embedding: maskedEmbedding(config.embedding),
      rerank: maskedRerank(config.rerank),
      expression: config.expression,
      sanitize_reply: config.sanitize_reply !== false,
      restart_required: false,
    })
  }
  if (method !== 'POST') {
    return sendJson(res, 405, { ok: false, reason: 'method_not_allowed' })
  }
  let body = null
  try {
    body = await readJsonBody(req)
  } catch (error) {
    return sendJson(res, 400, { ok: false, reason: (error && error.reason) || 'bad_payload' })
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return sendJson(res, 400, { ok: false, reason: 'bad_payload' })
  }
  if (body.confirm !== true) {
    return sendJson(res, 400, { ok: false, reason: 'confirm_required' })
  }
  const config = readFullConfig(paths.dir)
  const hasEmbedding = body.embedding && typeof body.embedding === 'object'
  const hasRerank = body.rerank && typeof body.rerank === 'object'
  const hasExpression = body.expression && typeof body.expression === 'object'
  const hasSanitize = typeof body.sanitize_reply === 'boolean'
  if (!hasEmbedding && !hasRerank && !hasExpression && !hasSanitize) {
    return sendJson(res, 400, { ok: false, reason: 'no_settings' })
  }
  if (hasEmbedding) {
    const raw = body.embedding
    const embedding = normalizeEmbedding(raw)
    if (!embedding.mode) {
      return sendJson(res, 400, { ok: false, reason: 'bad_mode', allowed: Array.from(EMBEDDING_MODES) })
    }
    if (!('api_key' in raw) || String(raw.api_key || '').startsWith('••')) {
      embedding.api_key = config.embedding.api_key
    }
    config.embedding = embedding
  }
  if (hasRerank) {
    const raw = body.rerank
    const rerank = normalizeRerank(raw)
    if (!('api_key' in raw) || String(raw.api_key || '').startsWith('••')) {
      rerank.api_key = config.rerank.api_key
    }
    config.rerank = rerank
  }
  if (hasExpression) config.expression = normalizeExpression(body.expression)
  if (hasSanitize) config.sanitize_reply = body.sanitize_reply === true
  writeFullConfig(paths.file, config)
  return sendJson(res, 200, {
    ok: true,
    embedding: maskedEmbedding(config.embedding),
    rerank: maskedRerank(config.rerank),
    expression: config.expression,
    sanitize_reply: config.sanitize_reply !== false,
    restart_required: true,
    hint: '重启 BioCore 生效',
  })
}

function notConfigured(kind, detail) {
  return { ok: false, kind, reason: 'not_configured', detail }
}

/** One minimal API call; never returns or logs the plaintext key. */
export async function testApiConnection(kind, config, options) {
  const opts = options || {}
  const fetchImpl = typeof opts.fetchImpl === 'function' ? opts.fetchImpl : globalThis.fetch
  const timeoutMs = Number(opts.timeoutMs) || 6000
  if (typeof fetchImpl !== 'function') return { ok: false, kind, reason: 'fetch_unavailable' }
  let url = ''
  let payload = null
  if (kind === 'embedding') {
    const embedding = config && config.embedding ? config.embedding : {}
    if (embedding.mode === 'off' || embedding.mode === 'local') {
      return notConfigured(kind, 'embedding mode is ' + embedding.mode)
    }
    if (!embedding.base_url || !embedding.model || !embedding.api_key) {
      return notConfigured(kind, 'embedding base_url/model/api_key missing')
    }
    url = String(embedding.base_url).replace(/\/+$/, '') + '/embeddings'
    payload = { model: embedding.model, input: 'ping' }
  } else if (kind === 'rerank') {
    const rerank = config && config.rerank ? config.rerank : {}
    if (rerank.enabled !== true) return notConfigured(kind, 'rerank disabled')
    if (!rerank.base_url || !rerank.model || !rerank.api_key) {
      return notConfigured(kind, 'rerank base_url/model/api_key missing')
    }
    url = String(rerank.base_url).replace(/\/+$/, '') + '/rerank'
    payload = {
      model: rerank.model,
      query: 'ping',
      documents: ['ping'],
      top_n: 1,
    }
  } else {
    return { ok: false, kind: String(kind || ''), reason: 'bad_kind' }
  }
  const key = kind === 'embedding'
    ? String(config.embedding.api_key || '')
    : String(config.rerank.api_key || '')
  const controller = typeof AbortController === 'function' ? new AbortController() : null
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null
  const started = Date.now()
  try {
    const response = await fetchImpl(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: 'Bearer ' + key },
      body: JSON.stringify(payload),
      signal: controller ? controller.signal : undefined,
    })
    const status = Number(response && response.status) || 0
    const elapsed = Date.now() - started
    if (response && response.ok) {
      return { ok: true, kind, status_code: status, elapsed_ms: elapsed }
    }
    return { ok: false, kind, reason: 'http_error', status_code: status, elapsed_ms: elapsed }
  } catch (error) {
    const name = error && error.name ? String(error.name) : ''
    return {
      ok: false,
      kind,
      reason: name === 'AbortError' ? 'timeout' : 'network_error',
      elapsed_ms: Date.now() - started,
    }
  } finally {
    if (timer) clearTimeout(timer)
  }
}

/** POST /api/biocore/settings/test {kind:'embedding'|'rerank'}. */
export async function handleSettingsTest(req, res, options) {
  const opts = options || {}
  const dataDir = opts.dataDir
  const method = String((req && req.method) || 'POST').toUpperCase()
  if (method !== 'POST') return sendJson(res, 405, { ok: false, reason: 'method_not_allowed' })
  let body = null
  try { body = await readJsonBody(req) } catch (error) {
    return sendJson(res, 400, { ok: false, reason: (error && error.reason) || 'bad_payload' })
  }
  const kind = String(body && body.kind || '').trim()
  if (kind !== 'embedding' && kind !== 'rerank') {
    return sendJson(res, 400, { ok: false, reason: 'bad_kind', allowed: ['embedding', 'rerank'] })
  }
  const config = readFullConfig(dataDir)
  const result = await testApiConnection(kind, config, { fetchImpl: opts.fetchImpl })
  return sendJson(res, 200, result)
}

const PERSONA_PREVIEW_FIELDS = [
  ['E', '外向', 2],
  ['A', '宜人', 2],
  ['baseline_v', '情绪基线', 2],
  ['baseline_a', '唤醒基线', 2],
  ['stress_reactivity', '压力反应', 2],
  ['recovery_rate', '恢复速度', 2],
  ['social_baseline', '社交基线', 2],
  ['autonomy_gain', '自主增益', 2],
  ['learning_rate', '学习率', 3],
]

/** 9 labeled numbers for the persona compile preview. */
export function personaPreview(profile) {
  const source = profile && typeof profile === 'object' ? profile : {}
  const rows = []
  for (const [key, label, digits] of PERSONA_PREVIEW_FIELDS) {
    const value = Number(source[key])
    rows.push({
      key,
      label,
      value: Number.isFinite(value) ? Number(value.toFixed(digits)) : null,
    })
  }
  return rows
}

function readJsonBody(req, limit) {
  const max = Number(limit) || 64 * 1024
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > max) {
        const error = new Error('payload_too_large')
        error.reason = 'payload_too_large'
        reject(error)
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'))
      } catch {
        const error = new Error('bad_payload')
        error.reason = 'bad_payload'
        reject(error)
      }
    })
    req.on('error', () => {
      const error = new Error('bad_payload')
      error.reason = 'bad_payload'
      reject(error)
    })
  })
}

/** GET/POST /api/biocore/persona (task-208). */
export async function handlePersona(req, res, dataDir) {
  const resolved = resolvePersonaPaths(dataDir)
  const paths = { profile: resolved.profile, text: resolved.text }
  const method = String((req && req.method) || 'GET').toUpperCase()
  if (method === 'GET') {
    let raw = ''
    try {
      raw = readFileSync(paths.profile, 'utf8')
    } catch {
      return sendJson(res, 200, {
        ok: true, text: '', profile: null, updated_at: 0, reason: 'no_persona',
        source: resolved.source, profile_path: paths.profile, text_path: paths.text,
      })
    }
    let doc = null
    try { doc = JSON.parse(raw) } catch { doc = null }
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
      return sendJson(res, 200, { ok: false, reason: 'bad_profile' })
    }
    const profile = doc.profile && typeof doc.profile === 'object' && !Array.isArray(doc.profile)
      ? doc.profile : null
    let text = String(doc.persona || '')
    if (!text.trim()) {
      try { text = readFileSync(paths.text, 'utf8').trim() } catch { /* keep empty */ }
    }
    let updatedAt = 0
    try { updatedAt = Math.round(statSync(paths.profile).mtimeMs) } catch { updatedAt = 0 }
    return sendJson(res, 200, {
      ok: true, text, profile, updated_at: updatedAt,
      preview: personaPreview(profile),
      source: resolved.source,
      profile_path: paths.profile,
      text_path: paths.text,
    })
  }
  if (method !== 'POST') {
    return sendJson(res, 405, { ok: false, reason: 'method_not_allowed' })
  }
  let body = null
  try {
    body = await readJsonBody(req)
  } catch (error) {
    return sendJson(res, 400, { ok: false, reason: (error && error.reason) || 'bad_payload' })
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return sendJson(res, 400, { ok: false, reason: 'bad_payload' })
  }
  if (body.confirm !== true) {
    return sendJson(res, 400, { ok: false, reason: 'confirm_required' })
  }
  const text = String(body.text == null ? '' : body.text)
  if (!text.trim()) return sendJson(res, 400, { ok: false, reason: 'empty_text' })
  if (text.trim().length > PERSONA_TEXT_LIMIT) {
    return sendJson(res, 400, { ok: false, reason: 'text_too_long', limit: PERSONA_TEXT_LIMIT })
  }
  const result = await compilePersona(paths.profile, paths.text, text)
  if (!result || result.ok !== true) {
    return sendJson(res, 500, {
      ok: false,
      reason: (result && result.reason) || 'compile_failed',
      detail: (result && result.detail) || '',
    })
  }
  // Persist the compiled persona to the shared directory as well (profiles
  // should not each need their own copy); the profile copy stays for compat.
  syncPersonaToShared(dataDir)
  const profile = result.profile && typeof result.profile === 'object' ? result.profile : {}
  return sendJson(res, 200, {
    ok: true,
    text: text.trim(),
    profile,
    updated_at: result.updated_at || 0,
    persona_hash: result.persona_hash || '',
    preview: personaPreview(profile),
    source: 'shared',
    profile_path: resolvePersonaPaths(dataDir).profile,
  })
}

/** GET/POST /api/biocore/persona/apply (task-214).
 *
 * GET  -> {ok, applied, preset_id, patch_path, chars, matches_preset, backup_path,...}
 * POST -> {confirm:true} writes persona.md into the default agent-preset's
 *         @deepseek-ai/dsh-persona prefix through ctx.configEditor.edit
 *         (profile patch + Loader reload); {confirm:true, rollback:true}
 *         restores the captured original preset config.
 */
export async function handlePersonaApply(req, res, options) {
  const opts = options || {}
  const dataDir = opts.dataDir
  const ctx = opts.ctx
  const editor = (() => {
    try { return ctx && typeof ctx.get === 'function' ? ctx.get('configEditor') : null } catch { return null }
  })()
  const presets = (() => {
    try { return ctx && typeof ctx.get === 'function' ? ctx.get('agentPresets') : null } catch { return null }
  })()
  const method = String((req && req.method) || 'GET').toUpperCase()
  if (method === 'GET') {
    try {
      return sendJson(res, 200, personaPresetStatus(editor, presets, dataDir))
    } catch (error) {
      return sendJson(res, 500, { ok: false, reason: 'status_failed', detail: String(error) })
    }
  }
  if (method !== 'POST') {
    return sendJson(res, 405, { ok: false, reason: 'method_not_allowed' })
  }
  let body = null
  try {
    body = await readJsonBody(req)
  } catch (error) {
    return sendJson(res, 400, { ok: false, reason: (error && error.reason) || 'bad_payload' })
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return sendJson(res, 400, { ok: false, reason: 'bad_payload' })
  }
  if (body.confirm !== true) {
    return sendJson(res, 400, { ok: false, reason: 'confirm_required' })
  }
  if (body.rollback === true) {
    let result
    try { result = await rollbackPersonaPreset({ editor, dataDir, now: Date.now() }) }
    catch (error) { return sendJson(res, 500, { ok: false, reason: 'rollback_failed', detail: String(error) }) }
    return sendJson(res, result && result.ok ? 200 : 409, result)
  }
  const text = readPersonaText(dataDir)
  if (!text) return sendJson(res, 400, { ok: false, reason: 'empty_persona' })
  let result
  try { result = await applyPersonaPreset({ editor, presets, dataDir, text, now: Date.now() }) }
  catch (error) { return sendJson(res, 500, { ok: false, reason: 'apply_failed', detail: String(error) }) }
  if (result && result.ok) syncPersonaToShared(dataDir)
  return sendJson(res, result && result.ok ? 200 : 409, result)
}

/** Session state and flags.json share one data directory (task-197). */
export function resolveStateDir(config) {
  return resolveDataDir(config)
}

function unavailable(reason, extra) {
  return Object.assign({ available: false, reason: String(reason) }, extra || {})
}

function sendJson(res, code, payload) {
  res.writeHead(code, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
  })
  res.end(JSON.stringify(payload))
}

/** Plain text of one DSH message payload (text content blocks only). */
export function textOfMessage(message) {
  try {
    const blocks = message && Array.isArray(message.content) ? message.content : []
    const parts = []
    for (const block of blocks) {
      if (block && block.type === 'text' && typeof block.text === 'string') {
        parts.push(block.text)
      }
    }
    return parts.join('\n').replace(/\s+/g, ' ').trim().slice(0, 2000)
  } catch {
    return ''
  }
}

/** /bio sync <text> grammar. */
export function parseSyncInput(rawInput) {
  const text = String(rawInput || '').trim()
  if (!text) return { ok: false, reason: 'usage' }
  const match = /^sync\b\s*([\s\S]*)$/.exec(text)
  if (!match) return { ok: false, reason: 'usage' }
  const message = match[1].trim()
  if (!message) return { ok: false, reason: 'usage' }
  return { ok: true, text: message.slice(0, 2000) }
}

function fmt(value) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(2) : '-'
}

/** One-line state-change summary for /bio sync. */
export function summarizeTurn(result) {
  const before = (result && result.before) || {}
  const after = (result && result.after) || {}
  const hint = (result && result.reply_hint) || {}
  return 'v ' + fmt(before.v) + '→' + fmt(after.v)
    + ' · 压力 ' + fmt(before.stress) + '→' + fmt(after.stress)
    + ' · 长度 ' + fmt(hint.length_scale)
    + ' · 主动性 ' + fmt(hint.initiative)
    + ' · 允许改口 ' + (hint.allow_correction ? '是' : '否')
}

/** Command handler factory (runTurn injectable for tests). */
export function makeCommandHandler(options) {
  const opts = options || {}
  const stateDir = opts.stateDir || DEFAULT_STATE_DIR
  const profilePath = opts.profilePath || personaProfilePathFor(stateDir)
  const turn = typeof opts.runTurn === 'function' ? opts.runTurn : runTurn
  const sessionOf = typeof opts.sessionOf === 'function'
    ? opts.sessionOf
    : (invocation) => invocation && invocation.agent && invocation.agent.session
      && invocation.agent.session.id
  return async function handleBioCommand(invocation) {
    const parsed = parseSyncInput(invocation && invocation.rawInput)
    if (!parsed.ok) {
      return { kind: 'error', text: '用法：/bio sync <一句话>' }
    }
    const sessionId = cleanSessionId(sessionOf(invocation))
    if (!sessionId) {
      return { kind: 'error', text: '无法确定当前会话（no_session）' }
    }
    const stateFile = sessionStateFile(stateDir, sessionId)
    const result = await turn(stateFile, parsed.text, profilePath)
    if (!result || result.ok !== true) {
      return {
        kind: 'error',
        text: '状态更新失败：' + ((result && result.reason) || 'unknown'),
      }
    }
    return {
      kind: 'success',
      text: summarizeTurn(result) + '（session ' + sessionId.slice(0, 8) + '）',
    }
  }
}

/** 'session/event' listener factory (runTurn injectable for tests). */
export function makeSessionEventListener(options) {
  const opts = options || {}
  const stateDir = opts.stateDir || DEFAULT_STATE_DIR
  const profilePath = opts.profilePath || personaProfilePathFor(stateDir)
  const turn = typeof opts.runTurn === 'function' ? opts.runTurn : runTurn
  return function onSessionEvent(session, event) {
    try {
      if (!event || event.type !== 'user/message') return
      const data = event.data || {}
      const source = data.source || {}
      if (source.kind !== 'user') return
      const text = textOfMessage(data)
      if (!text) return
      const sessionId = cleanSessionId(session && session.id)
      if (!sessionId) return
      const stateFile = sessionStateFile(stateDir, sessionId)
      queueSessionTurn(sessionId, async () => {
        try {
          return await turn(stateFile, text, profilePath)
        } catch (error) {
          // task-201: one diagnostic line; a failed update never breaks a turn.
          try {
            console.error('__bioTurnErr ' + String((error && error.stack) || error))
          } catch { /* logging must not throw */ }
          return null
        }
      })
    } catch {
      /* contained: a state update never disturbs a turn */
    }
  }
}

const CORE_STATE_TTL_MS = 5000
const CORE_STATE_CACHE = new Map()

/** Read the core pipeline state action (panel state, task-212), briefly cached. */
export async function fetchCoreState(sessionId, options) {
  const opts = options || {}
  const runAction = typeof opts.runAction === 'function' ? opts.runAction : runtimeAction
  const key = String(sessionId == null ? '' : sessionId)
  if (!key) return null
  const now = Date.now()
  const cached = CORE_STATE_CACHE.get(key)
  if (cached && now - cached.at < CORE_STATE_TTL_MS) return cached.value
  let value = null
  try {
    value = await runAction('state', { session_id: key, store: opts.store || defaultStoreDir() })
  } catch { value = null }
  CORE_STATE_CACHE.set(key, { at: Date.now(), value })
  while (CORE_STATE_CACHE.size > 100) CORE_STATE_CACHE.delete(CORE_STATE_CACHE.keys().next().value)
  return value
}

/** Overlay the core state action's fields onto the /state projection. */
export function mergeCoreState(body, reply) {
  const base = body && typeof body === 'object' ? Object.assign({}, body) : { available: true }
  if (!reply || reply.ok !== true || !reply.state || typeof reply.state !== 'object') return base
  const core = reply.state
  if (core.session_id != null) base.session_id = String(core.session_id)
  const count = core.memory && Number(core.memory.count)
  if (Number.isFinite(count)) base.memory_count = count
  const intero = (core.intero && typeof core.intero === 'object')
    ? core.intero
    : ((core.core && core.core.intero && typeof core.core.intero === 'object') ? core.core.intero : null)
  if (intero) base.intero = Object.assign({}, base.intero || {}, intero)
  if (core.relationship && typeof core.relationship === 'object') {
    const rel = core.relationship
    const metrics = Object.assign({}, base.relationship_metrics || {})
    for (const key of ['closeness', 'trust', 'conflict', 'bids']) {
      const value = Number(rel[key])
      if (Number.isFinite(value)) metrics[key] = Math.round(value * 100) / 100
    }
    base.relationship_metrics = metrics
  }
  for (const key of ['v', 'intero', 'tone', 'relation', 'autonomy', 'history',
    'memory_recent', 'narrative', 'body_metrics', 'motivation']) {
    if (core[key] !== undefined) base[key] = core[key]
  }
  return base
}

export async function handleState(req, res, stateDir, options) {
  const opts = options && typeof options === 'object' ? options : {}
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    return sendJson(res, 405, unavailable('method_not_allowed'))
  }
  let url = null
  try { url = new URL(String(req.url || ''), 'http://local') } catch { url = null }
  const rawSession = url ? url.searchParams.get('session') : null
  if (!rawSession) return sendJson(res, 200, unavailable('no_session'))
  const sessionId = cleanSessionId(rawSession)
  if (!sessionId) return sendJson(res, 200, unavailable('bad_session'))
  const stateFile = sessionStateFile(stateDir, sessionId)
  const read = readStateFile(stateFile)
  let body = null
  if (read.missing) {
    // Fast path: never call Python when the session file exists. Only a
    // missing file + an enabled switch may fall back to runtimeAction('state').
    let core = null
    try {
      const getSwitches = typeof opts.getSwitches === 'function' ? opts.getSwitches : null
      if (getSwitches && anySwitchOn(getSwitches())) {
        core = await fetchCoreState(sessionId, {
          runAction: opts.runAction,
          store: opts.store,
        })
      }
    } catch { core = null }
    if (!core || core.ok !== true || !core.state || typeof core.state !== 'object') {
      return sendJson(res, 200, unavailable('no_state_yet'))
    }
    body = projectState(core.state, { mtimeMs: 0, sessionId })
    Object.assign(body, buildPanelExtras(core.state))
    body.source = 'runtime'
  } else if (read.error) {
    return sendJson(res, 200, unavailable(read.error))
  } else {
    body = projectState(read.state, { mtimeMs: read.mtimeMs, sessionId })
    Object.assign(body, buildPanelExtras(read.state))
    body.source = 'file'
  }
  if (!payloadWithinLimit(body)) {
    return sendJson(res, 200, unavailable('payload_too_large'))
  }
  return sendJson(res, 200, body)
}

export function apply(ctx, config) {
  const dataDir = resolveDataDir(config)
  try { migrateLegacyPluginData(dataDir) } catch { /* best-effort data migration */ }
  try { migratePersonaToShared(dataDir) } catch { /* best-effort migration */ }
  const disposers = []
  const keep = (dispose) => {
    if (typeof dispose === 'function') disposers.push(dispose)
  }

  // 1) task-212: per-turn core milestones replace the adapter one_turn path.
  //    user/message -> turn_start (prompt_block for -190); assistant/message ->
  //    turn_end (gate + trace + relationship). All switches off: no calls.
  const getSwitches = () => {
    try { return readSwitches(dataDir).switches } catch { return {} }
  }
  try {
    if (ctx && typeof ctx.on === 'function') {
      keep(ctx.on('session/event', makeCoreSessionListener({
        stateDir: dataDir,
        getSwitches,
        store: resolveMemoryDir(dataDir),
      })))
    }
  } catch { /* headless or older host */ }
  try {
    keep(registerCoreTurn(ctx, { getSwitches, store: resolveMemoryDir(dataDir) }))
  } catch { /* prompt/assembly service unavailable */ }

  // 2) read-only endpoint.
  try {
    if (ctx && typeof ctx.inject === 'function') {
      ctx.inject(['webServer'], (webCtx) => {
        const webServer = webCtx && webCtx.webServer
        if (!webServer || typeof webServer.register !== 'function') return
        keep(webServer.register({
          kind: 'exact',
          path: STATE_ROUTE_PATH,
          handler: (req, res) => handleState(req, res, dataDir, { getSwitches, store: resolveMemoryDir(dataDir) }),
        }))
        // task-196: four switches persist to <dataDir>/flags.json (atomic write).
        keep(webServer.register({
          kind: 'exact',
          path: SWITCH_ROUTE_PATH,
          handler: (req, res) => handleSwitchRoute(req, res, {
            dataDir,
          }),
        }))
        // task-210: read-only "why this turn" traces written by python.
        keep(webServer.register({
          kind: 'exact',
          path: TRACE_ROUTE_PATH,
          handler: (req, res) => handleTraceRoute(req, res),
        }))
        // task-202: standalone instrument panel (ui/panel.html + assets).
        keep(webServer.register({
          kind: 'prefix',
          path: PANEL_MOUNT_PATH,
          handler: servePanelPage,
        }))
        // task-208: persona prompt -> compiled numeric profile.
        keep(webServer.register({
          kind: 'exact',
          path: PERSONA_ROUTE_PATH,
          handler: (req, res) => handlePersona(req, res, dataDir),
        }))
        // task-214: persona -> persistent DSH preset persona (configEditor).
        keep(webServer.register({
          kind: 'exact',
          path: PERSONA_APPLY_ROUTE_PATH,
          handler: (req, res) => handlePersonaApply(req, res, { dataDir, ctx }),
        }))
        // task-216: embedding/model settings -> <dataDir>/config.json.
        keep(webServer.register({
          kind: 'exact',
          path: SETTINGS_ROUTE_PATH,
          handler: (req, res) => handleSettings(req, res, dataDir),
        }))
        // API connection test (embedding / rerank); never logs the key.
        keep(webServer.register({
          kind: 'exact',
          path: SETTINGS_ROUTE_PATH + '/test',
          handler: (req, res) => handleSettingsTest(req, res, { dataDir }),
        }))
      })
    }
  } catch { /* headless */ }

  // 3) fallback command: /bio sync <一句话>.
  try {
    if (ctx && typeof ctx.inject === 'function') {
      ctx.inject(['commands'], (scoped) => {
        const commands = scoped && scoped.commands
        if (!commands || typeof commands.register !== 'function') return
        keep(commands.register({
          name: COMMAND_NAME,
          description: 'BioCore 状态：/bio sync <一句话> 推进一轮并写回该会话状态',
          input: { hint: 'sync <一句话>' },
          handler: makeCommandHandler({ stateDir: dataDir }),
        }))
      })
    }
  } catch { /* command service unavailable */ }

  // 4) memory (task-203): capture both sides, inject a cached summary, expose
  //    remember/recall/forget tools. All store I/O runs in the python bridge.
  const memoryDir = resolveMemoryDir(config)
  const injectionState = createInjectionState()
  try {
    // Normally capture happens inside core turn_start/turn_end. The standalone
  // session/event capture listener stays available but is opt-in.
  if (config && config.memoryCaptureEnabled === true) {
    const capture = registerMemoryCapture(ctx, () => config, memoryDir, {
      injectionState,
    })
    if (capture) keep(capture.dispose)
  }
  } catch { /* memory capture unavailable */ }
  try {
    const injection = registerMemoryInjection(ctx, () => config, memoryDir,
      injectionState)
    if (injection) keep(injection)
  } catch { /* prompt service unavailable */ }
  try {
    const tools = registerMemoryTools(ctx, () => config, memoryDir)
    if (tools) keep(tools.dispose)
  } catch { /* tool service unavailable */ }

  // 5) proactive (task-205): default OFF (config.proactiveEnabled=false in
  //    cordis.patch.yml). When enabled, one unref'd interval checks state via
  //    our python bridge and delivers a short user-role wake-up through
  //    ctx.sessionController.resolveAgent -> agent.followup, the same host path
  //    the built-in dsh-schedule uses. No timer is installed while OFF.
  try {
    if (config && config.proactiveEnabled === true) {
      ctx.inject(['sessionController', 'sessions'], (scoped) => {
        try {
          const dispose = registerProactive(scoped, () => config, dataDir)
          if (typeof dispose === 'function') keep(dispose)
        } catch { /* proactive unavailable */ }
      })
    }
  } catch { /* headless / no host services */ }

  return () => {
    for (const dispose of disposers) {
      try { if (typeof dispose === 'function') dispose() } catch { /* ignore */ }
    }
  }
}

// Cordis resolves the default export before reading plugin.inject, so attach
// the dependency map to the exported function as well (same as the reference).
export default Object.assign(apply, { inject, resolveStateDir, resolveDataDir })