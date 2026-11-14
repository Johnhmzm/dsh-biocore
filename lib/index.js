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
  sessionStateFile,
  STATE_LIMIT,
} from './modules/state.js'
import { compilePersona, configureBridge, configureCore, ensureState, memoryTool, runTurn, runtimeAction } from './py-bridge.js'
import { rerankConfig } from './modules/python-env.js'
import { SWITCH_ROUTE_PATH, handleSwitchRoute, readSwitches } from './modules/switch.js'
import { TRACE_ROUTE_PATH, defaultStoreDir, handleTraceRoute } from './modules/trace.js'
import { SERIES_ROUTE_PATH, handleSeriesRoute } from './modules/series.js'
import { readCacheStats } from './modules/cache-stats.js'
import { startCoreWorker, stopCoreWorker } from './core-worker.js'
import { runAutonomyTick } from './modules/autonomy.js'
import { drainOutbox } from './modules/standby.js'
import { queueSessionTurn } from './modules/turn-queue.js'
import { makeCoreSessionListener, registerCoreTurn } from './modules/core-turn.js'
import { migrateLegacyPluginData, PLUGIN_DATA_DIR, resolveDataDir } from './modules/data-dir.js'
import {
  PANEL_MOUNT_PATH,
  PANEL_ROUTE_PREFIX,
  servePanelPage,
} from './modules/panel-data.js'
import { resolveMemoryDir } from './modules/memory-store.js'
import { registerMemoryCapture } from './modules/memory-capture.js'
import { registerMemorySearchTool } from './modules/memory-search-tool.js'
import { registerCompactionScan } from './modules/compaction-scan.js'
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
/** 面板专用动作白名单（新增需评审）：只转发这三个，其余一律 400。 */
export const PANEL_ACTION_ALLOWLIST = ['settings_get', 'settings_set', 'test_connection',
  // 会话总览：timeline 免费、digest 一次模型调用（面板「工具」页的按钮走这里）
  'overview',
  // 向量体检 / 补齐：只读计数 + 只调 embedding（不调 chat）
  'vectors_check', 'vectors_backfill']
export const PANEL_ACTION_ROUTE_PATH = '/api/biocore/action'
export const SETTINGS_FILE_NAME = 'config.json'
export const COMMAND_NAME = 'bio'
export const PERSONA_TEXT_LIMIT = 20000
export const DEFAULT_STATE_DIR = PLUGIN_DATA_DIR
export const inject = ['webServer', 'tools']
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
  return { mode: 'auto', base_url: '', model: '', api_key: '' }
}

function normalizeEmbedding(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const rawMode = String(source.mode || 'auto').trim()
  return {
    mode: EMBEDDING_MODES.includes(rawMode) ? rawMode : null,
    base_url: String(source.base_url || '').trim().slice(0, 2000),
    model: String(source.model || '').trim().slice(0, 200),
    api_key: typeof source.api_key === 'string' ? source.api_key.trim().slice(0, 4000) : '',
  }
}

export const AUTONOMY_DEFAULTS = Object.freeze({
  max_actions_per_hour: 20,
  max_sends_per_hour: 5,
  max_sends_per_day: 30,
  quiet_hours: [],
  auto_decay_on_silence: true,
})

export const RECALL_INJECT_MIN = 500
export const RECALL_INJECT_MAX = 200000
export const RECALL_DELIVERY_MODES = ['event_rank', 'sentence', 'event']
export const RECALL_EVENT_PRESET_MIN = 1
export const RECALL_EVENT_PRESET_MAX = 20
export const RECALL_EVENT_PRESET_DEFAULT = 8

/** recall 配置：事件预算（主）、注入预算（字符）与交付方式。非法值必须响亮失败。 */
export function normalizeRecall(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const raw = source.inject_chars
  let chars = Number(raw)
  if (!Number.isFinite(chars)) {
    throw new Error('recall.inject_chars 必须是数字，合法区间 ' + RECALL_INJECT_MIN + '–' + RECALL_INJECT_MAX)
  }
  chars = Math.round(chars)
  if (chars < RECALL_INJECT_MIN || chars > RECALL_INJECT_MAX) {
    throw new Error('recall.inject_chars 超出区间 ' + RECALL_INJECT_MIN + '–' + RECALL_INJECT_MAX + '：收到 ' + chars)
  }
  const delivery = String(source.delivery || 'event_rank').trim().toLowerCase()
  if (!RECALL_DELIVERY_MODES.includes(delivery)) {
    throw new Error('recall.delivery 只能是 ' + RECALL_DELIVERY_MODES.join(' / ') + '：收到 ' + delivery)
  }
  // 事件预算：缺省/清空回 8，越界夹紧，非整数拒绝（浮点和小数串都不收）
  const rawPreset = source.event_preset
  let preset = RECALL_EVENT_PRESET_DEFAULT
  if (rawPreset !== undefined && rawPreset !== null && String(rawPreset).trim() !== '') {
    const number = Number(rawPreset)
    if (typeof rawPreset === 'boolean' || !Number.isInteger(number)) {
      throw new Error('recall.event_preset 必须是整数，合法区间 ' + RECALL_EVENT_PRESET_MIN + '–' + RECALL_EVENT_PRESET_MAX + '：收到 ' + rawPreset)
    }
    preset = Math.max(RECALL_EVENT_PRESET_MIN, Math.min(RECALL_EVENT_PRESET_MAX, number))
  }
  return { inject_chars: chars, delivery, event_preset: preset }
}

let _pluginVersion = ''
/** 版本号唯一来源：本包 package.json。读不到就返回空串，界面显示「—」，不编造。 */
export function readPluginVersion() {
  if (_pluginVersion) return _pluginVersion
  try {
    const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
    _pluginVersion = String((pkg && pkg.version) || '').trim()
  } catch { _pluginVersion = '' }
  return _pluginVersion
}

function readRecall(config) {
  try {
    return normalizeRecall((config && config.recall) || {})
  } catch {
    // 旧配置没有 recall 段时用默认值；非法值由保存路径拦住
    return { inject_chars: 14000, delivery: 'event_rank', event_preset: RECALL_EVENT_PRESET_DEFAULT }
  }
}

function normalizeAutonomy(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value)
    ? value : {}
  const int = (raw, fallback) => {
    const number = Number(raw)
    return Number.isFinite(number) ? Math.max(0, Math.round(number)) : fallback
  }
  return {
    max_actions_per_hour: int(source.max_actions_per_hour, AUTONOMY_DEFAULTS.max_actions_per_hour),
    max_sends_per_hour: int(source.max_sends_per_hour, AUTONOMY_DEFAULTS.max_sends_per_hour),
    max_sends_per_day: int(source.max_sends_per_day, AUTONOMY_DEFAULTS.max_sends_per_day),
    quiet_hours: Array.isArray(source.quiet_hours)
      ? source.quiet_hours.map((item) => String(item).slice(0, 12)).slice(0, 12) : [],
    auto_decay_on_silence: source.auto_decay_on_silence !== false,
  }
}

const RERANK_DEFAULTS = Object.freeze({
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
    autonomy: normalizeAutonomy(source.autonomy),
    standby: source.standby && typeof source.standby === 'object' ? source.standby : {},
    standbyEnabled: source.standbyEnabled === true,
    sanitize_reply: source.sanitize_reply !== false,
    recall: readRecall(source),
  }
}

function writeFullConfig(file, config) {
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, JSON.stringify({
    embedding: config.embedding,
    rerank: config.rerank,
    expression: config.expression,
    autonomy: config.autonomy,
    sanitize_reply: config.sanitize_reply !== false,
    recall: config.recall,
  }, null, 2) + '\n', { encoding: 'utf8', mode: 0o600 })
  try { chmodSync(file, 0o600) } catch { /* Windows may ignore POSIX modes */ }
}

/** kind -> test_connection 的 target（旧路由只认 embedding/rerank，现补 summarizer）。 */
function panelTestTarget(kind) {
  const name = String(kind || '').toLowerCase()
  if (name === 'summarizer' || name === 'embedding' || name === 'rerank') return name
  return ''
}

/** 旧 settings 接口的写入体 -> settings_set 的一次调用（只做映射，不复制校验）。 */
function panelSettingsPatch(body) {
  const patch = { model: {}, settings: {}, extra: {} }
  for (const name of ['summarizer', 'embedding', 'rerank']) {
    if (!body[name] || typeof body[name] !== 'object') continue
    const block = {}
    for (const field of ['base_url', 'model', 'api_key', 'key', 'mode', 'enabled', 'top_n',
      'timeout', 'thinking']) {
      if (field in body[name]) block[field] = body[name][field]
    }
    patch.model[name] = block
  }
  const recall = body.recall && typeof body.recall === 'object' ? body.recall : {}
  for (const key of ['tiered_delivery_on', 'portrait_on', 'consolidate_on',
    'event_preset', 'window', 'portrait_top']) {
    if (key in recall) patch.settings[key] = recall[key]
  }
  if (typeof body.detail_shape === 'boolean') patch.settings.detail_shape = body.detail_shape
  for (const key of ['expression', 'autonomy', 'sanitize_reply']) {
    if (key in body) patch.extra[key] = body[key]
  }
  return patch
}

/** GET/POST /api/biocore/settings: embedding + rerank + expression. */
export async function handleSettings(req, res, dataDir, options = {}) {
  const paths = settingsPaths(dataDir)
  const method = String((req && req.method) || 'GET').toUpperCase()
  if (method === 'GET') {
    const config = readFullConfig(paths.dir)
    const legacy = {
      ok: true,
      embedding: maskedEmbedding(config.embedding),
      rerank: maskedRerank(config.rerank),
      expression: config.expression,
      autonomy: config.autonomy,
      recall: readRecall(config),
      plugin_version: readPluginVersion(),
      sanitize_reply: config.sanitize_reply !== false,
    }
    // 统一语义：读也走 settings_get，旧字段一个不删，新字段追加在后面。
    let fresh = null
    try {
      fresh = await (options.runAction || runtimeAction)('settings_get',
        { store: options.store || join(String(dataDir), 'memory') })
    } catch { fresh = null }
    if (!fresh || fresh.ok !== true) return sendJson(res, 200, legacy)
    const merged = Object.assign({}, legacy, {
      models: fresh.models,
      settings: fresh.settings,
      status: fresh.status,
      summarizer: (fresh.models || {}).summarizer,
    })
    if (fresh.settings && 'event_preset' in fresh.settings) {
      merged.recall = Object.assign({}, legacy.recall, { event_preset: fresh.settings.event_preset })
    }
    return sendJson(res, 200, merged)
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
  const hasAutonomy = body.autonomy && typeof body.autonomy === 'object'
  const hasRecall = body.recall && typeof body.recall === 'object'
  if (!hasEmbedding && !hasRerank && !hasExpression && !hasSanitize
    && !hasAutonomy && !hasRecall) {
    return sendJson(res, 400, { ok: false, reason: 'no_settings' })
  }
  // 统一语义：写也只走 settings_set（一次保存 = 一次备份 + 一次写入）。
  // 不再返回 restart_required：保存即生效，旧字段已删除。
  if (hasEmbedding && !normalizeEmbedding(body.embedding).mode) {
    return sendJson(res, 400, { ok: false, reason: 'bad_mode', allowed: Array.from(EMBEDDING_MODES) })
  }
  // 只带 event_preset 这类补丁时不该被旧的全量校验挡住：有预算/交付字段才校验。
  const recallTouchesBudget = hasRecall
    && ('inject_chars' in body.recall || 'delivery' in body.recall)
  if (recallTouchesBudget) {
    try {
      normalizeRecall(body.recall)
    } catch (error) {
      return sendJson(res, 400, {
        ok: false,
        reason: 'bad_recall',
        detail: String((error && error.message) || error),
        allowed: { inject_chars: [RECALL_INJECT_MIN, RECALL_INJECT_MAX], delivery: RECALL_DELIVERY_MODES },
      })
    }
  }
  // 面板存的打码密钥不覆盖真值：去掉以 • 开头或等于既有打码值的 key
  const sanitized = JSON.parse(JSON.stringify(body))
  for (const name of ['summarizer', 'embedding', 'rerank']) {
    const block = sanitized[name]
    if (!block || typeof block !== 'object') continue
    for (const keyName of ['api_key', 'key']) {
      const value = String(block[keyName] || '')
      if (value && value.startsWith('••')) delete block[keyName]
    }
    if (block.api_key === undefined && block.key === undefined) {
      // 保留既有密钥：不传该字段即可
    }
  }
  let applied = null
  try {
    applied = await (options.runAction || runtimeAction)('settings_set',
      { patch: panelSettingsPatch(sanitized), store: options.store || join(String(dataDir), 'memory') })
  } catch (error) {
    return sendJson(res, 500, { ok: false, reason: 'write_failed',
      detail: String((error && error.message) || error) })
  }
  if (!applied || applied.ok !== true) {
    return sendJson(res, 400, { ok: false, reason: (applied && (applied.code || applied.reason)) || 'write_failed',
      warned: (applied && applied.warned) || [] })
  }
  const fresh = readFullConfig(paths.dir)
  return sendJson(res, 200, {
    ok: true,
    embedding: maskedEmbedding(fresh.embedding),
    rerank: maskedRerank(fresh.rerank),
    expression: fresh.expression,
    autonomy: fresh.autonomy,
    recall: readRecall(fresh),
    sanitize_reply: fresh.sanitize_reply !== false,
    models: applied.models,
    settings: applied.settings,
    warned: applied.warned || [],
    hint: '已保存，立即生效（不再需要重启）',
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
  const target = panelTestTarget(kind)
  if (!target) {
    return sendJson(res, 400, { ok: false, reason: 'bad_kind',
      allowed: ['embedding', 'rerank', 'summarizer'] })
  }
  // 统一到 test_connection（三种 target 全认；返回 ok / ms / code）。
  // 保留旧字段 kind / elapsed_ms，方便老调用方继续读。
  const result = await (opts.runAction || runtimeAction)('test_connection',
    { target, detail_shape: true,
      store: opts.store || join(String(dataDir), 'memory') })
  const merged = Object.assign({}, result, {
    kind,
    elapsed_ms: result && result.ms != null ? result.ms : undefined,
  })
  return sendJson(res, 200, merged)
}

/** task-223: the adapter only ships the config; the core decides everything. */
function coreConfigPayload(dataDir) {
  const config = readFullConfig(dataDir)
  let switches = {}
  try { switches = readSwitches(dataDir).switches } catch { switches = {} }
  let persona = ''
  try { persona = readPersonaText(dataDir) } catch { persona = '' }
  // Keys this host does not own (summarizer, recall, anything the core or a
  // human added) must survive a re-configure. Dropping them made capture raise
  // "no summarizer block" and memory vanished silently behind an adapter
  // fallback, so carry the previous file forward and overwrite only our keys.
  let carried = {}
  try {
    const previous = JSON.parse(readFileSync(join(dataDir, 'core_config.json'), 'utf8'))
    if (previous && typeof previous === 'object' && !Array.isArray(previous)) carried = previous
  } catch { carried = {} }
  return {
    ...carried,
    data_dir: dataDir,
    persona_text: persona,
    embedding: config.embedding,
    rerank: config.rerank,
    expression: config.expression,
    autonomy: config.autonomy,
    standby: {
      ...(config.standby && typeof config.standby === 'object' ? config.standby : {}),
      standby_enabled: config.standbyEnabled === true,
    },
    sanitize_reply: config.sanitize_reply !== false,
    switches,
    recall_mode: 'query',
    // 事件预算每次交付现读：只把这一项推给 core，其余 recall 键沿用文件里的
    recall: {
      ...(carried.recall && typeof carried.recall === 'object' ? carried.recall : {}),
      event_preset: config.recall.event_preset,
    },
  }
}

async function configureCoreRuntime(dataDir) {
  try { await configureCore(coreConfigPayload(dataDir)) } catch { /* core decides defaults */ }
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

export const STATS_ROUTE_PATH = '/api/biocore/stats'

/** task-223: /stats is a pure forward of the core reply (+ display-only cache). */
export async function handleStats(req, res, options) {
  const opts = options || {}
  const method = String((req && req.method) || 'GET').toUpperCase()
  if (method !== 'GET' && method !== 'HEAD') {
    return sendJson(res, 405, { ok: false, reason: 'method_not_allowed' })
  }
  let reply = null
  try {
    const runAction = typeof opts.runAction === 'function' ? opts.runAction : runtimeAction
    const store = (() => {
      try { return typeof opts.getStore === 'function' ? opts.getStore() : '' } catch { return '' }
    })()
    reply = await runAction('stats', { store })
  } catch { reply = null }
  const body = Object.assign({ ok: false, reason: 'core_unavailable' },
    reply && typeof reply === 'object' ? reply : {})
  // contract migration 1: the core computes cache stats; the legacy file is a
  // read-only fallback for one week, never a source of new numbers.
  try {
    if (!body.cache && typeof opts.dataDir === 'string' && opts.dataDir) {
      body.cache = readCacheStats(opts.dataDir)
    }
  } catch { /* display only */ }
  return sendJson(res, 200, body)
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
  // task-223: pure forward -- the core owns the whole /state payload.
  let reply = null
  try {
    const runAction = typeof opts.runAction === 'function' ? opts.runAction : runtimeAction
    reply = await runAction('state', { session_id: sessionId, store: opts.store })
  } catch { reply = null }
  const state = reply && reply.ok === true && reply.state
    && typeof reply.state === 'object' ? reply.state : null
  if (!state) return sendJson(res, 200, unavailable('core_unavailable'))
  if (!payloadWithinLimit(state)) {
    return sendJson(res, 200, unavailable('payload_too_large'))
  }
  return sendJson(res, 200, state)
}

export function apply(ctx, config) {
  const dataDir = resolveDataDir(config)
  configureBridge(dataDir)
  // task-223: one persistent core process for this plugin lifetime.
  try { startCoreWorker(dataDir) } catch (error) {
    try { console.error('__bioWorker start_failed ' + String(error)) } catch { /* ignore */ }
  }
  void configureCoreRuntime(dataDir)
  try { migrateLegacyPluginData(dataDir) } catch { /* best-effort data migration */ }
  try { migratePersonaToShared(dataDir) } catch { /* best-effort migration */ }
  const disposers = []
  const keep = (dispose) => {
    if (typeof dispose === 'function') disposers.push(dispose)
  }

  // 1) task-212: per-turn core milestones replace the adapter one_turn path.
  //    user/message -> turn_start (prompt_block for -190); assistant/message ->
  //    turn_end (gate + trace + relationship). All switches off: no calls.
  // 插件级 memoryEnabled 必须并进 switches：turn payload 的 memoryEnabled 只从这里来，
  // 否则关闭记忆只关掉了工具、捕获照记（ACTUAL-TASKS #53）。
  const memoryEnabled = !(config && config.memoryEnabled === false)
  const getSwitches = () => {
    let base = {}
    try { base = readSwitches(dataDir).switches || {} } catch { base = {} }
    const merged = Object.assign({}, base)
    if (!memoryEnabled) merged.memoryEnabled = false
    return merged
  }
  try {
    if (ctx && typeof ctx.on === 'function') {
      keep(ctx.on('session/event', makeCoreSessionListener({
        stateDir: dataDir,
        getSwitches,
        store: join(String(dataDir), 'memory'),
      })))
    }
  } catch { /* headless or older host */ }
  try {
    keep(registerCoreTurn(ctx, { getSwitches, store: join(String(dataDir), 'memory') }))
  } catch { /* prompt/assembly service unavailable */ }
  // 模型可见的记忆检索工具：走宿主 tools 通道（不在面板白名单里）。
  // 独立于上面的 try：registerCoreTurn 抛错时工具仍然要注册；没有 tools 服务则安静跳过。
  try {
    const disposeSearch = registerMemorySearchTool(ctx, () => join(String(dataDir), 'memory'))
    if (typeof disposeSearch === 'function') keep(disposeSearch)
  } catch { /* tools 通道不可用 */ }

  // 2) read-only endpoint.
  try {
    if (ctx && typeof ctx.inject === 'function') {
      ctx.inject(['webServer'], (webCtx) => {
        const webServer = webCtx && webCtx.webServer
        if (!webServer || typeof webServer.register !== 'function') return
        keep(webServer.register({
          kind: 'exact',
          path: STATE_ROUTE_PATH,
          handler: (req, res) => handleState(req, res, dataDir, { getSwitches, store: join(String(dataDir), 'memory') }),
        }))
        // task-221: 24-point mood/channel series (global core_state history).
        keep(webServer.register({
          kind: 'exact',
          path: SERIES_ROUTE_PATH,
          handler: (req, res) => handleSeriesRoute(req, res, {
            store: join(String(dataDir), 'memory'), dataDir,
          }),
        }))
        // embedding cache stats (action=stats; zeros when unavailable).
        keep(webServer.register({
          kind: 'exact',
          path: STATS_ROUTE_PATH,
          handler: (req, res) => handleStats(req, res, {
            getSwitches,
            getStore: () => join(String(dataDir), 'memory'),
            dataDir,
          }),
        }))
        // task-196: four switches persist to <dataDir>/flags.json (atomic write).
        keep(webServer.register({
          kind: 'exact',
          path: SWITCH_ROUTE_PATH,
          handler: async (req, res) => {
            const out = await handleSwitchRoute(req, res, { dataDir })
            if (String((req && req.method) || '').toUpperCase() === 'POST') {
              void configureCoreRuntime(dataDir)
            }
            return out
          },
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
        // 面板专用：白名单动作转发（只读 body → 转发 → 回 JSON；不缓存、不解释）。
        keep(webServer.register({
          kind: 'exact',
          path: PANEL_ACTION_ROUTE_PATH,
          handler: async (req, res) => {
            if (String((req && req.method) || 'GET').toUpperCase() !== 'POST') {
              return sendJson(res, 405, { ok: false, reason: 'method_not_allowed' })
            }
            let body = null
            try {
              body = await readJsonBody(req, 64 * 1024)
            } catch (error) {
              const tooBig = String((error && error.reason) || '') === 'text_too_long'
              return sendJson(res, tooBig ? 413 : 400,
                { ok: false, reason: tooBig ? 'payload_too_large' : 'bad_payload' })
            }
            const action = String((body && body.action) || '')
            if (!PANEL_ACTION_ALLOWLIST.includes(action)) {
              return sendJson(res, 400, { ok: false, reason: 'action not allowed' })
            }
            // 注入 store：面板只传业务参数，路径由宿主决定。
            // 不注入时 python 侧落到相对 'memory'，settings_get 会读不到
            // <dataDir>/core_config.json（旧路由自己注入过，所以只有新路由出过这个空壳）。
            const incoming = (body && typeof body.payload === 'object' && body.payload) || {}
            const payload = Object.assign({}, incoming,
              { store: incoming.store || join(String(dataDir), 'memory') })
            const reply = await runtimeAction(action, payload)
            return sendJson(res, 200, reply && typeof reply === 'object' ? reply : { ok: false })
          },
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
  try {
    // Normally capture happens inside core turn_start/turn_end. The standalone
  // session/event capture listener stays available but is opt-in.
  if (config && config.memoryCaptureEnabled === true) {
    const capture = registerMemoryCapture(ctx, () => config, memoryDir, {})
    if (capture) keep(capture.dispose)
  }
  } catch { /* memory capture unavailable */ }
  // 压缩记录补扫（与 memory-capture 并列）：事件投递不可靠，就从会话日志补。
  // 捕获靠事件、补扫不靠事件，两条路各走各的；补扫 fire-and-forget，回合不等它。
  if (!config || config.compactionScan !== false) {
    try {
      const scan = registerCompactionScan(ctx, () => config, memoryDir)
      if (scan) keep(scan.dispose)
    } catch { /* 补扫不可用不该拖累宿主 */ }
  }
  try {
    const tools = registerMemoryTools(ctx, () => config, memoryDir, {
      getRerankConfig: () => rerankConfig(dataDir),
    })
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

  // task-222: autonomy tick -- the core decides, this loop only executes.
  if (!config || config.autonomyEnabled !== false) {
    let timer = null
    const tick = async () => {
      try {
        await runAutonomyTick(ctx, {
          runAction: runtimeAction,
          store: join(String(dataDir), 'memory'),
          dataDir,
          sessionId: 'autonomy',
          memoryTool,
          rerankConfig: rerankConfig(dataDir),
        })
      } catch { /* an autonomy tick never disturbs a turn */ }
    }
    timer = setInterval(() => { void tick() }, 5 * 60 * 1000)
    if (typeof timer.unref === 'function') timer.unref()
    keep(() => { if (timer !== null) { clearInterval(timer); timer = null } })
  }

  // task-215: standby is off by default; when on, the core writes the outbox
  // and this adapter only drains it into results.
  if (config && config.standbyEnabled === true) {
    let standbyTimer = null
    const standbyTick = async () => {
      try {
        await runtimeAction('standby_tick', {
          store: join(String(dataDir), 'memory'),
          data_dir: dataDir,
          session_id: 'standby',
          now: Date.now() / 1000,
        })
        drainOutbox(dataDir)
      } catch { /* standby never disturbs a turn */ }
    }
    standbyTimer = setInterval(() => { void standbyTick() }, 5 * 60 * 1000)
    if (typeof standbyTimer.unref === 'function') standbyTimer.unref()
    keep(() => { if (standbyTimer !== null) { clearInterval(standbyTimer); standbyTimer = null } })
  }

  return () => {
    for (const dispose of disposers) {
      try { if (typeof dispose === 'function') dispose() } catch { /* ignore */ }
    }
    try { stopCoreWorker() } catch { /* ignore */ }
  }
}

// Cordis resolves the default export before reading plugin.inject, so attach
// the dependency map to the exported function as well (same as the reference).
export default Object.assign(apply, { inject, resolveStateDir, resolveDataDir })