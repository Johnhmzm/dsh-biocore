/** BioCore instrument panel runtime — vanilla JS, no dependencies.
 *
 * Data contract (unchanged): GET /api/biocore/state?session=<id> is the single
 * polling source; /series /stats /trace /persona /settings /switch are additive
 * reads plus the two write paths the panel is allowed to touch.
 *
 * Render layers, hard split:
 *   dashboard  — intero dial, episodic log, state, today, loops, dims, cache,
 *                system, 24h strip (all data only)
 *   memory / relation — data only
 *   settings — controls only
 *   tools — diagnostics only
 *
 * A missing field never throws: it renders the em dash and stays empty.
 */
(function () {
  'use strict'

  var QUERY = new URLSearchParams(window.location.search)
  var SESSION = (QUERY.get('session') || '').trim()
  var INITIAL_TAB = (QUERY.get('tab') || 'dashboard').trim()
  var THEME = (QUERY.get('theme') || '').trim()

  var PANEL_VERSION = '0.5.0'
  var POLL_MS = 5000
  var STATS_MS = 30000
  var PAGE_START = Date.now()
  var DASH = '—'
  var THRESHOLD = 0.65
  var DIAL_C = 2 * Math.PI * 72
  var LOG_ROWS = 6
  var TRACE_LIMIT = 5
  var LONG_PRESS_MS = 400
  var MOVE_CANCEL_PX = 8
  var CAPACITY = 200

  var SWITCHES = [
    { key: 'core_enabled', label: '核心', desc: '关掉后整条核心链不运行', group: '总开关' },
    { key: 'safety_enabled', label: '安全闸', desc: '可能拦截或改写回复', group: '安全' },
    { key: 'boundary_enabled', label: '底线', desc: '越界时先守边界', group: '安全' },
    { key: 'crisis_safety_enabled', label: '危机兜底', desc: '危机信号优先安全', group: '安全' },
    { key: 'proactive_send', label: '主动回复', desc: '允许私聊主动发消息', group: '主动' },
    { key: 'mood_recall', label: '情绪匹配召回', desc: '召回偏向当前心情', group: '学习' },
    { key: 'affect_encoding', label: '编码带情绪', desc: '写记忆时带情绪强度', group: '学习' },
    { key: 'memory_feedback', label: '回忆回灌情绪', desc: '召回的记忆小幅回灌心情', group: '学习' },
    { key: 'preference_drift', label: '主题偏好漂移', desc: '重复主题缓慢调整偏好', group: '学习' },
  ]

  var EMPTY_COPY = {
    no_state_yet: ['这个会话还没聊过', '发一条消息后就有状态'],
    request_failed: ['连不上宿主', '确认 DSH 与插件已加载，面板每 5 秒自动重试'],
    bad_state: ['状态文件坏了', '删掉该会话的状态文件后重试'],
  }

  var KNOWN_SINCE_KEY = 'biocore-known-since-v1'
  var MODEL_DRAFT_KEY = 'biocore-panel-model-draft-v1'

  var lastSuccess = 0
  var lastData = null
  var seriesPoints = null
  var switchBusy = false
  var personaText = ''
  var personaDirty = false
  var toastTimer = null
  var bootDone = false

  function $(id) { return document.getElementById(id) }

  /* ------------------------------------------------------------- theme --- */

  /** Host pins <html data-theme> in the parent window; ?theme= wins for previews;
   *  otherwise the CSS prefers-color-scheme rule decides. */
  function hostTheme() {
    try {
      var doc = window.parent && window.parent !== window ? window.parent.document : null
      var value = doc && doc.documentElement ? doc.documentElement.getAttribute('data-theme') : ''
      return value === 'light' || value === 'dark' ? value : ''
    } catch (error) { return '' }
  }

  function applyTheme() {
    var root = document.documentElement
    if (THEME === 'light' || THEME === 'dark') {
      root.setAttribute('data-theme', THEME)
      return
    }
    var theme = hostTheme()
    if (theme) root.setAttribute('data-theme', theme)
    else root.removeAttribute('data-theme')
  }

  function watchTheme() {
    applyTheme()
    try {
      var doc = window.parent && window.parent !== window ? window.parent.document : null
      if (!doc || !window.MutationObserver) return
      var observer = new MutationObserver(function () { applyTheme() })
      observer.observe(doc.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] })
    } catch (error) { /* cross-origin parent: the media query still applies */ }
  }

  /* ----------------------------------------------------------- helpers --- */

  function fmt(value, digits) {
    var number = Number(value)
    if (!Number.isFinite(number)) return DASH
    return number.toFixed(digits === undefined ? 2 : digits)
  }

  function setText(id, value) {
    var el = $(id)
    if (el) el.textContent = value
  }

  function setTitle(id, value) {
    var el = $(id)
    if (el) el.setAttribute('title', value)
  }

  function clamp01(value) {
    var number = Number(value)
    if (!Number.isFinite(number)) return 0
    return Math.max(0, Math.min(1, number))
  }

  function pct(value) {
    return Math.round(clamp01(value) * 100) + '%'
  }

  function live(data) {
    return !!data && data.available !== false
  }

  function textOr(value) {
    if (typeof value === 'string') return value.trim() || DASH
    if (typeof value === 'number' && Number.isFinite(value)) return String(value)
    return DASH
  }

  /** Presentation helper: pick a one-line label out of string/object payloads. */
  function scalarLabel(value, fallback) {
    if (typeof value === 'string') return value.trim() || fallback
    if (value && typeof value === 'object') {
      var keys = ['label', 'stage', 'action', 'mode', 'tone', 'name', 'word', 'state']
      for (var i = 0; i < keys.length; i += 1) {
        var nested = value[keys[i]]
        if (typeof nested === 'string' && nested.trim()) return nested.trim()
        if (nested && typeof nested === 'object') {
          var deep = scalarLabel(nested, '')
          if (deep) return deep
        }
      }
    }
    return fallback
  }

  function setBar(id, ratio) {
    var el = $(id)
    if (!el) return
    el.style.width = pct(ratio)
  }

  function setDot(value) {
    var el = $('dialArc')
    if (!el) return
    var number = Number(value)
    var ratio = Number.isFinite(number) ? clamp01((number + 1) / 2) : 0
    el.style.strokeDashoffset = String(DIAL_C * (1 - ratio))
  }

  function formatDelta(previous, current) {
    var before = Number(previous)
    var after = Number(current)
    if (!Number.isFinite(before) || !Number.isFinite(after)) return DASH
    var delta = after - before
    if (Math.abs(delta) < 0.005) return '±0.00'
    return (delta > 0 ? '+' : '') + delta.toFixed(2)
  }

  function historyDelta(data, field) {
    var history = live(data) && Array.isArray(data.history) ? data.history : []
    if (history.length < 2) return DASH
    return formatDelta(history[history.length - 2][field], history[history.length - 1][field])
  }

  function shortSession() {
    if (!SESSION) return DASH
    return SESSION.length > 12 ? SESSION.slice(0, 10) + '…' : SESSION
  }

  function moodWord(value) {
    var number = Number(value)
    if (!Number.isFinite(number)) return ''
    if (number > 0.25) return '偏高'
    if (number < -0.25) return '偏低'
    return '平稳'
  }

  function knownDays() {
    var now = Date.now()
    var store = {}
    try { store = JSON.parse(window.localStorage.getItem(KNOWN_SINCE_KEY) || '{}') || {} } catch { store = {} }
    var key = SESSION || 'default'
    var since = Number(store[key])
    if (!Number.isFinite(since) || since <= 0 || since > now) {
      since = now
      store[key] = now
      try { window.localStorage.setItem(KNOWN_SINCE_KEY, JSON.stringify(store)) } catch { /* ignore */ }
    }
    return Math.max(1, Math.ceil((now - since) / 86400000))
  }

  function attachmentOf(trust, closeness, conflict) {
    if (trust >= 0.5 && closeness >= 0.45 && conflict < 0.4) return '安全型'
    if (trust < 0.35) return '回避型'
    return '焦虑型'
  }

  function memoryKind(text) {
    var value = String(text || '')
    if (/(喜欢|爱|讨厌|偏好|习惯|最怕|想要|想吃)/.test(value)) return '偏好'
    if (/(答应|约好|说好|明天|后天|记得|见面|一起|会去)/.test(value)) return '约定'
    return '事实'
  }

  function clockOf(ts) {
    var number = Number(ts)
    if (!Number.isFinite(number) || number <= 0) return DASH
    var date = new Date(number)
    var hh = String(date.getHours()).padStart(2, '0')
    var mm = String(date.getMinutes()).padStart(2, '0')
    return hh + ':' + mm
  }

  function weightOf(value) {
    var number = Number(value)
    if (!Number.isFinite(number)) return DASH
    var fixed = number.toFixed(2)
    return fixed.indexOf('0.') === 0 ? fixed.slice(1) : fixed
  }

  function startOfToday() {
    var dayStart = new Date()
    dayStart.setHours(0, 0, 0, 0)
    return dayStart.getTime()
  }

  /* ---------------------------------------------------- status / topbar --- */

  function setStatus(ok, text) {
    var orb = $('orbState')
    if (orb) orb.classList.toggle('off', !ok)
    var chip = $('topChip')
    if (chip) {
      chip.textContent = ok ? 'ONLINE' : 'NO DATA'
      chip.setAttribute('title', text || (ok ? '已连接' : '未连接'))
    }
  }

  function updateStatusClock() {
    if (!lastSuccess) return
    if (lastData && lastData.available === false) return
    var seconds = Math.max(0, Math.round((Date.now() - lastSuccess) / 1000))
    setStatus(true, '已连接 ' + seconds + ' 秒前')
  }

  function tickClock() {
    var el = $('topClock')
    if (!el) return
    var date = new Date()
    el.textContent = [date.getHours(), date.getMinutes(), date.getSeconds()]
      .map(function (part) { return String(part).padStart(2, '0') }).join(':')
  }

  function renderEmpty(kind) {
    var entry = EMPTY_COPY[kind] || EMPTY_COPY.request_failed
    setText('motivation', entry[0])
    var hint = $('emptyHint')
    if (hint) { hint.textContent = entry[1]; hint.hidden = false }
  }

  function clearEmpty() {
    var hint = $('emptyHint')
    if (hint) { hint.textContent = ''; hint.hidden = true }
  }

  /* ---------------------------------------------------------- dashboard --- */

  function renderDial(data) {
    var on = live(data)
    var value = on ? Number(data.v) : NaN
    setDot(Number.isFinite(value) ? value : NaN)
    setText('dialValue', Number.isFinite(value) ? fmt(value) : DASH)
    if (!on) { setText('dialLabel', DASH); return }
    var tone = scalarLabel(data.tone, '')
    var word = moodWord(data.v)
    var parts = []
    if (tone) parts.push(tone)
    if (word) parts.push(word)
    setText('dialLabel', parts.length ? parts.join(' · ') : DASH)
  }

  function renderChannels(data) {
    var on = live(data)
    var intero = (on && data.intero) || {}
    var rows = [
      ['tension', 'tensionValue'],
      ['warmth', 'warmthValue'],
      ['heaviness', 'heavinessValue'],
      ['clarity', 'clarityValue'],
    ]
    for (var i = 0; i < rows.length; i += 1) {
      var raw = on ? Number(intero[rows[i][0]]) : NaN
      setBar(rows[i][0] + 'Bar', Number.isFinite(raw) ? raw : 0)
      setText(rows[i][1], Number.isFinite(raw) ? fmt(raw) : DASH)
    }
    var samples = on && Array.isArray(data.history) ? data.history.length : 0
    setText('interoMeta', on ? 'SAMPLE ' + samples + ' · 5S' : DASH)
    if (!on) { setText('interoNote', DASH); return }
    var delta = historyDelta(data, 'v')
    var tension = Number(intero.tension)
    var over = Number.isFinite(tension) && tension >= THRESHOLD
    setText('interoNote', 'Δ ' + delta + ' · ' + (over ? '张力已进阈值区' : '未触发干预阈值'))
  }

  function memoryRow(item) {
    var tr = document.createElement('tr')
    var time = document.createElement('td')
    time.className = 't num'
    time.textContent = clockOf(item.ts)
    var kind = document.createElement('td')
    kind.className = 'tag'
    kind.textContent = memoryKind(item.text)
    var body = document.createElement('td')
    body.className = 'c'
    body.textContent = String(item.text || DASH)
    body.title = String(item.text || '')
    var weight = document.createElement('td')
    weight.className = 'n num'
    weight.textContent = weightOf(item.score)
    tr.appendChild(time)
    tr.appendChild(kind)
    tr.appendChild(body)
    tr.appendChild(weight)
    return tr
  }

  function fillMemoryBody(tbody, rows) {
    if (!tbody) return
    tbody.textContent = ''
    if (!rows.length) {
      var empty = document.createElement('tr')
      empty.className = 'is-empty'
      var cell = document.createElement('td')
      cell.colSpan = 4
      cell.textContent = DASH
      empty.appendChild(cell)
      tbody.appendChild(empty)
      return
    }
    for (var i = 0; i < rows.length; i += 1) tbody.appendChild(memoryRow(rows[i] || {}))
  }

  function renderMemories(data) {
    var on = live(data)
    var rows = on && Array.isArray(data.memory_recent) ? data.memory_recent : []
    var total = on && typeof data.memory_count === 'number' ? data.memory_count : rows.length
    var dayStart = startOfToday()
    var fresh = 0
    for (var i = 0; i < rows.length; i += 1) {
      var ts = Number(rows[i] && rows[i].ts)
      if (Number.isFinite(ts) && ts >= dayStart) fresh += 1
    }
    setText('logMeta', on ? total + ' ENTRIES · +' + fresh + ' TODAY' : DASH)
    setText('memoryHint', on ? '共 ' + total + ' 条' : DASH)
    fillMemoryBody($('memoryList'), rows.slice(0, LOG_ROWS))
    fillMemoryBody($('memoryListFull'), rows)
  }

  function joinRelation(data) {
    var stage = scalarLabel(data.relation, '')
    var trust = Number(data.relationship_metrics && data.relationship_metrics.trust)
    var parts = []
    if (stage) parts.push(stage)
    if (Number.isFinite(trust)) parts.push('信任 ' + fmt(trust))
    return parts.length ? parts.join(' · ') : DASH
  }

  function renderState(data) {
    var on = live(data)
    setText('stateRelation', on ? joinRelation(data) : DASH)
    setText('stateTone', on ? (scalarLabel(data.tone, '') || DASH) : DASH)
    setText('stateAutonomy', on ? (scalarLabel(data.autonomy, '') || DASH) : DASH)
    var narrative = (on && data.narrative) || {}
    var intent = String(narrative.intent || '').trim().toUpperCase()
    var length = Number(narrative.length_factor)
    var intentText = DASH
    if (on && intent) intentText = intent + (Number.isFinite(length) ? ' · LEN ' + weightOf(length) : '')
    setText('stateIntent', intentText)
    var pain = Number((on && data.body_metrics) ? data.body_metrics.social_pain : NaN)
    var painEl = $('statePain')
    if (painEl) {
      painEl.textContent = Number.isFinite(pain) ? fmt(pain) : DASH
      painEl.classList.toggle('warn', Number.isFinite(pain) && pain >= 0.35)
    }
    setText('motivation', on ? textOr(data.motivation) : (EMPTY_COPY[String(data && data.reason)] || EMPTY_COPY.request_failed)[0])
  }

  function renderToday(data) {
    var on = live(data)
    var rows = on && Array.isArray(data.memory_recent) ? data.memory_recent : []
    var dayStart = startOfToday()
    var fresh = 0
    for (var i = 0; i < rows.length; i += 1) {
      var ts = Number(rows[i] && rows[i].ts)
      if (Number.isFinite(ts) && ts >= dayStart) fresh += 1
    }
    setText('todayMem', on ? String(fresh) : DASH)
    setText('todayTurns', on && Array.isArray(data.history) ? String(data.history.length) : DASH)
    var proactive = on ? Number(data.proactive_sent_today) : NaN
    setText('todayProactive', Number.isFinite(proactive) ? String(Math.round(proactive)) : DASH)
  }

  function renderLoops(data) {
    var loops = (live(data) && data.open_loops && typeof data.open_loops === 'object') ? data.open_loops : {}
    setText('loopPromise', textOr(loops.promise))
    setText('loopUnanswered', textOr(loops.unanswered))
    setText('loopRepair', textOr(loops.repair))
    var count = Number(live(data) ? data.open_loop_count : NaN)
    setText('loopsMeta', Number.isFinite(count) ? String(count) : DASH)
  }

  /** 12-axis schema, label order fixed; extra axes from the core are appended. */
  var DIM_AXES = [
    ['v', 'VALENCE'],
    ['a', 'AROUSAL'],
    ['dominance', 'DOMINANCE'],
    ['stress', 'STRESS'],
    ['social', 'SOCIAL'],
    ['trust', 'TRUST'],
    ['closeness', 'CLOSENESS'],
    ['conflict', 'CONFLICT'],
    ['needs.belonging', 'NEED BELONGING'],
    ['needs.self_esteem', 'NEED ESTEEM'],
    ['needs.control', 'NEED CONTROL'],
    ['needs.meaningful_existence', 'NEED MEANING'],
  ]

  function dimLabel(key) {
    for (var i = 0; i < DIM_AXES.length; i += 1) {
      if (DIM_AXES[i][0] === key) return DIM_AXES[i][1]
    }
    return String(key || '').replace(/[._]+/g, ' ').toUpperCase()
  }

  function dimRow(key, label) {
    var row = document.createElement('div')
    row.className = 'kv'
    row.setAttribute('data-dim', key)
    var name = document.createElement('span')
    name.className = 'k'
    name.textContent = label
    var value = document.createElement('span')
    value.className = 'num'
    value.textContent = DASH
    row.appendChild(name)
    row.appendChild(value)
    return row
  }

  function renderDims(data) {
    var box = $('dimsRows')
    if (!box) return
    var on = live(data)
    var dims = (on && data.dimensions) || {}
    var axes = dims.axes && typeof dims.axes === 'object' ? dims.axes : null
    var rows = []
    var seen = {}
    if (axes) {
      for (var i = 0; i < DIM_AXES.length; i += 1) {
        var key = DIM_AXES[i][0]
        rows.push([key, DIM_AXES[i][1], axes[key]])
        seen[key] = true
      }
      var extra = Object.keys(axes)
      for (var j = 0; j < extra.length; j += 1) {
        if (seen[extra[j]]) continue
        rows.push([extra[j], dimLabel(extra[j]), axes[extra[j]]])
      }
    } else {
      for (var k = 0; k < DIM_AXES.length; k += 1) rows.push([DIM_AXES[k][0], DIM_AXES[k][1], null])
    }
    box.textContent = ''
    for (var r = 0; r < rows.length; r += 1) {
      var axis = rows[r][2]
      var explicit = axis && typeof axis === 'object' ? Number(axis.explicit) : NaN
      var implicit = axis && typeof axis === 'object' ? Number(axis.implicit) : NaN
      var row = dimRow(rows[r][0], rows[r][1])
      var valueEl = row.lastChild
      if (Number.isFinite(explicit)) {
        valueEl.textContent = fmt(explicit)
        if (Number.isFinite(implicit)) {
          row.title = '显式 ' + fmt(explicit) + ' · 隐式 ' + fmt(implicit)
            + (axis.ambivalence !== undefined ? ' · 矛盾 ' + fmt(axis.ambivalence) : '')
        }
      }
      box.appendChild(row)
    }
    setText('dimsMeta', on && axes ? rows.length + ' AXES' : DASH)
  }

  function renderCache() {
    var rate = $('cacheStatText')
    var detail = $('cacheDetail')
    if (!rate) return
    fetch('/api/biocore/stats', { headers: { accept: 'application/json' } })
      .then(function (response) { return response.ok ? response.json() : null })
      .then(function (body) {
        var cache = body && body.cache ? body.cache : null
        var rateValue = NaN
        var detailText = DASH
        if (cache && Number(cache.turns) > 0) {
          rateValue = Number(cache.hit_rate)
          detailText = (Number(cache.cache_read) || 0) + ' / ' + (Number(cache.prompt_tokens) || 0)
        } else {
          var embed = body && body.embed ? body.embed : null
          var hit = embed ? (Number(embed.hit) || 0) : 0
          var miss = embed ? (Number(embed.miss) || 0) : 0
          if (hit + miss > 0) {
            rateValue = hit / (hit + miss)
            detailText = 'hit ' + hit + ' / miss ' + miss
          }
        }
        var rateText = DASH
        if (Number.isFinite(rateValue)) {
          rateText = (Math.round(rateValue * 1000) / 10) + '%'
          setBar('cacheBar', rateValue)
          setText('todayCache', Math.round(rateValue * 100) + '%')
        } else {
          setBar('cacheBar', 0)
          setText('todayCache', DASH)
        }
        rate.textContent = rateText
        if (detail) detail.textContent = detailText
      })
      .catch(function () {
        rate.textContent = DASH
        if (detail) detail.textContent = DASH
        setBar('cacheBar', 0)
        setText('todayCache', DASH)
      })
  }

  function renderSystem(data) {
    var on = live(data)
    var adapter = (on && data.adapter) || {}
    var recall = Number(on ? data.recall_count : NaN)
    var generated = Number(on ? data.generated_at : NaN)
    var degraded = on && Array.isArray(data.degraded) ? data.degraded : []
    setText('sysAdapter', on ? textOr(adapter.version) : DASH)
    setText('sysRecall', Number.isFinite(recall) && recall >= 0 ? String(Math.round(recall)) : DASH)
    setText('sysUpdated', Number.isFinite(generated) && generated > 0
      ? new Date(generated).toLocaleTimeString() : DASH)
    setText('sysDegraded', on ? (degraded.length ? String(degraded.length) : '0') : DASH)
    setText('systemMeta', on ? (degraded.length ? 'DEGRADED' : 'OK') : DASH)
  }

  function mergePoints(data) {
    var source = live(data) && Array.isArray(data.history) && data.history.length
      ? data.history
      : (seriesPoints || [])
    var out = []
    for (var i = 0; i < source.length; i += 1) {
      var item = source[i] || {}
      var ts = Number(item.ts) || 0
      var v = Number(item.v)
      if (!Number.isFinite(v) || ts <= 0) continue
      out.push({ ts: ts < 1e12 ? ts * 1000 : ts, v: v })
    }
    return out
  }

  function renderStrip(data) {
    var box = $('moodChart')
    if (!box) return
    var points = mergePoints(data)
    var slots = 72
    var bucketMs = (24 * 3600 * 1000) / slots
    var end = Date.now()
    var start = end - 24 * 3600 * 1000
    var buckets = []
    for (var i = 0; i < slots; i += 1) buckets.push(null)
    var peak = null
    for (var p = 0; p < points.length; p += 1) {
      var point = points[p]
      if (point.ts < start || point.ts > end) continue
      var index = Math.min(slots - 1, Math.max(0, Math.floor((point.ts - start) / bucketMs)))
      if (buckets[index] === null || point.v > buckets[index]) buckets[index] = point.v
      if (!peak || point.v > peak.v) peak = point
    }
    box.textContent = ''
    for (var b = 0; b < slots; b += 1) {
      var bar = document.createElement('i')
      var value = buckets[b]
      if (value !== null) {
        var ratio = clamp01((value + 1) / 2)
        bar.style.height = (8 + ratio * 92).toFixed(1) + '%'
        if (ratio >= 0.75) bar.className = 'h'
        else if (ratio >= 0.62) bar.className = 'warm'
        else if (ratio >= 0.5) bar.className = 'm'
      }
      box.appendChild(bar)
    }
    var empty = $('chartEmpty')
    if (empty) empty.hidden = points.length > 0
    setText('chartHint', peak ? 'PEAK ' + fmt(peak.v) + ' @ ' + clockOf(peak.ts) : DASH)
    setText('peakValue', peak ? fmt(peak.v) : DASH)
  }

  /* ------------------------------------------------- memory / relation --- */

  function renderMemorySummary(data) {
    var on = live(data)
    var rows = on && Array.isArray(data.memory_recent) ? data.memory_recent : []
    var total = on && typeof data.memory_count === 'number' ? data.memory_count : rows.length
    var dayStart = startOfToday()
    var today = 0
    for (var i = 0; i < rows.length; i += 1) {
      var ts = Number(rows[i] && rows[i].ts)
      if (Number.isFinite(ts) && ts >= dayStart) today += 1
    }
    var recall = Number(on ? data.recall_count : NaN)
    setText('memTotal', on ? String(total) : DASH)
    setText('memToday', on ? String(today) : DASH)
    setText('memRecall', Number.isFinite(recall) && recall > 0 ? String(Math.round(recall)) : DASH)
    var ratio = clamp01(total / CAPACITY)
    setBar('capFill', ratio)
    setText('capFillText', on ? pct(ratio) : DASH)
    setText('capText', on ? total + ' / ' + CAPACITY : DASH)
  }

  function relationRow(label, value, available) {
    var row = document.createElement('div')
    var dt = document.createElement('dt')
    dt.textContent = label
    var dd = document.createElement('dd')
    dd.className = 'num'
    dd.textContent = available ? fmt(value) : DASH
    row.appendChild(dt)
    row.appendChild(dd)
    return row
  }

  function renderRelation(data) {
    var box = $('metrics')
    if (!box) return
    box.textContent = ''
    var on = live(data)
    var rel = (on && data.relationship_metrics) || {}
    var closeness = Number(rel.closeness)
    var trust = Number(rel.trust)
    var conflict = Number(rel.conflict)
    var safeTrust = Number.isFinite(trust) ? trust : 0
    var safeCloseness = Number.isFinite(closeness) ? closeness : 0
    var safeConflict = Number.isFinite(conflict) ? conflict : 0

    var list = document.createElement('dl')
    list.className = 'data-list'
    list.appendChild(relationRow('亲近', safeCloseness, on))
    list.appendChild(relationRow('信任', safeTrust, on))
    list.appendChild(relationRow('冲突', safeConflict, on))
    box.appendChild(list)

    var count = Number(rel.conflict_count)
    if (!Number.isFinite(count)) count = safeConflict > 0 ? Math.round(safeConflict * 10) : 0
    var foot = document.createElement('div')
    foot.className = 'relation-foot'
    var facts = [
      ['认识', on ? knownDays() + ' 天' : DASH],
      ['冲突', on ? count + ' 次' : DASH],
      ['依恋', on ? attachmentOf(safeTrust, safeCloseness, safeConflict) : DASH],
    ]
    for (var i = 0; i < facts.length; i += 1) {
      var cell = document.createElement('span')
      cell.textContent = facts[i][0]
      var value = document.createElement('b')
      value.textContent = facts[i][1]
      cell.appendChild(value)
      foot.appendChild(cell)
    }
    box.appendChild(foot)
    setText('relationHint', on ? '只读' : DASH)
  }

  function renderNarrative(data) {
    var box = $('narrative')
    if (!box) return
    box.textContent = ''
    var on = live(data)
    var narrative = (on && data.narrative) || {}
    var rows = [
      ['意图', on ? textOr(narrative.intent) : DASH],
      ['主动性', on && narrative.initiative !== undefined ? fmt(narrative.initiative) : DASH],
      ['长度因子', on && narrative.length_factor !== undefined ? fmt(narrative.length_factor) : DASH],
      ['元认知带', on ? textOr(narrative.metacog_band) : DASH],
      ['允许纠偏', on ? (narrative.allow_correction === true ? '是' : '否') : DASH],
    ]
    for (var i = 0; i < rows.length; i += 1) {
      var wrap = document.createElement('div')
      var dt = document.createElement('dt')
      dt.textContent = rows[i][0]
      var dd = document.createElement('dd')
      dd.textContent = rows[i][1]
      wrap.appendChild(dt)
      wrap.appendChild(dd)
      box.appendChild(wrap)
    }
    setText('narrativeHint', on ? '上一轮' : DASH)
  }

  /* --------------------------------------------------------------- tools --- */

  function renderRuntime(data) {
    var on = live(data)
    var seconds = Math.max(0, Math.round((Date.now() - PAGE_START) / 1000))
    var uptime = seconds < 60 ? seconds + ' 秒'
      : (seconds < 3600 ? Math.round(seconds / 60) + ' 分钟' : Math.round(seconds / 3600) + ' 小时')
    setText('runtimeUptime', uptime)
    setText('runtimeTurns', on && Array.isArray(data.history) ? String(data.history.length) : DASH)
    var mode = $('embeddingMode')
    setText('runtimeVector', mode ? (mode.value || 'auto') : DASH)
    setText('runSession', SESSION ? '会话 ' + shortSession() : DASH)
    setText('diagSession', SESSION || DASH)
  }

  function renderDiagnostics(data) {
    var on = live(data)
    var degraded = on && Array.isArray(data.degraded) ? data.degraded : []
    var errors = on && Array.isArray(data.errors) ? data.errors : []
    var parts = degraded.concat(errors).filter(Boolean).map(function (item) {
      return String(item).slice(0, 40)
    })
    setText('diagDegraded', parts.length ? parts.join('，') : (on ? '无' : DASH))
    var generated = Number(on ? data.generated_at : NaN)
    setText('diagGenerated', Number.isFinite(generated) && generated > 0
      ? new Date(generated).toLocaleTimeString() : DASH)
    setText('diagAdapter', on && data.adapter && data.adapter.version ? String(data.adapter.version) : DASH)
  }

  var traceFormatter = function () { return window.BioTraceView || null }

  function renderTrace(body) {
    var list = $('traceList')
    if (!list) return
    list.textContent = ''
    var view = traceFormatter()
    var failed = !body || body.ok === false
    var rows = !failed && Array.isArray(body.rows) ? body.rows.slice(0, TRACE_LIMIT) : []
    var formatted = {
      rows: view ? rows.map(function (row) { return view.formatRow(row) }) : rows,
      empty: !rows.length,
      error: failed,
    }
    setText('traceHint', rows.length ? '最近 ' + rows.length + ' 轮' : DASH)
    if (formatted.empty) {
      var empty = document.createElement('p')
      empty.className = 'state-note'
      empty.textContent = failed ? '读取失败，稍后自动重试' : '还没有记录'
      list.appendChild(empty)
      return
    }
    for (var i = 0; i < formatted.rows.length; i += 1) {
      var row = formatted.rows[i] || {}
      var item = document.createElement('div')
      item.className = 'trace-row'
      var values = [row.time, row.changes, row.intent, row.length, row.gate, row.memory]
      for (var c = 0; c < values.length; c += 1) {
        var cell = document.createElement('span')
        if (c === 0) cell.className = 'trace-time'
        var cellText = String(values[c] || DASH).split(' · ').join('，')
        cell.textContent = cellText
        cell.title = cellText
        cell.setAttribute('data-label', ['时间', '状态变化', '意图', '长度来源', '闸命中', '记忆'][c])
        item.appendChild(cell)
      }
      list.appendChild(item)
    }
  }

  function fetchTrace() {
    if (!SESSION) {
      renderTrace({ ok: true, rows: [] })
      return Promise.resolve(null)
    }
    return fetch('/api/biocore/trace?session=' + encodeURIComponent(SESSION) + '&limit=' + TRACE_LIMIT, {
      headers: { accept: 'application/json' },
    })
      .then(function (response) { return response.json() })
      .then(function (body) { renderTrace(body); return body })
      .catch(function () { renderTrace(null); return null })
  }

  /* ------------------------------------------------------------- polling --- */

  function finishBoot() {
    if (bootDone) return
    bootDone = true
    document.body.classList.remove('is-booting')
  }

  function render(data) {
    lastData = data
    renderDial(data)
    renderChannels(data)
    renderMemories(data)
    renderState(data)
    renderToday(data)
    renderLoops(data)
    renderDims(data)
    renderSystem(data)
    renderStrip(data)
    renderMemorySummary(data)
    renderRelation(data)
    renderNarrative(data)
    renderRuntime(data)
    renderDiagnostics(data)
    setText('topSession', SESSION ? shortSession() : DASH)
    document.body.classList.toggle('is-empty', !live(data))
    if (!data) renderEmpty('request_failed')
    else if (data.available === false) renderEmpty(String(data.reason || 'request_failed'))
    else clearEmpty()
    finishBoot()
  }

  function fetchState() {
    if (!SESSION) {
      setStatus(false, '未连接')
      render(null)
      return Promise.resolve(null)
    }
    return fetch('/api/biocore/state?session=' + encodeURIComponent(SESSION), {
      headers: { accept: 'application/json' },
    })
      .then(function (response) { return response.json() })
      .then(function (data) {
        lastSuccess = Date.now()
        var on = live(data)
        setStatus(on, on ? '已连接 0 秒前' : '还没有状态')
        render(data)
        return data
      })
      .catch(function () {
        setStatus(false, '未连接')
        render(null)
        return null
      })
  }

  function loadSeries() {
    if (!SESSION) return
    fetch('/api/biocore/series?session=' + encodeURIComponent(SESSION) + '&window=24h', {
      headers: { accept: 'application/json' },
    })
      .then(function (response) { return response.ok ? response.json() : null })
      .then(function (body) {
        if (body && Array.isArray(body.points) && body.points.length) {
          seriesPoints = body.points
        } else if (body && body.series && Array.isArray(body.series.v)
          && Array.isArray(body.series.ts) && body.series.v.length === body.series.ts.length) {
          seriesPoints = body.series.ts.map(function (ts, index) {
            return { ts: ts, v: body.series.v[index] }
          })
        }
      })
      .catch(function () { /* optional: state history already covers the strip */ })
  }

  /* ------------------------------------------------------------- switches --- */

  function switchPost(key, value) {
    return fetch('/api/biocore/switch', {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ key: key, value: !!value, confirm: true }),
    }).then(function (response) { return response.json() })
  }

  function setSwitchInputs(key, checked) {
    var inputs = document.querySelectorAll('input[data-switch="' + key + '"]')
    for (var i = 0; i < inputs.length; i += 1) inputs[i].checked = !!checked
  }

  function applySwitchValues(values) {
    for (var i = 0; i < SWITCHES.length; i += 1) {
      if (values && typeof values[SWITCHES[i].key] === 'boolean') {
        setSwitchInputs(SWITCHES[i].key, values[SWITCHES[i].key])
      }
    }
  }

  function buildSwitches() {
    var box = $('switches')
    if (!box) return
    box.textContent = ''
    var lastGroup = ''
    for (var i = 0; i < SWITCHES.length; i += 1) {
      var meta = SWITCHES[i]
      if (meta.group && meta.group !== lastGroup) {
        lastGroup = meta.group
        var heading = document.createElement('p')
        heading.className = 'switch-group'
        heading.textContent = meta.group
        box.appendChild(heading)
      }
      var row = document.createElement('label')
      row.className = 'switch-row'
      var textWrap = document.createElement('span')
      textWrap.className = 'switch-text'
      var title = document.createElement('span')
      title.className = 'switch-label'
      title.textContent = meta.label
      var desc = document.createElement('span')
      desc.className = 'switch-desc'
      desc.textContent = meta.desc
      textWrap.appendChild(title)
      textWrap.appendChild(desc)
      var input = document.createElement('input')
      input.type = 'checkbox'
      input.setAttribute('data-switch', meta.key)
      input.setAttribute('aria-label', meta.label)
      input.addEventListener('change', onSwitchChange)
      row.appendChild(textWrap)
      row.appendChild(input)
      box.appendChild(row)
    }
  }

  function onSwitchChange(event) {
    var input = event.target
    var key = input && input.getAttribute('data-switch')
    if (!key || switchBusy) return
    var next = !!input.checked
    switchBusy = true
    input.disabled = true
    setText('switchHint', '写入中')
    switchPost(key, next)
      .then(function (body) {
        if (body && body.ok === true && body.switches) {
          applySwitchValues(body.switches)
          setText('switchHint', '已写入 flags')
        } else {
          setSwitchInputs(key, !next)
          setText('switchHint', '写入失败 ' + ((body && body.reason) || 'unknown'))
        }
      })
      .catch(function () {
        setSwitchInputs(key, !next)
        setText('switchHint', '写入失败 未连接')
      })
      .then(function () {
        switchBusy = false
        input.disabled = false
      })
  }

  function loadSwitches() {
    return fetch('/api/biocore/switch', { headers: { accept: 'application/json' } })
      .then(function (response) { return response.json() })
      .then(function (body) {
        if (body && body.ok === true && body.switches) applySwitchValues(body.switches)
      })
      .catch(function () { /* keep defaults, POST still tries */ })
  }

  /* -------------------------------------------------------------- persona --- */

  function personaStatus(message, tone) {
    var el = $('personaStatus')
    if (!el) return
    el.textContent = message
    el.classList.toggle('warn', tone === 'error')
  }

  function personaPersistHint(message, tone) {
    var el = $('personaHint')
    if (!el) return
    el.textContent = message
    el.classList.toggle('warn', tone === 'error')
  }

  function loadPersona() {
    return fetch('/api/biocore/persona', { headers: { accept: 'application/json' } })
      .then(function (response) { return response.json() })
      .then(function (body) {
        if (!body || body.ok !== true) {
          personaStatus('人设文件坏了 ' + ((body && body.reason) || 'bad_profile'), 'error')
          return body
        }
        personaText = String(body.text || '')
        var area = $('personaText')
        if (area) area.value = personaText
        personaDirty = false
        personaStatus(body.text ? '已载入' : '未设置', body.text ? 'ok' : '')
        return body
      })
      .catch(function () {
        personaStatus('连不上宿主', 'error')
        return null
      })
  }

  function savePersona() {
    var area = $('personaText')
    var button = $('personaSave')
    var text = area ? area.value : ''
    if (!text.trim()) {
      personaStatus('人设不能为空', 'error')
      return
    }
    if (!window.confirm('确定保存并编译人设吗？这会覆盖当前数值画像。')) return
    personaStatus('编译中')
    if (button) button.disabled = true
    fetch('/api/biocore/persona', {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ text: text, confirm: true }),
    })
      .then(function (response) { return response.json() })
      .then(function (body) {
        if (!body || body.ok !== true) {
          personaStatus('编译失败 ' + ((body && body.reason) || 'unknown'), 'error')
          if (body && body.detail) setText('personaHint', String(body.detail).slice(0, 120))
          return body
        }
        personaDirty = false
        personaText = String(body.text || text)
        personaStatus('已编译 ' + new Date().toLocaleTimeString(), 'ok')
        return body
      })
      .catch(function () {
        personaStatus('编译失败 连不上宿主', 'error')
        return null
      })
      .then(function () { if (button) button.disabled = false })
  }

  function loadPersonaPersist() {
    return fetch('/api/biocore/persona/apply', { headers: { accept: 'application/json' } })
      .then(function (response) { return response.json() })
      .then(function (body) {
        var applyButton = $('personaPersist')
        var rollbackButton = $('personaRollback')
        if (!body || body.ok !== true) {
          personaPersistHint('常驻不可用 ' + ((body && body.reason) || 'unknown'), 'error')
          if (rollbackButton) rollbackButton.hidden = true
          return body
        }
        if (body.applied) {
          personaPersistHint('常驻已应用 预设 ' + String(body.preset_id || '') + ' '
            + Number(body.chars || 0) + ' 字'
            + (body.matches_preset ? '' : ' 预设已变更'), body.matches_preset ? 'ok' : 'error')
          if (rollbackButton) rollbackButton.hidden = false
          if (applyButton) applyButton.textContent = '重新应用常驻'
        } else {
          var reason = String(body.reason || 'not_applied')
          personaPersistHint(reason === 'not_applied' ? '已保存人设，常驻未启用' : '常驻未应用 ' + reason, '')
          if (rollbackButton) rollbackButton.hidden = !body.has_backup
          if (applyButton) applyButton.textContent = '设为常驻'
        }
        return body
      })
      .catch(function () {
        personaPersistHint('常驻 连不上宿主', 'error')
        return null
      })
  }

  function applyPersonaPersist() {
    var button = $('personaPersist')
    if (!window.confirm('把人设写入 DSH 默认预设并常驻？应用后由预设提供人设，可随时恢复。')) return
    personaPersistHint('正在写入预设')
    if (button) button.disabled = true
    fetch('/api/biocore/persona/apply', {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ confirm: true }),
    })
      .then(function (response) { return response.json() })
      .then(function (body) {
        if (!body || body.ok !== true) {
          personaPersistHint('常驻应用失败 ' + ((body && body.reason) || 'unknown'), 'error')
          return body
        }
        personaStatus('已写入常驻预设', 'ok')
        return loadPersonaPersist()
      })
      .catch(function () {
        personaPersistHint('常驻应用失败 连不上宿主', 'error')
        return null
      })
      .then(function () { if (button) button.disabled = false })
  }

  function rollbackPersonaPersist() {
    var button = $('personaRollback')
    if (!window.confirm('恢复应用常驻前的预设人设？')) return
    personaPersistHint('正在恢复')
    if (button) button.disabled = true
    fetch('/api/biocore/persona/apply', {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ confirm: true, rollback: true }),
    })
      .then(function (response) { return response.json() })
      .then(function (body) {
        if (!body || body.ok !== true) {
          personaPersistHint('恢复失败 ' + ((body && body.reason) || 'unknown'), 'error')
          return body
        }
        personaStatus('已恢复原预设人设', 'ok')
        return loadPersonaPersist()
      })
      .catch(function () {
        personaPersistHint('恢复失败 连不上宿主', 'error')
        return null
      })
      .then(function () { if (button) button.disabled = false })
  }

  function bindPersona() {
    var area = $('personaText')
    var button = $('personaSave')
    if (area) {
      area.addEventListener('input', function () {
        personaDirty = area.value !== personaText
        if (personaDirty) personaStatus('未保存')
      })
    }
    if (button) button.addEventListener('click', savePersona)
    var persistButton = $('personaPersist')
    if (persistButton) persistButton.addEventListener('click', applyPersonaPersist)
    var rollbackButton = $('personaRollback')
    if (rollbackButton) rollbackButton.addEventListener('click', rollbackPersonaPersist)
    loadPersonaPersist()
  }

  /* ---------------------------------------------------------- model/config --- */

  function modelInputs() {
    return {
      mode: $('embeddingMode'),
      baseUrl: $('modelBaseUrl'),
      model: $('modelName'),
      apiKey: $('modelApiKey'),
    }
  }

  function modelValues() {
    var fields = modelInputs()
    return {
      mode: fields.mode ? fields.mode.value : 'auto',
      base_url: fields.baseUrl ? fields.baseUrl.value.trim() : '',
      model: fields.model ? fields.model.value.trim() : '',
      api_key: fields.apiKey ? fields.apiKey.value : '',
    }
  }

  function rerankInputs() {
    return {
      enabled: $('rerankEnabled'),
      baseUrl: $('rerankBaseUrl'),
      model: $('rerankModel'),
      apiKey: $('rerankApiKey'),
      topN: $('rerankTopN'),
    }
  }

  function rerankValues() {
    var fields = rerankInputs()
    var top = Number(fields.topN ? fields.topN.value : 20)
    return {
      enabled: !!(fields.enabled && fields.enabled.checked),
      base_url: fields.baseUrl ? fields.baseUrl.value.trim() : '',
      model: fields.model ? fields.model.value.trim() : '',
      api_key: fields.apiKey ? fields.apiKey.value : '',
      top_n: Number.isFinite(top) ? Math.max(1, Math.min(100, Math.round(top))) : 20,
    }
  }

  function fillModel(values) {
    var fields = modelInputs()
    var data = values || {}
    if (fields.mode && data.mode) fields.mode.value = String(data.mode)
    if (fields.baseUrl) fields.baseUrl.value = String(data.base_url || '')
    if (fields.model) fields.model.value = String(data.model || '')
    if (fields.apiKey && data.api_key) fields.apiKey.value = '••••••••'
  }

  function fillRerank(value) {
    var fields = rerankInputs()
    var data = value && typeof value === 'object' ? value : {}
    if (fields.enabled) fields.enabled.checked = data.enabled === true
    if (fields.baseUrl) fields.baseUrl.value = String(data.base_url || 'https://api.jina.ai/v1')
    if (fields.model) fields.model.value = String(data.model || 'jina-reranker-v2-base-multilingual')
    if (fields.apiKey && data.api_key) fields.apiKey.value = '••••••••'
    if (fields.topN) fields.topN.value = String(data.top_n || 20)
  }

  function setModelStatus(message) { setText('modelStatus', message) }

  function saveModelDraft(values) {
    try { window.localStorage.setItem(MODEL_DRAFT_KEY, JSON.stringify(values)) } catch { /* ignore */ }
  }

  function loadModelDraft() {
    try {
      var raw = window.localStorage.getItem(MODEL_DRAFT_KEY)
      return raw ? JSON.parse(raw) : null
    } catch { return null }
  }

  function expressionInputs() {
    return {
      toggle: $('expressionToggle'),
      fields: $('expressionFields'),
      target: $('lengthTarget'),
      max: $('lengthMax'),
      status: $('expressionStatus'),
      save: $('expressionSave'),
    }
  }

  function syncExpressionFields() {
    var inputs = expressionInputs()
    if (!inputs.toggle) return
    var on = inputs.toggle.checked === true
    if (inputs.fields) inputs.fields.hidden = !on
    if (inputs.status) inputs.status.textContent = on ? '按目标与上限约束' : '长度由人设决定'
  }

  function fillExpression(value) {
    var inputs = expressionInputs()
    var data = value && typeof value === 'object' ? value : {}
    if (inputs.toggle) inputs.toggle.checked = data.length_hint === true
    if (inputs.target) inputs.target.value = String(data.length_target || 10)
    if (inputs.max) inputs.max.value = String(data.length_max || 30)
    syncExpressionFields()
  }

  function expressionValues() {
    var inputs = expressionInputs()
    var target = Number(inputs.target ? inputs.target.value : 10)
    var max = Number(inputs.max ? inputs.max.value : 30)
    return {
      length_hint: !!(inputs.toggle && inputs.toggle.checked),
      length_target: Number.isFinite(target) ? Math.max(1, Math.min(200, Math.round(target))) : 10,
      length_max: Number.isFinite(max) ? Math.max(2, Math.min(400, Math.round(max))) : 30,
    }
  }

  function bindExpression() {
    var inputs = expressionInputs()
    if (inputs.toggle) inputs.toggle.addEventListener('change', syncExpressionFields)
    if (!inputs.save) return
    inputs.save.addEventListener('click', function () {
      var values = expressionValues()
      if (inputs.save.disabled) return
      inputs.save.disabled = true
      if (inputs.status) inputs.status.textContent = '保存中'
      fetch('/api/biocore/settings', {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({ confirm: true, expression: values }),
      })
        .then(function (response) { return response.ok ? response.json() : null })
        .then(function (body) {
          if (body && body.ok === true && body.expression) {
            fillExpression(body.expression)
            if (inputs.status) inputs.status.textContent = '已保存，重启后生效'
            return
          }
          if (inputs.status) inputs.status.textContent = '保存失败 ' + ((body && body.reason) || 'unknown')
        })
        .catch(function () {
          if (inputs.status) inputs.status.textContent = '保存失败 连不上宿主'
        })
        .then(function () { inputs.save.disabled = false })
    })
  }

  function loadModelSettings() {
    fetch('/api/biocore/settings', { headers: { accept: 'application/json' } })
      .then(function (response) { return response.ok ? response.json() : null })
      .then(function (body) {
        var embedding = body && (body.embedding || body.settings)
        if (body && body.ok === true && embedding) {
          fillModel(embedding)
          fillRerank(body.rerank)
          fillExpression(body.expression)
          setText('sanitizeReplyText', body.sanitize_reply === false ? '关' : '开')
          setModelStatus('已载入')
          return
        }
        var draft = loadModelDraft()
        if (draft) fillModel(draft)
        setModelStatus(draft ? '本地草稿' : '未保存')
      })
      .catch(function () {
        var draft = loadModelDraft()
        if (draft) fillModel(draft)
        setModelStatus(draft ? '本地草稿' : '未保存')
      })
  }

  function bindModelActions() {
    var button = $('modelSave')
    var test = $('modelTest')
    var rerankTest = $('rerankTest')

    function runConnectionTest(kind, buttonEl) {
      setModelStatus((kind === 'rerank' ? 'Rerank ' : 'Embedding ') + '测试中')
      if (buttonEl) buttonEl.disabled = true
      fetch('/api/biocore/settings/test', {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({ kind: kind }),
      })
        .then(function (response) { return response.ok ? response.json() : null })
        .then(function (body) {
          if (body && body.ok === true) {
            setModelStatus(kind + ' 正常 ' + (body.elapsed_ms || 0) + 'ms')
            return
          }
          setModelStatus(kind + ' 失败 ' + ((body && (body.detail || body.reason)) || 'no_response'))
        })
        .catch(function () { setModelStatus(kind + ' 失败 连不上宿主') })
        .then(function () { if (buttonEl) buttonEl.disabled = false })
    }

    if (test) test.addEventListener('click', function () { runConnectionTest('embedding', test) })
    if (rerankTest) rerankTest.addEventListener('click', function () { runConnectionTest('rerank', rerankTest) })
    if (!button) return
    button.addEventListener('click', function () {
      var values = modelValues()
      if (values.api_key.indexOf('•') === 0) delete values.api_key
      var rerank = rerankValues()
      if (String(rerank.api_key || '').indexOf('•') === 0) delete rerank.api_key
      setModelStatus('保存中')
      button.disabled = true
      fetch('/api/biocore/settings', {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({ confirm: true, embedding: values, rerank: rerank }),
      })
        .then(function (response) { return response.ok ? response.json() : null })
        .then(function (body) {
          if (body && body.ok === true) {
            setModelStatus(body.restart_required === false ? '已保存' : '已保存，重启后生效')
            return
          }
          saveModelDraft(values)
          setModelStatus('接口未开启 已存本地草稿')
        })
        .catch(function () {
          saveModelDraft(values)
          setModelStatus('接口未开启 已存本地草稿')
        })
        .then(function () { button.disabled = false })
    })
  }

  function bindAdvanced() {
    var reset = $('resetLayout')
    if (reset) reset.addEventListener('click', function () { resetLayout() })
    var clear = $('clearDraft')
    if (clear) clear.addEventListener('click', function () {
      try { window.localStorage.removeItem(MODEL_DRAFT_KEY) } catch { /* ignore */ }
      setText('advancedHint', '草稿已清空')
      layoutToast('已清空本地草稿')
    })
  }

  /* ------------------------------------------------------------ layout --- */

  var layoutApi = typeof window !== 'undefined' ? window.BioCorePanelLayout : null
  var layoutState = null
  var layoutEditing = false
  var activeTab = 'dashboard'
  var drag = null
  var pressTimer = null
  var pressStart = null

  function layoutStorage() {
    try { return window.localStorage } catch { return null }
  }

  function layoutCards() {
    var container = $('grid')
    if (!container) return []
    return Array.prototype.slice.call(container.querySelectorAll('[data-card-id]'))
  }

  function layoutToast(message) {
    var el = $('layoutToast')
    if (!el) return
    el.textContent = String(message || '')
    el.hidden = false
    if (toastTimer) window.clearTimeout(toastTimer)
    toastTimer = window.setTimeout(function () { el.hidden = true }, 2400)
  }

  function ensureHideButton(card) {
    if (!card || card.querySelector('[data-layout-hide]')) return
    var hide = document.createElement('button')
    hide.type = 'button'
    hide.className = 'card-hide'
    hide.setAttribute('data-layout-hide', '')
    hide.setAttribute('aria-label', '隐藏这张卡片')
    hide.textContent = '−'
    card.appendChild(hide)
  }

  function cardLabel(card) {
    var named = card.getAttribute('data-card-label')
    if (named) return named
    var title = card.querySelector('.card-head b')
    return title ? title.textContent.trim() : card.getAttribute('data-card-id')
  }

  function applyTabGroup() {
    if (!layoutApi) return
    var body = document.body
    body.dataset.tab = activeTab
    body.classList.toggle('layout-editing', layoutEditing)
    var group = layoutEditing ? null : (layoutApi.TAB_GROUPS[activeTab] || [])
    var cards = layoutCards()
    for (var i = 0; i < cards.length; i += 1) {
      var id = cards[i].getAttribute('data-card-id')
      cards[i].classList.toggle('tab-off', !!group && group.indexOf(id) < 0)
    }
    var items = document.querySelectorAll('[data-tab]')
    for (var j = 0; j < items.length; j += 1) {
      var on = items[j].getAttribute('data-tab') === activeTab
      items[j].classList.toggle('active', on)
      items[j].setAttribute('aria-selected', on ? 'true' : 'false')
    }
    if (layoutEditing) renderAddCards()
  }

  function setActiveTab(tab) {
    if (!layoutApi || !layoutApi.TAB_GROUPS[tab]) return
    activeTab = tab
    applyTabGroup()
    var grid = $('grid')
    if (grid) grid.scrollTop = 0
    if (tab === 'tools') renderCache()
  }

  function bindTabBar() {
    document.addEventListener('click', function (event) {
      var target = event.target
      var button = target && target.closest ? target.closest('[data-tab]') : null
      if (!button) return
      event.preventDefault()
      setActiveTab(button.getAttribute('data-tab'))
    })
    applyTabGroup()
  }

  function hostOf(card) {
    var parent = card.parentElement
    if (parent && parent.classList && parent.classList.contains('stack')) return parent
    return $('grid')
  }

  function applyLayout(next, persist) {
    if (!layoutApi) return
    layoutState = layoutApi.normalizeLayout(next)
    if (persist !== false) layoutApi.writeLayout(layoutStorage(), layoutState)
    var container = $('grid')
    if (!container) return
    var cards = layoutCards()
    var byId = {}
    for (var i = 0; i < cards.length; i += 1) {
      var card = cards[i]
      ensureHideButton(card)
      byId[card.getAttribute('data-card-id')] = card
    }
    var ordered = layoutApi.sortCards(Object.keys(byId), layoutState.order)
    for (var j = 0; j < ordered.length; j += 1) {
      var node = byId[ordered[j]]
      if (!node) continue
      node.classList.toggle('is-hidden', layoutState.hidden.indexOf(ordered[j]) >= 0)
      var host = hostOf(node)
      if (host) host.appendChild(node)
    }
    renderAddCards()
    applyTabGroup()
  }

  function persistDomOrder() {
    if (!layoutApi) return
    var order = []
    var cards = layoutCards()
    for (var i = 0; i < cards.length; i += 1) order.push(cards[i].getAttribute('data-card-id'))
    layoutState = layoutApi.normalizeLayout({
      order: order,
      hidden: layoutState ? layoutState.hidden : [],
    })
    layoutApi.writeLayout(layoutStorage(), layoutState)
  }

  function hiddenCards() {
    var out = []
    if (!layoutApi || !layoutState) return out
    var cards = layoutCards()
    for (var i = 0; i < cards.length; i += 1) {
      var id = cards[i].getAttribute('data-card-id')
      if (layoutState.hidden.indexOf(id) >= 0) {
        out.push({ id: id, card: cards[i], label: cardLabel(cards[i]) })
      }
    }
    return out
  }

  function renderAddCards() {
    var section = $('addCards')
    var list = $('addList')
    if (!section || !list) return
    var hidden = layoutEditing ? hiddenCards() : []
    if (!hidden.length) {
      section.hidden = true
      list.textContent = ''
      return
    }
    section.hidden = false
    list.textContent = ''
    for (var i = 0; i < hidden.length; i += 1) {
      var chip = document.createElement('button')
      chip.type = 'button'
      chip.className = 'add-chip'
      chip.setAttribute('data-add-card', hidden[i].id)
      chip.textContent = '+ ' + hidden[i].label
      list.appendChild(chip)
    }
  }

  function showHiddenCard(id) {
    if (!layoutApi || !layoutState) return
    var next = layoutApi.showCard(layoutState, id)
    applyLayout(next)
    var label = ''
    var cards = layoutCards()
    for (var i = 0; i < cards.length; i += 1) {
      if (cards[i].getAttribute('data-card-id') === id) label = cardLabel(cards[i])
    }
    layoutToast('已恢复「' + (label || id) + '」')
  }

  function setLayoutEditing(on) {
    layoutEditing = !!on
    var fab = $('layoutFab')
    if (fab) fab.hidden = layoutEditing
    var done = $('layoutDone')
    if (done) done.hidden = !layoutEditing
    var reset = $('layoutReset')
    if (reset) reset.hidden = !layoutEditing
    var cards = layoutCards()
    for (var i = 0; i < cards.length; i += 1) ensureHideButton(cards[i])
    renderAddCards()
    applyTabGroup()
    if (layoutEditing) layoutToast('编辑模式：拖动排序，橙色 − 隐藏卡片')
    else persistDomOrder()
  }

  function resetLayout(message) {
    if (layoutApi) layoutApi.clearLayout(layoutStorage())
    layoutState = layoutApi ? layoutApi.defaultLayout() : null
    applyLayout(layoutState, false)
    layoutToast(message || '已恢复默认布局')
  }

  /* --- drag & drop ------------------------------------------------------- */

  function beginDrag(card, pointerId) {
    if (!layoutEditing || !layoutApi || !card) return
    drag = { card: card, pointerId: pointerId }
    card.classList.add('dragging')
    try { if (card.setPointerCapture) card.setPointerCapture(pointerId) } catch { /* ignore */ }
  }

  function updateDrag(event) {
    if (!drag || event.pointerId !== drag.pointerId) return
    var all = layoutCards()
    var cards = []
    for (var i = 0; i < all.length; i += 1) {
      if (all[i].parentElement !== drag.card.parentElement) continue
      if (all[i].classList.contains('tab-off') || all[i].classList.contains('is-hidden')) continue
      cards.push(all[i])
    }
    var container = drag.card.parentElement
    if (!container) return
    var x = event.clientX
    var y = event.clientY
    var reference = null
    var last = null
    for (var j = 0; j < cards.length; j += 1) {
      var candidate = cards[j]
      if (candidate === drag.card) continue
      var rect = candidate.getBoundingClientRect()
      last = rect
      if (y < rect.top) { reference = candidate; break }
      if (y <= rect.bottom && x < rect.left + rect.width / 2) { reference = candidate; break }
    }
    if (reference) {
      container.insertBefore(drag.card, reference)
      return
    }
    if (last && y > last.bottom) container.appendChild(drag.card)
  }

  function endDrag() {
    if (!drag) return
    var card = drag.card
    card.classList.remove('dragging')
    try { if (card.releasePointerCapture) card.releasePointerCapture(drag.pointerId) } catch { /* ignore */ }
    drag = null
    persistDomOrder()
  }

  function onGridPointerDown(event) {
    if (!layoutEditing) return
    var target = event.target
    if (!target || !target.closest) return
    if (target.closest('[data-layout-hide]')) return
    var card = target.closest('[data-card-id]')
    if (!card) return
    var intent = layoutApi && layoutApi.dragIntent
      ? layoutApi.dragIntent({ editing: layoutEditing, pointerType: event.pointerType })
      : { hold: true }
    if (!intent.hold) return
    if (event.cancelable) event.preventDefault()
    pressStart = {
      x: event.clientX,
      y: event.clientY,
      card: card,
      pointerId: event.pointerId,
      touch: event.pointerType !== 'mouse',
    }
    if (pressTimer) window.clearTimeout(pressTimer)
    if (pressStart.touch) {
      pressTimer = window.setTimeout(function () {
        pressTimer = null
        if (!pressStart) return
        var pending = pressStart
        pressStart = null
        beginDrag(pending.card, pending.pointerId)
      }, LONG_PRESS_MS)
    }
  }

  function onDocPointerMove(event) {
    if (pressStart && !drag) {
      var dx = event.clientX - pressStart.x
      var dy = event.clientY - pressStart.y
      if (dx * dx + dy * dy > MOVE_CANCEL_PX * MOVE_CANCEL_PX) {
        if (pressTimer) { window.clearTimeout(pressTimer); pressTimer = null }
        if (pressStart.touch) {
          pressStart = null
          return
        }
        var pending = pressStart
        pressStart = null
        beginDrag(pending.card, pending.pointerId)
      }
      return
    }
    if (drag) {
      if (event.cancelable) event.preventDefault()
      updateDrag(event)
    }
  }

  function onDocPointerUp() {
    if (pressTimer) { window.clearTimeout(pressTimer); pressTimer = null }
    pressStart = null
    endDrag()
  }

  function onGridClick(event) {
    var target = event.target
    if (!target || !target.closest) return
    var add = target.closest('[data-add-card]')
    if (add) {
      event.preventDefault()
      showHiddenCard(add.getAttribute('data-add-card'))
      return
    }
    var hide = target.closest('[data-layout-hide]')
    if (!hide) return
    event.preventDefault()
    var card = hide.closest('[data-card-id]')
    if (!card || !layoutApi) return
    var id = card.getAttribute('data-card-id')
    var next = layoutApi.hideCard(layoutState, id)
    if (next.restored) {
      resetLayout('全部卡片已隐藏，已恢复默认布局')
      return
    }
    applyLayout(next)
    layoutToast('已隐藏「' + cardLabel(card) + '」')
  }

  function initLayout() {
    if (!layoutApi) return
    layoutState = layoutApi.readLayout(layoutStorage())
    applyLayout(layoutState, false)
    var fab = $('layoutFab')
    var done = $('layoutDone')
    var reset = $('layoutReset')
    if (fab) fab.addEventListener('click', function () { setLayoutEditing(true) })
    if (done) done.addEventListener('click', function () { setLayoutEditing(false) })
    if (reset) reset.addEventListener('click', function () { resetLayout() })
    var grid = $('grid')
    if (grid) grid.addEventListener('pointerdown', onGridPointerDown)
    document.addEventListener('click', onGridClick)
    bindTabBar()
    document.addEventListener('pointermove', onDocPointerMove, { passive: false })
    document.addEventListener('pointerup', onDocPointerUp)
    document.addEventListener('pointercancel', onDocPointerUp)
  }

  /* ------------------------------------------------------------------ boot --- */

  function boot() {
    watchTheme()
    renderVersions()
    setText('topSession', SESSION ? shortSession() : DASH)
    setText('dataDirText', '数据目录：宿主托管')
    tickClock()
    window.setInterval(tickClock, 1000)

    if (layoutApi && layoutApi.TAB_GROUPS[INITIAL_TAB]) activeTab = INITIAL_TAB
    initLayout()
    bindAdvanced()
    bindPersona()
    loadPersona()
    bindModelActions()
    bindExpression()
    loadModelSettings()
    buildSwitches()

    if (!SESSION) {
      setStatus(false, '未连接')
      render(null)
      renderTrace({ ok: true, rows: [] })
      window.setTimeout(finishBoot, 600)
      return
    }
    loadSwitches()
    loadSeries()
    fetchState()
    fetchTrace()
    renderCache()

    function pollTick() {
      if (document.hidden) return
      fetchState()
      fetchTrace()
    }
    window.setInterval(pollTick, POLL_MS)
    window.setInterval(updateStatusClock, 1000)
    window.setInterval(renderCache, STATS_MS)
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) return
      fetchState()
      fetchTrace()
      renderCache()
    })
    window.addEventListener('resize', function () { renderStrip(lastData) })
    window.setTimeout(finishBoot, 3000)
  }

  function renderVersions() {
    setText('railVersion', 'v' + PANEL_VERSION)
    setText('topBuild', 'BUILD ' + PANEL_VERSION)
    setText('pluginVersion', PANEL_VERSION)
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot)
  else boot()
})();
