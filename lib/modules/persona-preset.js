/** BioCore persona -> persistent DSH agent-preset persona (task-214).
 *
 * Research summary (see README / docs):
 * - Agent presets are @deepseek-ai/dsh-agent-preset rows; each preset's plugin
 *   list carries a @deepseek-ai/dsh-persona child row whose config.prefix is
 *   rendered as the scoped deployment:persona-prefix section (shadowing the
 *   deployment-wide dsh-system-prompt personaPrefix).
 * - The supported persistent edit path is ctx.configEditor.edit(entry, change):
 *   it validates the raw config, rewrites the profile cordis.patch.yml with a
 *   file lock and YAML/!!js preservation, and reconciles through Loader.
 * - Editing a preset affects subsequently created sessions (live agents keep
 *   their retained preset revision).
 *
 * This module only computes the edit; the host service performs the write.
 */
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

export const PRESET_PLUGIN_NAME = '@deepseek-ai/dsh-agent-preset'
export const PERSONA_PLUGIN_NAME = '@deepseek-ai/dsh-persona'
export const PERSONA_ROW_ID = 'biocore-persona'
export const STATUS_FILE = 'persona_preset.json'
export const BACKUP_FILE = 'persona_preset_backup.json'
export const PERSONA_TEMPLATE_RE = /\{\{|\}\}/

export function personaPathsDir(dataDir) {
  return String(dataDir || '').trim()
}

export function statusPath(dataDir) { return join(personaPathsDir(dataDir), STATUS_FILE) }
export function backupPath(dataDir) { return join(personaPathsDir(dataDir), BACKUP_FILE) }

function readJson(path) {
  try { return JSON.parse(readFileSync(path, 'utf8')) } catch { return null }
}

function writeJson(path, value) {
  mkdirSync(String(path).replace(/[\\/][^\\/]+$/, '') || '.', { recursive: true })
  writeFileSync(path, JSON.stringify(value, null, 2) + '\n', 'utf8')
}

export function personaHash(text) {
  return createHash('sha256').update(String(text || '')).digest('hex').slice(0, 16)
}

/** Locate the persona child row in one preset's plugin list. */
export function findPersonaRow(plugins) {
  const rows = Array.isArray(plugins) ? plugins : []
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index]
    if (!row || typeof row !== 'object') continue
    if (row.name === PERSONA_PLUGIN_NAME) return { index, row }
  }
  return { index: -1, row: null }
}

export function personaPrefixOf(plugins) {
  const found = findPersonaRow(plugins)
  if (!found.row) return ''
  const config = found.row.config && typeof found.row.config === 'object' ? found.row.config : {}
  return String(config.prefix || '')
}

/** Return a new preset config whose persona prefix is the supplied text. */
export function withPersonaPrefix(current, text) {
  const base = current && typeof current === 'object' ? current : {}
  const next = Object.assign({}, base)
  const plugins = Array.isArray(base.plugins) ? base.plugins.map((row) => (
    row && typeof row === 'object' ? Object.assign({}, row) : row
  )) : []
  const found = findPersonaRow(plugins)
  const value = String(text || '').trim()
  if (found.row) {
    const config = found.row.config && typeof found.row.config === 'object'
      ? Object.assign({}, found.row.config) : {}
    config.prefix = value
    plugins[found.index] = Object.assign({}, found.row, { config })
  } else {
    plugins.unshift({
      id: PERSONA_ROW_ID,
      name: PERSONA_PLUGIN_NAME,
      config: { prefix: value },
    })
  }
  next.plugins = plugins
  return next
}

/** Find the presets service default id (selectedDefault ?? default). */
export function defaultPresetId(presets) {
  try {
    const id = presets && presets.defaultId
    if (typeof id === 'string' && id.trim()) return id.trim()
  } catch { /* fall through */ }
  return 'standard'
}

/** Find the Loader entry that declares one preset. */
export function findPresetEntry(editor, presetId) {
  const entries = editor && typeof editor.entries === 'function' ? editor.entries() : []
  const wanted = String(presetId || '').trim()
  const candidates = entries.filter((entry) => {
    const options = entry && entry.options ? entry.options : {}
    const config = options.config && typeof options.config === 'object' ? options.config : {}
    return options.name === PRESET_PLUGIN_NAME && String(config.id || '') === wanted
  })
  if (candidates.length > 0) return candidates[0]
  return entries.find((entry) => {
    const options = entry && entry.options ? entry.options : {}
    return options.name === PRESET_PLUGIN_NAME && options.id === 'preset-' + wanted
  }) || null
}

/** Find a Loader entry by plugin package name. */
export function findPluginEntry(editor, packageName) {
  const entries = editor && typeof editor.entries === 'function' ? editor.entries() : []
  return entries.find((entry) => {
    const options = entry && entry.options ? entry.options : {}
    return options.name === packageName
  }) || null
}

/**
 * task-214: turn OFF the BioCore stack's per-turn persona injection.
 *
 * The reference dsh-biocore-memory plugin injects a persona block from
 * config.personaFile on every turn; once the persona lives in the DSH preset,
 * that injection is a duplicate. This edits the profile row through the same
 * configEditor (no source change, rollback restores the value).
 */
export async function disablePersonaInjection(editor, dataDir, backup, now) {
  const out = { disabled: false, reason: 'plugin_absent', entry_id: '', persona_file: '' }
  const entry = findPluginEntry(editor, 'dsh-biocore-memory')
  if (!entry) return out
  const config = entry.options && entry.options.config ? entry.options.config : {}
  const personaFile = String(config.personaFile || '')
  out.entry_id = String((entry.options && entry.options.id) || '')
  out.persona_file = personaFile
  if (!personaFile.trim()) {
    out.disabled = true
    out.reason = 'already_off'
    return out
  }
  if (backup && !backup.injection_backup) {
    backup.injection_backup = {
      entry_id: out.entry_id,
      persona_file: personaFile,
      created_at: Number(now) || Date.now(),
    }
    try {
      const existing = readJson(backupPath(dataDir))
      const merged = Object.assign({}, existing || backup, { injection_backup: backup.injection_backup })
      writeJson(backupPath(dataDir), merged)
    } catch { /* backup best-effort */ }
  }
  try {
    await editor.edit(entry, (current) => Object.assign({}, current || {}, { personaFile: '' }))
    out.disabled = true
    out.reason = 'disabled'
  } catch (error) {
    out.reason = 'edit_failed'
    out.detail = String(error && error.message || error)
  }
  return out
}

/** Restore the personaFile captured by disablePersonaInjection. */
export async function restorePersonaInjection(editor, backup) {
  const saved = backup && backup.injection_backup
  if (!saved) return { restored: false, reason: 'no_injection_backup' }
  const entries = editor && typeof editor.entries === 'function' ? editor.entries() : []
  const entry = entries.find((item) => String((item.options && item.options.id) || '') === String(saved.entry_id || ''))
    || findPluginEntry(editor, 'dsh-biocore-memory')
  if (!entry) return { restored: false, reason: 'plugin_absent' }
  try {
    await editor.edit(entry, (current) => Object.assign({}, current || {}, {
      personaFile: String(saved.persona_file || ''),
    }))
    return { restored: true, reason: 'restored' }
  } catch (error) {
    return { restored: false, reason: 'edit_failed', detail: String(error && error.message || error) }
  }
}

/** Read the persisted status (never throws). */
export function readStatus(dataDir) {
  const status = readJson(statusPath(dataDir))
  const backup = readJson(backupPath(dataDir))
  const out = status && typeof status === 'object' ? status : {}
  return {
    applied: out.applied === true,
    preset_id: String(out.preset_id || ''),
    entry_id: String(out.entry_id || ''),
    patch_path: String(out.patch_path || ''),
    chars: Number(out.chars || 0),
    persona_hash: String(out.persona_hash || ''),
    applied_at: Number(out.applied_at || 0),
    injection: String(out.injection || ''),
    injection_disabled: out.injection === 'off',
    has_backup: Boolean(backup && backup.config),
    backup_path: backup ? backupPath(dataDir) : '',
  }
}

/** Compare the persisted hash with the current preset config (drift check). */
export function currentMatches(editor, presetId, expectedHash) {
  try {
    const entry = findPresetEntry(editor, presetId)
    if (!entry) return false
    const config = entry.options && entry.options.config ? entry.options.config : {}
    return personaHash(personaPrefixOf(config.plugins)) === String(expectedHash || '')
  } catch {
    return false
  }
}

/** GET payload: applied, where, chars, drift and backup facts. */
export function personaPresetStatus(editor, presets, dataDir) {
  const status = readStatus(dataDir)
  const presetId = status.preset_id || defaultPresetId(presets)
  const currentDefault = defaultPresetId(presets)
  const matches = status.applied
    ? currentMatches(editor, presetId, status.persona_hash)
    : false
  return Object.assign({}, status, {
    ok: true,
    preset_id: presetId,
    default_preset_id: currentDefault,
    is_default_preset: status.applied && presetId === currentDefault,
    matches_preset: matches,
    editor_available: Boolean(editor && typeof editor.edit === 'function'),
    reason: status.applied
      ? (matches ? 'applied' : 'drift')
      : 'not_applied',
  })
}

/** Placeholder syntax used by AstrBot-style persona cards. */
export const PLACEHOLDER_RE = /\{\{\s*([^{}]*?)\s*\}\}/g

/**
 * Convert AstrBot persona placeholders into literal dsh-persona-safe text.
 * - {{user}} -> 「主管」
 * - {{char}} -> 「<charName>」 (resolved from the persona title, fallback 「她」)
 * - any other {{xxx}} -> xxx (braces removed)
 * A leftover {{ or }} after conversion is rejected as template_unsafe.
 */
export function sanitizePersonaTemplate(text, options) {
  const opts = options || {}
  const charName = String(opts.charName || '').trim() || '她'
  const raw = String(text == null ? '' : text)
  let replaced = 0
  const sanitized = raw.replace(PLACEHOLDER_RE, (_match, inner) => {
    replaced += 1
    const key = String(inner == null ? '' : inner).trim().toLowerCase()
    if (key === 'user') return '「主管」'
    if (key === 'char') return '「' + charName + '」'
    return String(inner == null ? '' : inner).trim()
  })
  const out = {
    raw_chars: raw.length,
    sanitized_chars: sanitized.length,
    replaced_placeholders: replaced,
    char_name: charName,
    text: sanitized,
  }
  if (sanitized.includes('{{') || sanitized.includes('}}')) {
    return Object.assign(out, { ok: false, reason: 'template_unsafe' })
  }
  if (!sanitized.trim()) {
    return Object.assign(out, { ok: false, reason: 'empty_persona' })
  }
  return Object.assign(out, { ok: true, reason: 'ok' })
}

/**
 * Resolve the character name from the persona card title.
 * Example: "# 角色扮演：安吉拉（Angela）" -> "安吉拉".
 * Falls back to 「她」 when no usable title is present.
 */
export function resolveCharName(dataDir) {
  const text = readPersonaText(dataDir)
  if (!text) return '她'
  try {
    const roleplay = /角色扮演[：:]\s*([^（(\n]+)/.exec(text)
    if (roleplay && roleplay[1] && roleplay[1].trim()) return roleplay[1].trim().slice(0, 24)
    const heading = /^#\s*(.+)$/m.exec(text)
    if (heading && heading[1]) {
      let title = String(heading[1]).trim()
      title = title.replace(/^[^：:]{1,12}[：:]\s*/, '')
      title = title.replace(/[（(].*$/, '').trim()
      if (title) return title.slice(0, 24)
    }
  } catch { /* fall through */ }
  return '她'
}

/**
 * Apply persona text to the default preset's persona row.
 * Requires the host configEditor service; returns {ok:false, reason} on refusal.
 */
export async function applyPersonaPreset(options) {
  const opts = options || {}
  const editor = opts.editor
  const dataDir = opts.dataDir
  const raw = String(opts.text || '')
  const now = Number(opts.now) || Date.now()
  if (!editor || typeof editor.edit !== 'function' || typeof editor.entries !== 'function') {
    return { ok: false, reason: 'host_unavailable' }
  }
  if (!raw.trim()) return { ok: false, reason: 'empty_persona', raw_chars: raw.length, sanitized_chars: 0 }
  const charName = String(opts.charName || '').trim() || resolveCharName(dataDir)
  const sanitized = sanitizePersonaTemplate(raw, { charName })
  if (!sanitized.ok) {
    return {
      ok: false,
      reason: sanitized.reason,
      detail: sanitized.reason === 'template_unsafe'
        ? 'persona still contains {{ or }} after placeholder conversion'
        : '',
      raw_chars: sanitized.raw_chars,
      sanitized_chars: sanitized.sanitized_chars,
      char_name: charName,
    }
  }
  const text = sanitized.text.trim()
  const presetId = defaultPresetId(opts.presets)
  const entry = findPresetEntry(editor, presetId)
  if (!entry) return { ok: false, reason: 'no_preset_entry', preset_id: presetId }
  const current = entry.options && entry.options.config ? entry.options.config : {}
  const next = withPersonaPrefix(current, text)

  // Keep the original preset config once; never overwrite an existing backup.
  let backup = readJson(backupPath(dataDir))
  if (!backup || !backup.config) {
    backup = {
      preset_id: presetId,
      entry_id: String((entry.options && entry.options.id) || ''),
      patch_path: String((editor.documentPath || '')),
      config: current,
      created_at: now,
    }
    try { writeJson(backupPath(dataDir), backup) } catch (error) {
      return { ok: false, reason: 'backup_failed', detail: String(error) }
    }
  }
  try {
    await editor.edit(entry, () => next)
  } catch (error) {
    return { ok: false, reason: 'edit_failed', detail: String(error && error.message || error) }
  }
  let injection = { disabled: false, reason: 'skipped' }
  try {
    injection = await disablePersonaInjection(editor, dataDir, backup, now)
  } catch (error) {
    injection = { disabled: false, reason: 'edit_failed', detail: String(error) }
  }
  const status = {
    applied: true,
    preset_id: presetId,
    entry_id: String((entry.options && entry.options.id) || ''),
    patch_path: String(editor.documentPath || ''),
    chars: text.length,
    raw_chars: sanitized.raw_chars,
    sanitized_chars: sanitized.sanitized_chars,
    replaced_placeholders: sanitized.replaced_placeholders,
    char_name: sanitized.char_name,
    persona_hash: personaHash(text),
    applied_at: now,
    injection: 'off',
    injection_reason: injection.reason,
    injection_entry_id: injection.entry_id || '',
    injection_persona_file: injection.persona_file || '',
    mode: 'preset-persona',
  }
  try { writeJson(statusPath(dataDir), status) } catch { /* status is best-effort */ }
  return Object.assign({ ok: true, reason: 'applied' }, status, {
    backup_path: backupPath(dataDir),
    preview: text.slice(0, 80),
    injection_disabled: injection.disabled === true,
    injection_detail: injection,
  })
}

/** Restore the preset config captured by the first successful apply. */
export async function rollbackPersonaPreset(options) {
  const opts = options || {}
  const editor = opts.editor
  const dataDir = opts.dataDir
  if (!editor || typeof editor.edit !== 'function' || typeof editor.entries !== 'function') {
    return { ok: false, reason: 'host_unavailable' }
  }
  const backup = readJson(backupPath(dataDir))
  if (!backup || !backup.config) return { ok: false, reason: 'no_backup' }
  const presetId = String(backup.preset_id || '')
  let entry = null
  try {
    const entries = editor.entries()
    entry = entries.find((item) => String((item.options && item.options.id) || '') === String(backup.entry_id || ''))
      || findPresetEntry(editor, presetId)
  } catch { entry = null }
  if (!entry) return { ok: false, reason: 'no_preset_entry', preset_id: presetId }
  try {
    await editor.edit(entry, () => backup.config)
  } catch (error) {
    return { ok: false, reason: 'edit_failed', detail: String(error && error.message || error) }
  }
  let injection = { restored: false, reason: 'no_injection_backup' }
  try {
    injection = await restorePersonaInjection(editor, backup)
  } catch (error) {
    injection = { restored: false, reason: 'edit_failed', detail: String(error) }
  }
  const status = Object.assign({}, readStatus(dataDir), {
    applied: false,
    rolled_back_at: Number(opts.now) || Date.now(),
    injection: 'on',
    injection_disabled: false,
    injection_reason: injection.reason,
  })
  try { writeJson(statusPath(dataDir), status) } catch { /* best-effort */ }
  return Object.assign({ ok: true, reason: 'rolled_back' }, status, {
    backup_path: backupPath(dataDir),
    injection_restored: injection.restored === true,
    injection_detail: injection,
  })
}

/** Read persona text from <dataDir>/persona_profile.json or persona.md. */
export function readPersonaText(dataDir) {
  const dir = personaPathsDir(dataDir)
  const profilePath = join(dir, 'persona_profile.json')
  try {
    const doc = JSON.parse(readFileSync(profilePath, 'utf8'))
    const text = String((doc && doc.persona) || '').trim()
    if (text) return text
  } catch { /* fall through */ }
  try {
    return String(readFileSync(join(dir, 'persona.md'), 'utf8')).trim()
  } catch {
    return ''
  }
}

export function applyRouteAvailable(ctx) {
  try {
    const editor = ctx && typeof ctx.get === 'function' ? ctx.get('configEditor') : null
    return Boolean(editor && typeof editor.edit === 'function')
  } catch {
    return false
  }
}

export { existsSync }
