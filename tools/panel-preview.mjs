/** Local preview harness for ui/panel.html (dev only, not shipped).
 *
 *   node tools/panel-preview.mjs [port]
 *
 * Serves the panel files straight from ui/ and answers the documented BioCore
 * routes with sample data, so layout and states can be screenshotted in
 * headless Edge without a running DSH host.
 *
 * Modes (set by the panel.html query string):
 *   (default) live sample state
 *   empty=1   state unavailable, reason no_state_yet
 *   slow=1    state delayed 5s, used to capture the loading skeleton
 *
 * Routes: /shot?tab=&w=&h=[&empty=1]  iframe at an exact CSS size
 *         /ui-check                   DOM interaction smoke test
 */
import { createServer } from 'node:http'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const UI = join(ROOT, 'ui')
const PORT = Number(process.argv[2] || 8791)
const TYPES = {
  'panel.html': 'text/html; charset=utf-8',
  'panel.css': 'text/css; charset=utf-8',
  'panel.js': 'text/javascript; charset=utf-8',
  'panel-layout.js': 'text/javascript; charset=utf-8',
  'trace-view.js': 'text/javascript; charset=utf-8',
  'orb.png': 'image/png',
}

let stateMode = 'full'
const nowMs = Date.now()
const nowS = nowMs / 1000
const history = Array.from({ length: 42 }, (_, i) => {
  const t = nowS - (41 - i) * 1900
  return {
    ts: Math.round(t * 1000) / 1000,
    v: Math.round((Math.sin(i / 5.2) * 0.42 + 0.06) * 100) / 100,
    tension: Math.round((0.38 + Math.sin(i / 7) * 0.12) * 100) / 100,
    warmth: Math.round((0.58 + Math.cos(i / 9) * 0.1) * 100) / 100,
    heaviness: Math.round((0.34 + Math.sin(i / 11) * 0.09) * 100) / 100,
    clarity: Math.round((0.66 + Math.cos(i / 13) * 0.08) * 100) / 100,
  }
})

const memories = [
  '她说今天下雨，想在家写东西',
  '答应明天晚上聊一次，不要太晚',
  '不喜欢被连着问三个问题',
  '喜欢安静一点的语气，别太热闹',
  '提起过在写一篇关于记忆的稿子',
  '加班到很晚，说脑子是糊的',
  '想听一句不带建议的回应',
  '把「别急」当作口头禅',
].map((text, index) => ({
  ts: nowMs - index * 3_600_000 - 240_000,
  score: 0.72 - index * 0.03,
  source: 'chat',
  kind: 'fact',
  text,
}))

const state = {
  ok: true,
  available: true,
  adapter: { version: '3.0.0', level: 'full' },
  session_id: 'demo-session-0001',
  generated_at: nowMs,
  degraded: [],
  v: 0.28,
  intero: { tension: 0.41, warmth: 0.63, heaviness: 0.33, clarity: 0.68 },
  tone: { label: '温和' },
  relation: { stage: '熟悉' },
  autonomy: { label: '克制' },
  mood: { valence: 0.28, arousal: 0.55 },
  relationship_metrics: { closeness: 0.58, trust: 0.62, conflict: 0.12, bids: 3, conflict_count: 2 },
  body_metrics: { self_esteem: 0.55, social_pain: 0.12, energy: 0.6, social: 0.58 },
  narrative: { intent: 'hold', length_factor: 0.92, initiative: 0.38, metacog_band: '中', allow_correction: true },
  motivation: '想先听着，不急着推进',
  memory_recent: memories,
  memory_count: 37,
  recall_count: 128,
  proactive_sent_today: 2,
  history,
  errors: [],
}

const emptyState = {
  ok: true, available: false, reason: 'no_state_yet', session_id: 'demo-session-0001',
  generated_at: nowMs, degraded: ['state:no_state_yet'],
}

const settings = {
  ok: true,
  embedding: { mode: 'remote', base_url: 'https://api.siliconflow.cn/v1', model: 'BAAI/bge-m3', api_key: 'sk-••••••••••90' },
  rerank: { enabled: false, base_url: 'https://api.jina.ai/v1', model: 'jina-reranker-v2-base-multilingual', api_key: '', top_n: 20 },
  expression: { length_hint: false, length_target: 10, length_max: 30 },
  sanitize_reply: true,
}

const switches = {
  core_enabled: true,
  safety_enabled: true,
  boundary_enabled: false,
  crisis_safety_enabled: false,
  proactive_send: false,
  mood_recall: true,
  affect_encoding: true,
  memory_feedback: true,
  preference_drift: true,
}

const personaText = '她是安静、克制、先接情绪的人。说话短句，不用感叹号，不追问三次。在意分寸，也记得别人怕什么。'

const persona = {
  ok: true,
  text: personaText,
  updated_at: nowMs - 86_400_000,
  preview: [
    ['baseline_v', 0.18], ['baseline_warmth', 0.62], ['baseline_tension', 0.34],
    ['E', 0.71], ['A', 0.55], ['O', 0.48], ['C', 0.66], ['N', 0.29], ['length', 0.92],
  ].map(([key, value]) => ({ key, label: key, value })),
}

const trace = {
  ok: true,
  rows: [5, 4, 3, 2, 1].map((n) => ({
    ts: nowS - n * 420,
    state_delta: [
      { field: 'v', before: 0.24, after: 0.28 },
      { field: 'warmth', before: 0.59, after: 0.63 },
    ],
    intent: { kind: 'hold', weight: 0.62, reason: '对方在陈述，不需要推进' },
    length: { scale: 0.92, source: 'persona' },
    gate: { action: 'pass', rule: '', label: '' },
    memory: { recalled_ids: ['m-1042', 'm-1039'], captured_id: 'm-1051' },
  })),
}

function json(res, code, payload) {
  const body = Buffer.from(JSON.stringify(payload))
  res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
  res.end(body)
}

function shotPage(tab, w, h, empty, skeleton, scrollBottom, theme, press) {
  const html = '<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#e0e5ec;overflow:hidden}'
    + 'iframe{display:block;border:0;width:' + w + 'px;height:' + h + 'px}</style>'
    + '<iframe id="f" src="/api/biocore-ui/ui/panel.html?session=demo-session-0001&tab=' + tab
    + (empty ? '&empty=1' : '')
    + (theme ? '&theme=' + theme : '') + '"></iframe>'
  const scrollScript = scrollBottom
    ? '<script>window.addEventListener("load",function(){setTimeout(function(){'
      + 'var w=document.getElementById("f").contentWindow;'
      + 'w.scrollTo(0, w.document.documentElement.scrollHeight);'
      + '},900)})<\/script>'
    : ''
  const pressScript = press
    ? '<script>window.addEventListener("load",function(){setTimeout(function(){'
      + 'var d=document.getElementById("f").contentDocument;'
      + 'var b=d.getElementById("modelSave");'
      + 'if(b){b.classList.add("is-pressed");}'
      + '},900)})<\/script>'
    : ''
  if (!skeleton) return html + scrollScript + pressScript
  return html + scrollScript + '<script>window.addEventListener("load",function(){setTimeout(function(){'
    + 'document.getElementById("f").contentDocument.body.classList.add("is-booting");'
    + '},900)})<\/script>'
}

const checkScript = '<script>window.addEventListener("load",function(){setTimeout(function(){'
  + 'var out=[];try{'
  + 'var fr=document.getElementById("f");var d=fr.contentDocument;'
  + 'function q(s){return d.querySelectorAll(s)}'
  + 'out.push("cards="+q("[data-card-id]").length);'
  + 'out.push("dashVisible="+q("[data-card-id]:not(.tab-off)").length);'
  + 'd.getElementById("layoutFab").click();'
  + 'out.push("editing="+d.body.classList.contains("layout-editing"));'
  + 'd.querySelector("[data-card-id=mood] [data-layout-hide]").click();'
  + 'out.push("hidden="+d.querySelector("[data-card-id=mood]").classList.contains("is-hidden"));'
  + 'd.querySelector("[data-add-card=mood]").click();'
  + 'out.push("restored="+!d.querySelector("[data-card-id=mood]").classList.contains("is-hidden"));'
  + 'd.getElementById("layoutDone").click();'
  + 'd.querySelector(".rail-item[data-tab=settings]").click();'
  + 'out.push("tab="+d.body.dataset.tab);'
  + 'out.push("settingsVisible="+q("[data-card-id]:not(.tab-off)").length);'
  + 'out.push("switches="+q("#switches input[data-switch]").length);'
  + '}catch(e){out.push("ERROR "+e.message)}'
  + 'document.getElementById("out").textContent="RESULT "+out.join(" | ");'
  + '},1500)})<\/script>'

function checkPage() {
  return '<!doctype html><meta charset="utf-8">'
    + '<iframe id="f" src="/api/biocore-ui/ui/panel.html?session=demo-session-0001&tab=dashboard" style="width:1200px;height:800px;border:0"></iframe>'
    + '<pre id="out">pending</pre>' + checkScript
}

const server = createServer((req, res) => {
  const url = new URL(req.url || '/', 'http://127.0.0.1')
  const path = url.pathname

  if (path.startsWith('/api/biocore-ui/ui/')) {
    const name = path.slice('/api/biocore-ui/ui/'.length)
    if (name === 'panel.html') {
      const mode = url.searchParams.get('empty') === '1' ? 'empty'
        : (url.searchParams.get('slow') === '1' ? 'slow' : 'full')
      stateMode = mode
      console.log('[page] mode=' + mode)
    }
    if (!TYPES[name]) return json(res, 404, { ok: false })
    try {
      const buf = readFileSync(join(UI, name))
      res.writeHead(200, { 'content-type': TYPES[name], 'cache-control': 'no-store' })
      return res.end(buf)
    } catch { return json(res, 404, { ok: false }) }
  }

  if (path === '/shot') {
    const tab = url.searchParams.get('tab') || 'dashboard'
    const w = Number(url.searchParams.get('w') || 430)
    const h = Number(url.searchParams.get('h') || 932)
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    return res.end(shotPage(tab, w, h, url.searchParams.get('empty') === '1',
      url.searchParams.get('skeleton') === '1', url.searchParams.get('scroll') === 'bottom',
      url.searchParams.get('theme') || '', url.searchParams.get('press') === '1'))
  }
  if (path === '/overflow') {
    const widths = [390, 768, 1280]
    const html = '<!doctype html><meta charset="utf-8"><pre id="out">pending</pre>'
      + widths.map((w) => '<iframe data-w="' + w + '" src="/api/biocore-ui/ui/panel.html?session=demo-session-0001&tab=settings" style="width:' + w + 'px;height:600px;border:0"></iframe>').join('')
      + '<script>window.addEventListener("load",function(){setTimeout(function(){'
      + 'var out=[];Array.prototype.forEach.call(document.querySelectorAll("iframe"),function(f){'
      + 'var d=f.contentDocument;var w=d.documentElement;'
      + 'out.push("w="+f.dataset.w+" scroll="+w.scrollWidth+" client="+w.clientWidth+" over="+(w.scrollWidth>w.clientWidth+1));});'
      + 'document.getElementById("out").textContent="RESULT "+out.join(" | ");},1500)})<\/script>'
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    return res.end(html)
  }
  if (path === '/ui-check') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    return res.end(checkPage())
  }

  if (path === '/api/biocore/state') {
    console.log('[state] mode=' + stateMode + ' ua=' + String(req.headers['user-agent'] || '').slice(0, 24))
    if (stateMode === 'slow') return setTimeout(() => json(res, 200, state), 60_000)
    if (stateMode === 'empty') return json(res, 200, emptyState)
    return json(res, 200, state)
  }
  if (path === '/api/biocore/series') {
    if (stateMode === 'empty') return json(res, 200, { ok: true, available: false, window: '24h', points: [], series: {} })
    return json(res, 200, { ok: true, available: true, window: '24h', points: history.map((h) => ({ ts: h.ts * 1000, v: h.v })) })
  }
  if (path === '/api/biocore/stats') {
    if (stateMode === 'empty') return json(res, 200, { ok: true, cache: { turns: 0 }, embed: { hit: 0, miss: 0 } })
    return json(res, 200, { ok: true, cache: { turns: 41, hit_rate: 0.63, cache_read: 12_480, prompt_tokens: 19_800 }, embed: { hit: 82, miss: 18 } })
  }
  if (path === '/api/biocore/trace') {
    return json(res, 200, stateMode === 'empty' ? { ok: true, rows: [] } : trace)
  }
  if (path === '/api/biocore/switch') return json(res, 200, { ok: true, switches, namespace: 'flags.json', revision: 7 })
  if (path === '/api/biocore/persona') return json(res, 200, persona)
  if (path === '/api/biocore/persona/apply') {
    return json(res, 200, { ok: true, applied: false, reason: 'not_applied', has_backup: false })
  }
  if (path === '/api/biocore/settings') return json(res, 200, settings)
  if (path === '/api/biocore/settings/test') return json(res, 200, { ok: true, elapsed_ms: 212 })
  if (path === '/' || path === '') {
    res.writeHead(302, { location: '/api/biocore-ui/ui/panel.html?session=demo-session-0001' })
    return res.end()
  }
  return json(res, 404, { ok: false, reason: 'not_found' })
})

server.listen(PORT, '127.0.0.1', () => {
  console.log('panel preview on 127.0.0.1:' + PORT)
})
