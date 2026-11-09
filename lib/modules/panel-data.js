/** Host-side extras for the standalone instrument panel (task-202).
 *
 * /api/biocore/state stays the panel's single polling source; this module only
 * adds read-only, additive fields the instrument page needs:
 *   memory_recent      latest 10 memories (text <= 80 chars)
 *   relationship_metrics  closeness / trust / conflict / bids
 *   body_metrics       self_esteem / social_pain / energy / social
 *   narrative          intent label / length_factor / initiative / band
 *   motivation         one short Chinese sentence for "她现在的样子"
 *   history            24h v samples (from the session state file)
 *
 * It also owns the static file route /api/biocore-ui/ui/* used by the panel.
 * Everything here is read-only: no state writes and no switch logic.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

export const PANEL_ROUTE_PREFIX = '/api/biocore-ui/ui/'
/** Route mount: the webserver prefix matcher matches <mount>/..., no trailing slash. */
export const PANEL_MOUNT_PATH = '/api/biocore-ui/ui'
export const PANEL_DIR = fileURLToPath(new URL('../../ui', import.meta.url))
export const PANEL_FILES = Object.freeze({
  'panel.html': 'text/html; charset=utf-8',
  'panel.css': 'text/css; charset=utf-8',
  'panel.js': 'text/javascript; charset=utf-8',
  'panel-layout.js': 'text/javascript; charset=utf-8',
  'trace-view.js': 'text/javascript; charset=utf-8',
  'orb.png': 'image/png',
})
export const MEMORY_LIMIT = 10
export const MEMORY_TEXT_LIMIT = 80
export const HISTORY_LIMIT = 120
export const HISTORY_WINDOW_SECONDS = 24 * 3600

const MOTIVATIONS = {
  hold: '想先听着，不急着推进',
  approach: '想靠近一点，再问一句',
  withdraw: '想退半步，留点空间',
  probe: '想探一探，但还不确定',
  repair: '想把话圆回来',
  rest: '有点累，想安静待着',
  share: '想说说自己这边的事',
}

function num(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function round(value, digits) {
  const factor = Math.pow(10, digits === undefined ? 2 : digits)
  return Math.round(num(value) * factor) / factor
}

function text(value, limit) {
  return String(value == null ? '' : value).replace(/\s+/g, ' ').trim().slice(0, limit)
}

/** Latest memory previews, newest first, each <= 80 chars. */
export function memoryRecent(state) {
  const memory = state && typeof state.memory === 'object' && state.memory ? state.memory : {}
  const rows = Array.isArray(memory.recent) ? memory.recent.slice() : []
  rows.sort((left, right) => num(right && right.ts) - num(left && left.ts))
  const out = []
  for (const item of rows) {
    if (!item || typeof item !== 'object') continue
    const preview = text(item.text_preview || item.text || '', MEMORY_TEXT_LIMIT)
    if (!preview) continue
    out.push({
      ts: Math.round(num(item.ts, 0) * 1000),
      score: round(item.score, 3),
      source: text(item.source, 24),
      kind: text(item.kind, 24),
      text: preview,
    })
    if (out.length >= MEMORY_LIMIT) break
  }
  return out
}

/** 24h v samples from the saved state history (oldest first, bounded). */
export function moodHistory(state) {
  const core = state && typeof state === 'object' ? state : {}
  const now = num(core.now, 0)
  const rows = Array.isArray(core.history) ? core.history : []
  const out = []
  for (const item of rows) {
    if (!item || typeof item !== 'object') continue
    const ts = num(item.ts, 0)
    if (!ts) continue
    if (now > 0 && ts < now - HISTORY_WINDOW_SECONDS) continue
    out.push({
      ts: round(ts, 3),
      v: round(item.v, 4),
      tension: round(item.tension, 4),
      warmth: round(item.warmth, 4),
      heaviness: round(item.heaviness, 4),
      clarity: round(item.clarity, 4),
    })
    if (out.length >= HISTORY_LIMIT) break
  }
  return out
}

/** Additive /state fields consumed by ui/panel.js only. */
export function buildPanelExtras(state) {
  const core = state && typeof state === 'object' ? state : {}
  const inner = core.core && typeof core.core === 'object' ? core.core : {}
  const rel = core.relationship && typeof core.relationship === 'object' ? core.relationship : {}
  const lastTurn = core.last_turn && typeof core.last_turn === 'object' ? core.last_turn : {}
  const turn = lastTurn.turn && typeof lastTurn.turn === 'object' ? lastTurn.turn : {}
  const labels = inner.labels && typeof inner.labels === 'object' ? inner.labels : {}
  const intent = text(lastTurn.intent || labels.intent || '', 24)
  return {
    memory_recent: memoryRecent(core),
    relationship_metrics: {
      closeness: round(rel.closeness, 2),
      trust: round(rel.trust, 2),
      conflict: round(rel.conflict, 2),
      bids: round(rel.bids, 2),
    },
    body_metrics: {
      self_esteem: round(inner.self_esteem, 2),
      social_pain: round(inner.social_pain, 2),
      energy: round(inner.energy, 2),
      social: round(inner.social, 2),
    },
    narrative: {
      intent,
      length_factor: round(turn.length_factor, 3),
      initiative: round(turn.initiative, 3),
      metacog_band: text(lastTurn.metacog_band, 8),
      allow_correction: turn.allow_correction === true,
    },
    motivation: MOTIVATIONS[intent] || '安静待着，等你开口',
    history: moodHistory(core),
  }
}

function sendJson(res, code, payload) {
  res.writeHead(code, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
  })
  res.end(JSON.stringify(payload))
}

/** GET/HEAD /api/biocore-ui/ui/<panel.html|panel.css|panel.js> */
export function servePanelPage(req, res) {
  const method = String((req && req.method) || 'GET').toUpperCase()
  if (method !== 'GET' && method !== 'HEAD') {
    return sendJson(res, 405, { ok: false, reason: 'method_not_allowed' })
  }
  let pathname = ''
  try {
    pathname = new URL(String((req && req.url) || ''), 'http://local').pathname
  } catch {
    pathname = ''
  }
  if (!pathname.startsWith(PANEL_ROUTE_PREFIX)) {
    return sendJson(res, 404, { ok: false, reason: 'not_found' })
  }
  const name = pathname.slice(PANEL_ROUTE_PREFIX.length)
  if (!PANEL_FILES[name]) return sendJson(res, 404, { ok: false, reason: 'not_found' })
  let content
  try {
    content = readFileSync(join(PANEL_DIR, name))
  } catch {
    return sendJson(res, 404, { ok: false, reason: 'not_found' })
  }
  if (name === 'panel.html') {
    // 版本号只有一个来源：package.json。面板上的 BUILD / vX.Y.Z 都由这里替换。
    try {
      const pkg = JSON.parse(readFileSync(join(PANEL_DIR, '..', 'package.json'), 'utf8'))
      const version = String((pkg && pkg.version) || '').trim() || 'unknown'
      content = Buffer.from(String(content).split('__BIOCORE_VERSION__').join(version), 'utf8')
    } catch { /* 读不到就保持占位符，不假装有版本号 */ }
  }
  res.writeHead(200, {
    'content-type': PANEL_FILES[name],
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'content-length': content.length,
  })
  if (method === 'HEAD') return res.end()
  return res.end(content)
}
