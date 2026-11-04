/** BioCore instrument panel: vanilla JS, no external dependencies.
 *
 * Reads GET /api/biocore/state?session=<id> every 2 seconds. The /series
 * endpoint is optional: if it is absent we draw from the state extras
 * (history / memory_recent) and never block rendering on it.
 */
(function () {
  'use strict'

  var POLL_MS = 5000
  var SESSION = (new URLSearchParams(window.location.search).get('session') || '').trim()
  var SWITCHES = [
    { key: 'safety_enabled', label: '安全闸', desc: '打开后可能拦截/改写回复' },
    { key: 'boundary_enabled', label: '底线', desc: '越界时先守住边界' },
    { key: 'crisis_safety_enabled', label: '危机兜底', desc: '危机信号优先安全' },
    { key: 'proactive_send', label: '主动回复', desc: '允许私聊主动发消息' },
  ]
  var lastSuccess = 0
  var lastData = null
  var seriesPoints = null
  var localPoints = []
  var switchBusy = false

  function $(id) { return document.getElementById(id) }

  function fmt(value, digits) {
    var number = Number(value)
    if (!Number.isFinite(number)) return '–'
    return number.toFixed(digits === undefined ? 2 : digits)
  }

  function setText(id, value) {
    var el = $(id)
    if (el) el.textContent = value
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

  function setStatus(ok, text) {
    var wrap = $('status')
    var label = $('statusText')
    if (wrap) {
      wrap.classList.toggle('on', !!ok)
      wrap.classList.toggle('off', !ok)
    }
    if (label) label.textContent = text
  }

  function updateStatusClock() {
    if (!lastSuccess) return
    var seconds = Math.max(0, Math.round((Date.now() - lastSuccess) / 1000))
    setStatus(true, '已连接 · ' + seconds + ' 秒前')
  }

  function setBar(id, ratio) {
    var el = $(id)
    if (!el) return
    var value = Math.max(0, Math.min(1, Number(ratio) || 0))
    el.style.width = Math.round(value * 100) + '%'
  }

  function formatDelta(previous, current) {
    var before = Number(previous)
    var after = Number(current)
    if (!Number.isFinite(before) || !Number.isFinite(after)) return '—'
    var delta = after - before
    if (Math.abs(delta) < 0.005) return '±0.00'
    return (delta > 0 ? '+' : '') + delta.toFixed(2)
  }

  function arrowFor(delta) {
    if (delta === '—' || delta === '±0.00') return '→'
    return delta.charAt(0) === '+' ? '↑' : '↓'
  }

  function historyDelta(data, field) {
    var history = data && data.available !== false && Array.isArray(data.history) ? data.history : []
    if (history.length < 2) return '—'
    return formatDelta(history[history.length - 2][field], history[history.length - 1][field])
  }

  function paintDelta(id, delta) {
    var el = $(id)
    if (!el) return
    el.textContent = delta
    el.classList.toggle('up', delta.charAt(0) === '+')
    el.classList.toggle('down', delta.charAt(0) === '-')
  }

  var EMPTY_COPY = {
    no_state_yet: ['这个会话还没聊过', '发一条消息后就有状态'],
    request_failed: ['连不上宿主', '确认 3081 上的 DSH 与插件已加载'],
    bad_state: ['状态文件坏了', '删掉该会话的状态文件后重试'],
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

  function shortSession() {
    if (!SESSION) return '-'
    return SESSION.length > 10 ? SESSION.slice(0, 8) : SESSION
  }

  function isPlausibleMs(ts) {
    return Number.isFinite(ts) && ts > 1000000000000
  }

  function timeLabel(ts) {
    if (!isPlausibleMs(ts)) return '--:--'
    var date = new Date(ts)
    var hh = String(date.getHours()).padStart(2, '0')
    var mm = String(date.getMinutes()).padStart(2, '0')
    return hh + ':' + mm
  }

  var STAT_WORDS = {
    tension: ['松弛', '适中', '略紧'],
    warmth: ['偏冷', '适中', '偏暖'],
    heaviness: ['轻', '适中', '偏沉'],
    clarity: ['模糊', '适中', '清醒'],
  }
  var STAT_NOTES = {
    tension: ['松下来了，不紧绷', '还压得住，但不太放松', '明显绷着，先缓一缓'],
    warmth: ['有点冷，想要靠近', '温度刚好，慢慢热', '心里暖，愿意靠近'],
    heaviness: ['轻一些，能提起劲', '有点分量，还扛得住', '沉下去了，先放一放'],
    clarity: ['有点糊，慢一点', '还算清楚，能接话', '想得清楚，状态在线'],
  }
  var KNOWN_SINCE_KEY = 'biocore-known-since-v1'

  function statLevel(value) {
    var number = Number(value)
    if (!Number.isFinite(number)) return 1
    if (number < 0.35) return 0
    if (number < 0.65) return 1
    return 2
  }

  function paintStatDelta(channel, data, available) {
    var delta = available ? historyDelta(data, channel) : '—'
    paintDelta('delta-' + channel, delta)
    var arrow = $('arrow-' + channel)
    if (!arrow) return
    var direction = arrowFor(delta)
    arrow.textContent = direction
    arrow.classList.toggle('up', direction === '↑')
    arrow.classList.toggle('down', direction === '↓')
  }

  function setStatBar(channel, value) {
    var ratio = Number.isFinite(Number(value)) ? Math.max(0, Math.min(1, Number(value))) : 0
    var fill = $('bar-' + channel)
    if (fill) fill.style.width = Math.round(ratio * 100) + '%'
    var over = $('over-' + channel)
    if (over) {
      over.style.left = '65%'
      over.style.width = ratio > 0.65 ? Math.round((ratio - 0.65) * 100) + '%' : '0%'
    }
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

  function closenessNote(value) {
    var level = statLevel(value)
    return ['还不太熟，慢慢来', '挺自然，不装', '很亲近，想粘着'][level]
  }

  function trustNote(value) {
    var level = statLevel(value)
    return ['还在观察你', '慢慢信你', '信你，能交底'][level]
  }

  function memoryKind(text) {
    var value = String(text || '')
    if (/(喜欢|爱|讨厌|偏好|习惯|最怕|想要|想吃)/.test(value)) return '偏好'
    if (/(答应|约好|说好|明天|后天|记得|见面|一起|会去)/.test(value)) return '约定'
    return '事实'
  }

  function relativeTime(ts) {
    var value = Number(ts)
    if (!Number.isFinite(value) || value <= 1000000000000) return '刚刚'
    var seconds = Math.max(0, Math.round((Date.now() - value) / 1000))
    if (seconds < 60) return '刚刚'
    if (seconds < 3600) return Math.round(seconds / 60) + ' 分钟前'
    if (seconds < 86400) return Math.round(seconds / 3600) + ' 小时前'
    return Math.round(seconds / 86400) + ' 天前'
  }

  function renderNumbers(data) {
    var available = data && data.available !== false
    var intero = (available && data.intero) || {}
    setText('num-v', available ? fmt(data.v) : '–')
    setText('num-tension', available ? fmt(intero.tension) : '–')
    setText('num-warmth', available ? fmt(intero.warmth) : '–')
    setText('num-heaviness', available ? fmt(intero.heaviness) : '–')
    setText('num-clarity', available ? fmt(intero.clarity) : '–')
    setBar('bar-v', available ? (Number(data.v) + 1) / 2 : 0)

    var channels = ['tension', 'warmth', 'heaviness', 'clarity']
    for (var channelIndex = 0; channelIndex < channels.length; channelIndex += 1) {
      var channel = channels[channelIndex]
      var rawValue = available ? intero[channel] : null
      setText('status-' + channel, available ? STAT_WORDS[channel][statLevel(rawValue)] : '—')
      setText('note-' + channel, available ? STAT_NOTES[channel][statLevel(rawValue)] : '—')
      setStatBar(channel, rawValue)
      paintStatDelta(channel, data, available)
    }

    // 情绪卡右侧「张力」复用同一份数据（只换 DOM 节点，不改数据）。
    var emotionTension = available ? intero.tension : null
    setText('num-emotion-tension', available ? fmt(emotionTension) : '–')
    setText('note-emotion-tension', available ? STAT_NOTES.tension[statLevel(emotionTension)] : '—')
    setBar('bar-emotion-tension', emotionTension)
    paintDelta('delta-emotion-tension', available ? historyDelta(data, 'tension') : '—')
    var emotionArrow = $('arrow-emotion-tension')
    if (emotionArrow) {
      var emotionDirection = arrowFor(available ? historyDelta(data, 'tension') : '—')
      emotionArrow.textContent = emotionDirection
      emotionArrow.classList.toggle('up', emotionDirection === '↑')
      emotionArrow.classList.toggle('down', emotionDirection === '↓')
    }
    setText('moodAside', available ? emotionMoodWord(data.v) : '—')
    setText('moodNote', available ? emotionMoodWord(data.v) : '—')

    var tone = available ? scalarLabel(data.tone, '') : ''
    setText('word-tone', tone || (available ? '正常' : '–'))
    setText('word-relation', available ? (scalarLabel(data.relation, '') || '–') : '–')
    setText('word-autonomy', available ? (scalarLabel(data.autonomy, '') || '–') : '–')
    if (available) {
      setText('motivation', data.motivation || '安静待着，等你开口')
    }
    var fields = ['v', 'tension', 'warmth', 'heaviness', 'clarity']
    for (var index = 0; index < fields.length; index += 1) {
      var delta = available ? historyDelta(data, fields[index]) : '—'
      paintDelta('delta-' + fields[index], delta)
    }
    var arrow = $('arrow-v')
    if (arrow) {
      var direction = arrowFor(available ? historyDelta(data, 'v') : '—')
      arrow.textContent = direction
      arrow.classList.toggle('up', direction === '↑')
      arrow.classList.toggle('down', direction === '↓')
    }
  }

  function emotionMoodWord(value) {
    var number = Number(value)
    if (!Number.isFinite(number)) return '—'
    if (number > 0.25) return '偏高'
    if (number < -0.25) return '偏低'
    return '平稳'
  }

  function renderMetrics(data) {
    var box = $('metrics')
    if (!box) return
    box.textContent = ''
    var available = data && data.available !== false
    var rel = (available && data.relationship_metrics) || {}
    var closeness = Number(rel.closeness)
    var trust = Number(rel.trust)
    var conflict = Number(rel.conflict)
    var safeCloseness = Number.isFinite(closeness) ? closeness : null
    var safeTrust = Number.isFinite(trust) ? trust : null
    var safeConflict = Number.isFinite(conflict) ? conflict : 0

    var grid = document.createElement('div')
    grid.className = 'relation-grid'
    var columns = [
      ['亲近', safeCloseness, closenessNote(safeCloseness)],
      ['信任', safeTrust, trustNote(safeTrust)],
    ]
    for (var i = 0; i < columns.length; i += 1) {
      var column = document.createElement('div')
      column.className = 'relation-col'
      var label = document.createElement('span')
      label.className = 'relation-label'
      label.textContent = columns[i][0]
      var value = document.createElement('strong')
      value.className = 'relation-value'
      value.textContent = available && columns[i][1] !== null ? fmt(columns[i][1]) : '–'
      var note = document.createElement('p')
      note.className = 'relation-note'
      note.textContent = available ? columns[i][2] : '—'
      column.appendChild(label)
      column.appendChild(value)
      column.appendChild(note)
      grid.appendChild(column)
    }
    box.appendChild(grid)

    var count = Number(rel.conflict_count)
    if (!Number.isFinite(count)) count = safeConflict > 0 ? Math.round(safeConflict * 10) : 0
    var foot = document.createElement('div')
    foot.className = 'relation-foot'
    foot.textContent = available
      ? '认识 ' + knownDays() + ' 天 · 冲突 ' + count + ' 次 · 依恋 ' + attachmentOf(safeTrust || 0, safeCloseness || 0, safeConflict)
      : '认识 — 天 · 冲突 — 次 · 依恋 —'
    box.appendChild(foot)
  }

  function renderStats(data) {
    var available = data && data.available !== false
    setText('statMood', available ? fmt(data.v) : '–')
    var relation = available && data.relation ? data.relation.stage : ''
    var rel = available && data.relationship_metrics ? Number(data.relationship_metrics.closeness) : NaN
    setText('statRelation', relation || (Number.isFinite(rel) ? fmt(rel) : '–'))
    setText('statMemory', String(available && typeof data.memory_count === 'number' ? data.memory_count : 0))
    var total = available && typeof data.memory_count === 'number' ? data.memory_count : 0
    var capacity = Math.max(0, Math.min(1, total / 200))
    var fill = $('capFill')
    if (fill) fill.style.width = Math.round(capacity * 100) + '%'
    setText('capText', total + ' / 200')
  }

  function renderMemorySummary(data) {
    var available = data && data.available !== false
    var rows = available && Array.isArray(data.memory_recent) ? data.memory_recent : []
    var total = available && typeof data.memory_count === 'number' ? data.memory_count : rows.length
    var today = 0
    var dayStart = new Date()
    dayStart.setHours(0, 0, 0, 0)
    for (var i = 0; i < rows.length; i += 1) {
      var ts = Number(rows[i] && rows[i].ts)
      if (Number.isFinite(ts) && ts > 1000000000000 && ts >= dayStart.getTime()) today += 1
    }
    setText('memTotal', String(total))
    setText('memToday', String(available ? today : '—'))
    setText('memRecall', '—')
  }

  function renderRuntime(data) {
    var available = data && data.available !== false
    var seconds = Math.max(0, Math.round((Date.now() - PAGE_START) / 1000))
    var uptime = seconds < 60 ? seconds + ' 秒'
      : (seconds < 3600 ? Math.round(seconds / 60) + ' 分钟' : Math.round(seconds / 3600) + ' 小时')
    setText('runtimeUptime', uptime)
    var turns = available && Array.isArray(data.history) ? data.history.length : '—'
    setText('runtimeTurns', String(turns))
    var mode = $('embeddingMode')
    setText('runtimeVector', mode ? (mode.value || 'auto') : '—')
  }

  function renderMemories(data) {
    var list = $('memoryList')
    var hint = $('memoryHint')
    if (!list) return
    list.textContent = ''
    var rows = data && data.available !== false && Array.isArray(data.memory_recent)
      ? data.memory_recent : []
    var total = data && data.available !== false && typeof data.memory_count === 'number'
      ? data.memory_count : rows.length
    setText('memoryHint', '共 ' + total + ' 条')
    if (!rows.length) {
      var empty = document.createElement('li')
      empty.className = 'empty'
      empty.textContent = '还没有记忆，聊几句就会记住'
      list.appendChild(empty)
      return
    }
    for (var i = 0; i < rows.length; i += 1) {
      var item = rows[i] || {}
      var text = String(item.text || '').slice(0, 40)
      var li = document.createElement('li')
      var head = document.createElement('div')
      head.className = 'memory-head'
      var time = document.createElement('time')
      time.textContent = relativeTime(Number(item.ts))
      var tag = document.createElement('span')
      tag.className = 'memory-tag'
      tag.textContent = memoryKind(item.text)
      head.appendChild(time)
      head.appendChild(tag)
      var body = document.createElement('span')
      body.className = 'memory-text'
      body.textContent = text
      li.appendChild(head)
      li.appendChild(body)
      list.appendChild(li)
    }
  }

  function mergePoints(data) {
    var extras = data && data.available !== false ? data : {}
    var history = Array.isArray(extras.history) ? extras.history : []
    if (history.length) {
      return history.map(function (item) {
        return { ts: Number(item.ts) || 0, v: Number(item.v) || 0 }
      })
    }
    if (seriesPoints && seriesPoints.length) {
      return seriesPoints.map(function (item) {
        return { ts: Number(item.ts) / 1000, v: Number(item.value) || 0 }
      })
    }
    if (data && data.available !== false) {
      localPoints.push({ ts: Date.now() / 1000, v: Number(data.v) || 0 })
      if (localPoints.length > 120) localPoints = localPoints.slice(-120)
    }
    return localPoints.slice()
  }

  function drawChartInto(canvas, data) {
    if (!canvas) return
    var points = mergePoints(data)
    var wrapWidth = canvas.clientWidth || canvas.parentElement.clientWidth || 600
    var wrapHeight = 170
    var dpr = window.devicePixelRatio || 1
    canvas.width = Math.round(wrapWidth * dpr)
    canvas.height = Math.round(wrapHeight * dpr)
    var ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, wrapWidth, wrapHeight)

    var styles = window.getComputedStyle(document.documentElement)
    var lineColor = styles.getPropertyValue('--accent').trim() || '#4176e6'
    var gridColor = styles.getPropertyValue('--line').trim() || 'rgba(127,127,127,.2)'
    var mutedColor = styles.getPropertyValue('--text-3').trim() || '#81858c'
    var amber = styles.getPropertyValue('--amber').trim() || '#f7ad31'

    var padL = 34
    var padR = 12
    var padT = 12
    var padB = 20
    var plotW = Math.max(10, wrapWidth - padL - padR)
    var plotH = Math.max(10, wrapHeight - padT - padB)

    ctx.strokeStyle = gridColor
    ctx.lineWidth = 1
    ctx.font = '10px -apple-system, "Microsoft YaHei", sans-serif'
    ctx.fillStyle = mutedColor
    for (var g = 0; g < 5; g += 1) {
      var y = padT + (plotH * g) / 4
      ctx.beginPath()
      ctx.moveTo(padL, y)
      ctx.lineTo(padL + plotW, y)
      ctx.stroke()
      if (g === 0 || g === 4) {
        var label = g === 0 ? '1.0' : '-1.0'
        ctx.fillText(label, 6, y + 3)
      }
    }
    if (points.length > 1) {
      var minTs = points[0].ts
      var maxTs = points[points.length - 1].ts
      var span = maxTs - minTs || 1
      var toX = function (ts) { return padL + ((ts - minTs) / span) * plotW }
      var toY = function (v) { return padT + ((1 - Math.max(-1, Math.min(1, v))) / 2) * plotH }

      ctx.beginPath()
      for (var i = 0; i < points.length; i += 1) {
        var x = toX(points[i].ts)
        var py = toY(points[i].v)
        if (i === 0) ctx.moveTo(x, py)
        else ctx.lineTo(x, py)
      }
      ctx.strokeStyle = lineColor
      ctx.lineWidth = 2
      ctx.lineJoin = 'round'
      ctx.stroke()

      var last = points[points.length - 1]
      ctx.fillStyle = lineColor
      ctx.beginPath()
      ctx.arc(toX(last.ts), toY(last.v), 3, 0, Math.PI * 2)
      ctx.fill()

      var events = data && data.available !== false && Array.isArray(data.memory_recent)
        ? data.memory_recent : []
      ctx.fillStyle = amber
      for (var e = 0; e < events.length; e += 1) {
        var eventTs = Number(events[e].ts) / 1000
        if (!Number.isFinite(eventTs) || eventTs < minTs || eventTs > maxTs) continue
        var eventY = toY(last.v)
        for (var p = 0; p < points.length; p += 1) {
          if (points[p].ts >= eventTs) { eventY = toY(points[p].v); break }
        }
        ctx.beginPath()
        ctx.arc(toX(eventTs), eventY, 2.6, 0, Math.PI * 2)
        ctx.fill()
      }
      setText(canvas.id === 'deskChart' ? 'deskChartHint' : 'chartHint', '单线 + ' + events.length + ' 个事件点')
    } else {
      ctx.strokeStyle = lineColor
      ctx.lineWidth = 2
      var midY = padT + plotH / 2
      ctx.beginPath()
      ctx.moveTo(padL, midY)
      ctx.lineTo(padL + plotW, midY)
      ctx.stroke()
      if (points.length === 1) {
        ctx.fillStyle = lineColor
        ctx.beginPath()
        ctx.arc(padL + plotW - 3, padT + ((1 - Math.max(-1, Math.min(1, points[0].v))) / 2) * plotH, 3, 0, Math.PI * 2)
        ctx.fill()
      }
      setText(canvas.id === 'deskChart' ? 'deskChartHint' : 'chartHint', '积累中 · 单点')
    }
    ctx.fillStyle = mutedColor
    ctx.fillText('24 小时', padL, wrapHeight - 5)
  }

  function renderChart(data) {
    drawChartInto($('moodChart'), data)
    drawChartInto($('deskChart'), data)
  }

  var DESK_HIDDEN_KEY = 'biocore-desk-hidden'
  var RAIL_KEY = 'biocore-rail-collapsed'

  function copyText(fromId, toId) {
    var from = $(fromId)
    var to = $(toId)
    if (from && to) to.textContent = from.textContent
  }

  function copyBar(fromId, toId) {
    var from = $(fromId)
    var to = $(toId)
    if (from && to) to.style.width = from.style.width
  }

  /** Desktop grid mirrors the mobile DOM (single data source, no duplicate state). */
  function deskSync(data) {
    copyText('motivation', 'deskMotivation')
    copyText('statusText', 'deskStatusText')
    var mobileStatus = $('status')
    var deskStatus = $('deskStatus')
    if (mobileStatus && deskStatus) {
      deskStatus.className = 'status' + (mobileStatus.classList.contains('on') ? ' on' : (mobileStatus.classList.contains('off') ? ' off' : ''))
    }
    copyText('num-v', 'deskNumV')
    copyText('moodNote', 'deskWordV')
    copyText('arrow-v', 'deskArrowV')
    copyText('delta-v', 'deskDeltaV')
    copyBar('bar-v', 'deskBarV')
    copyText('num-emotion-tension', 'deskNumTension')
    copyText('status-tension', 'deskWordTension')
    copyText('arrow-emotion-tension', 'deskArrowTension')
    copyText('delta-emotion-tension', 'deskDeltaTension')
    copyBar('bar-emotion-tension', 'deskBarTension')
    copyText('runtimeUptime', 'deskUptime')
    copyText('runtimeTurns', 'deskTurns')
    copyText('runtimeVector', 'deskVector')
    copyText('capText', 'deskCapText')
    copyBar('capFill', 'deskCapFill')
    copyText('dataDirText', 'deskDataDir')
    copyText('memTotal', 'deskTodayMem')
    copyText('runtimeTurns', 'deskTodayTurns')
    copyText('memoryHint', 'deskMemoryHint')
    var memoryFrom = $('memoryList')
    var memoryTo = $('deskMemoryList')
    if (memoryFrom && memoryTo) memoryTo.innerHTML = memoryFrom.innerHTML
    var available = data && data.available !== false
    var intero = (available && data.intero) || {}
    var channels = [['tension', 'deskAvgTension'], ['warmth', 'deskAvgWarmth'], ['heaviness', 'deskAvgHeaviness'], ['clarity', 'deskAvgClarity']]
    for (var i = 0; i < channels.length; i += 1) {
      var value = Number(intero[channels[i][0]])
      setText(channels[i][1], Number.isFinite(value) ? fmt(value) : '—')
    }
    var rel = (available && data.relationship_metrics) || {}
    setText('deskRelCloseness', available && Number.isFinite(Number(rel.closeness)) ? fmt(rel.closeness) : '—')
    setText('deskRelTrust', available && Number.isFinite(Number(rel.trust)) ? fmt(rel.trust) : '—')
    setText('deskRelConflict', available && Number.isFinite(Number(rel.conflict)) ? fmt(rel.conflict) : '—')
    setText('deskTodayProactive', '—')
    var personaBox = $('personaText')
    var personaChars = personaBox ? String(personaBox.value || '').replace(/s+/g, '').length : 0
    setText('deskPersona', personaChars ? '已载入' : '未载入')
    setText('deskPersonaNote', personaChars ? (personaChars + ' 字 · 编译后只改倾向') : '还没有写人设')
  }

  function deskCards() {
    return Array.prototype.slice.call(document.querySelectorAll('#deskGrid .desk-card'))
  }

  function deskCardId(card, index) {
    if (!card.id) card.id = 'desk-card-' + index
    return card.id
  }

  function readDeskHidden() {
    try {
      var raw = window.localStorage.getItem(DESK_HIDDEN_KEY)
      var list = raw ? JSON.parse(raw) : []
      return Array.isArray(list) ? list : []
    } catch (e) { return [] }
  }

  function writeDeskHidden(list) {
    try { window.localStorage.setItem(DESK_HIDDEN_KEY, JSON.stringify(list)) } catch (e) { /* ignore */ }
  }

  function applyDeskHidden() {
    var hidden = readDeskHidden()
    deskCards().forEach(function (card, index) {
      card.classList.toggle('is-desk-hidden', hidden.indexOf(deskCardId(card, index)) >= 0)
    })
  }

  function ensureDeskHides() {
    deskCards().forEach(function (card, index) {
      deskCardId(card, index)
      if (card.querySelector('[data-desk-hide]')) return
      var button = document.createElement('button')
      button.type = 'button'
      button.className = 'desk-hide'
      button.setAttribute('data-desk-hide', '')
      button.setAttribute('aria-label', '隐藏这张卡片')
      button.textContent = '−'
      card.appendChild(button)
    })
  }

  function deskLabel(card) {
    var named = card.getAttribute('data-card-label')
    if (named) return named
    var title = card.querySelector('h2')
    return title ? title.textContent.trim() : card.id
  }

  function restoreDeskCard(id) {
    writeDeskHidden(readDeskHidden().filter(function (item) { return item !== id }))
    applyDeskHidden()
    renderAddCards()
    layoutToast('已恢复卡片')
  }

  function onDeskEditClick(event) {
    var target = event.target
    var add = target && target.closest ? target.closest('[data-desk-add]') : null
    if (add) {
      event.preventDefault()
      restoreDeskCard(add.getAttribute('data-desk-add'))
      return
    }
    var hide = target && target.closest ? target.closest('[data-desk-hide]') : null
    if (!hide) return
    event.preventDefault()
    var card = hide.closest('.desk-card')
    if (!card) return
    var hidden = readDeskHidden()
    var id = card.id
    if (hidden.indexOf(id) < 0) hidden.push(id)
    writeDeskHidden(hidden)
    applyDeskHidden()
    renderAddCards()
    layoutToast('已隐藏「' + deskLabel(card) + '」')
  }

  function railApply(collapsed) {
    document.body.classList.toggle('rail-collapsed', !!collapsed)
    var button = $('railToggle')
    if (!button) return
    var label = collapsed ? '展开导航' : '收起导航'
    button.setAttribute('aria-expanded', String(!collapsed))
    button.setAttribute('aria-label', label)
    button.setAttribute('title', label)
  }

  function railInit() {
    var storage = layoutStorage()
    var collapsed = false
    try { collapsed = !!storage && storage.getItem(RAIL_KEY) === '1' } catch (e) { collapsed = false }
    railApply(collapsed)
    var button = $('railToggle')
    if (!button) return
    button.addEventListener('click', function () {
      var next = !document.body.classList.contains('rail-collapsed')
      railApply(next)
      try { if (storage) storage.setItem(RAIL_KEY, next ? '1' : '0') } catch (e) { /* ignore */ }
    })
  }

  function updateFooter(data) {
    var memory = data && data.available !== false && typeof data.memory_count === 'number'
      ? data.memory_count : '-'
    setText('footerMeta', '会话 ' + shortSession() + ' · 记忆 ' + memory + ' · 每 2 秒刷新')
  }

  function render(data) {
    lastData = data
    renderNumbers(data)
    renderMetrics(data)
    renderMemories(data)
    renderMemorySummary(data)
    renderStats(data)
    renderRuntime(data)
    renderChart(data)
    updateFooter(data)
    deskSync(data)
    deskSync(data)
    if (!data) renderEmpty('request_failed')
    else if (data.available === false) renderEmpty(String(data.reason || 'request_failed'))
    else clearEmpty()
  }

  function fetchState() {
    if (!SESSION) {
      setStatus(false, '未连接')
      setText('motivation', '没有会话参数，先在聊天会话里打开')
      return Promise.resolve(null)
    }
    return fetch('/api/biocore/state?session=' + encodeURIComponent(SESSION), {
      headers: { accept: 'application/json' },
    })
      .then(function (response) { return response.json() })
      .then(function (data) {
        lastSuccess = Date.now()
        setStatus(true, '已连接 · 0 秒前')
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
        if (body && body.series && Array.isArray(body.series.v) && body.series.v.length) {
          seriesPoints = body.series.v
        }
      })
      .catch(function () { /* optional: state extras already cover the chart */ })
  }

  function switchPost(key, value) {
    return fetch('/api/biocore/switch', {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ key: key, value: !!value, confirm: true }),
    }).then(function (response) { return response.json() })
  }

  function paintSwitchState(key, checked) {
    var states = document.querySelectorAll('[data-switch-state="' + key + '"]')
    for (var i = 0; i < states.length; i += 1) states[i].textContent = '当前：' + (checked ? '开' : '关')
  }

  function setSwitchInputs(key, checked) {
    var inputs = document.querySelectorAll('input[data-switch="' + key + '"]')
    for (var i = 0; i < inputs.length; i += 1) inputs[i].checked = !!checked
    paintSwitchState(key, checked)
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
    for (var i = 0; i < SWITCHES.length; i += 1) {
      var meta = SWITCHES[i]
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
      var state = document.createElement('span')
      state.className = 'switch-state'
      state.setAttribute('data-switch-state', meta.key)
      state.textContent = '当前：关'
      textWrap.appendChild(title)
      textWrap.appendChild(desc)
      textWrap.appendChild(state)
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
    setText('switchHint', '写入中…')
    switchPost(key, next)
      .then(function (body) {
        if (body && body.ok === true && body.switches) {
          applySwitchValues(body.switches)
          setText('switchHint', '已写入宿主设置')
        } else {
          setSwitchInputs(key, !next)
          setText('switchHint', '写入失败：' + ((body && body.reason) || 'unknown'))
        }
      })
      .catch(function () {
        setSwitchInputs(key, !next)
        setText('switchHint', '写入失败：未连接')
      })
      .then(function () {
        switchBusy = false
        input.disabled = false
      })
  }

  var personaText = ''
  var personaDirty = false

  function personaStatus(message, tone) {
    var el = $('personaStatus')
    if (!el) return
    el.textContent = message
    el.classList.toggle('persona-status-error', tone === 'error')
    el.classList.toggle('persona-status-ok', tone === 'ok')
  }

  function renderPersonaPreview(rows) {
    var box = $('personaPreview')
    if (!box) return
    box.textContent = ''
    var list = Array.isArray(rows) ? rows : []
    for (var i = 0; i < list.length; i += 1) {
      var row = list[i] || {}
      var item = document.createElement('div')
      var label = document.createElement('span')
      label.textContent = String(row.label || row.key || '')
      var value = document.createElement('strong')
      value.textContent = row.value === null || row.value === undefined ? '–' : String(row.value)
      item.appendChild(label)
      item.appendChild(value)
      box.appendChild(item)
    }
  }

  function loadPersona() {
    return fetch('/api/biocore/persona', { headers: { accept: 'application/json' } })
      .then(function (response) { return response.json() })
      .then(function (body) {
        if (!body || body.ok !== true) {
          personaStatus('人设文件坏了：' + ((body && body.reason) || 'bad_profile'), 'error')
          return body
        }
        personaText = String(body.text || '')
        var area = $('personaText')
        if (area) area.value = personaText
        personaDirty = false
        renderPersonaPreview(body.preview)
        personaStatus(body.text ? '已载入' : '未设置人设', body.text ? 'ok' : '')
        return body
      })
      .catch(function () {
        personaStatus('连不上宿主，读不到人设', 'error')
        return null
      })
  }

  function savePersona() {
    var area = $('personaText')
    var button = $('personaSave')
    var text = area ? area.value : ''
    if (!text.trim()) {
      personaStatus('编译失败：人设不能为空', 'error')
      return
    }
    if (!window.confirm('确定保存并编译人设吗？这会覆盖当前数值画像。')) return
    personaStatus('编译中…')
    if (button) button.disabled = true
    fetch('/api/biocore/persona', {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ text: text, confirm: true }),
    })
      .then(function (response) { return response.json() })
      .then(function (body) {
        if (!body || body.ok !== true) {
          personaStatus('编译失败：' + ((body && body.reason) || 'unknown'), 'error')
          if (body && body.detail) setText('personaHint', String(body.detail).slice(0, 120))
          return body
        }
        personaDirty = false
        personaText = String(body.text || text)
        renderPersonaPreview(body.preview)
        personaStatus('已编译 · ' + new Date().toLocaleTimeString(), 'ok')
        return body
      })
      .catch(function () {
        personaStatus('编译失败：连不上宿主', 'error')
        return null
      })
      .then(function () {
        if (button) button.disabled = false
      })
  }

  function personaPersistHint(message, tone) {
    var el = $('personaPersistHint')
    if (!el) return
    el.textContent = message
    el.classList.toggle('persona-status-error', tone === 'error')
    el.classList.toggle('persona-status-ok', tone === 'ok')
  }

  function loadPersonaPersist() {
    return fetch('/api/biocore/persona/apply', { headers: { accept: 'application/json' } })
      .then(function (response) { return response.json() })
      .then(function (body) {
        var applyButton = $('personaPersist')
        var rollbackButton = $('personaRollback')
        if (!body || body.ok !== true) {
          personaPersistHint('常驻：宿主不可用（' + ((body && body.reason) || 'unknown') + '）', 'error')
          if (rollbackButton) rollbackButton.hidden = true
          return body
        }
        if (body.applied) {
          personaPersistHint('常驻：已应用 · 预设 ' + String(body.preset_id || '') + ' · ' + Number(body.chars || 0) + ' 字' + (body.matches_preset ? '' : '（预设已变更）'), body.matches_preset ? 'ok' : 'error')
          if (rollbackButton) rollbackButton.hidden = false
          if (applyButton) applyButton.textContent = '重新应用常驻'
        } else {
          personaPersistHint('常驻：未应用（' + String(body.reason || 'not_applied') + '）', '')
          if (rollbackButton) rollbackButton.hidden = !body.has_backup
          if (applyButton) applyButton.textContent = '设为常驻模式'
        }
        return body
      })
      .catch(function () {
        personaPersistHint('常驻：连不上宿主', 'error')
        return null
      })
  }

  function applyPersonaPersist() {
    var button = $('personaPersist')
    if (!window.confirm('把人设写入 DSH 默认预设并常驻？\n应用后由预设提供人设，插件不再每轮注入人设；可随时恢复原人设。')) return
    personaPersistHint('常驻：写入预设中…')
    if (button) button.disabled = true
    fetch('/api/biocore/persona/apply', {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ confirm: true }),
    })
      .then(function (response) { return response.json() })
      .then(function (body) {
        if (!body || body.ok !== true) {
          personaPersistHint('常驻：应用失败（' + ((body && body.reason) || 'unknown') + '）', 'error')
          if (body && body.detail) personaStatus(String(body.detail).slice(0, 120), 'error')
          return body
        }
        personaStatus('已写入常驻预设', 'ok')
        return loadPersonaPersist()
      })
      .catch(function () {
        personaPersistHint('常驻：应用失败，连不上宿主', 'error')
        return null
      })
      .then(function () {
        if (button) button.disabled = false
      })
  }

  function rollbackPersonaPersist() {
    var button = $('personaRollback')
    if (!window.confirm('恢复应用常驻前的预设人设？')) return
    personaPersistHint('常驻：恢复中…')
    if (button) button.disabled = true
    fetch('/api/biocore/persona/apply', {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ confirm: true, rollback: true }),
    })
      .then(function (response) { return response.json() })
      .then(function (body) {
        if (!body || body.ok !== true) {
          personaPersistHint('常驻：恢复失败（' + ((body && body.reason) || 'unknown') + '）', 'error')
          return body
        }
        personaStatus('已恢复原预设人设', 'ok')
        return loadPersonaPersist()
      })
      .catch(function () {
        personaPersistHint('常驻：恢复失败，连不上宿主', 'error')
        return null
      })
      .then(function () {
        if (button) button.disabled = false
      })
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

  var MODEL_DRAFT_KEY = 'biocore-panel-model-draft-v1'

  function modelInputs() {
    return {
      mode: $('embeddingMode'),
      baseUrl: $('modelBaseUrl'),
      model: $('modelName'),
      apiKey: $('modelApiKey'),
      localPath: $('modelLocalPath'),
    }
  }

  function modelValues() {
    var fields = modelInputs()
    return {
      mode: fields.mode ? fields.mode.value : 'auto',
      base_url: fields.baseUrl ? fields.baseUrl.value.trim() : '',
      model: fields.model ? fields.model.value.trim() : '',
      api_key: fields.apiKey ? fields.apiKey.value : '',
      local_path: fields.localPath ? fields.localPath.value.trim() : '',
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

  function fillRerank(value) {
    var fields = rerankInputs()
    var data = value && typeof value === 'object' ? value : {}
    if (fields.enabled) fields.enabled.checked = data.enabled === true
    if (fields.baseUrl) fields.baseUrl.value = String(data.base_url || 'https://api.jina.ai/v1')
    if (fields.model) fields.model.value = String(data.model || 'jina-reranker-v2-base-multilingual')
    if (fields.apiKey && data.api_key) fields.apiKey.value = '••••••••'
    if (fields.topN) fields.topN.value = String(data.top_n || 20)
  }

  function fillModel(values) {
    var fields = modelInputs()
    var data = values || {}
    if (fields.mode && data.mode) fields.mode.value = String(data.mode)
    if (fields.baseUrl) fields.baseUrl.value = String(data.base_url || '')
    if (fields.model) fields.model.value = String(data.model || '')
    if (fields.apiKey && data.api_key) fields.apiKey.value = '••••••••'
    if (fields.localPath) fields.localPath.value = String(data.local_path || '')
  }

  function setModelStatus(message) {
    setText('modelStatus', message)
  }

  function saveModelDraft(values) {
    try { window.localStorage.setItem(MODEL_DRAFT_KEY, JSON.stringify(values)) } catch { /* ignore */ }
  }

  function loadModelDraft() {
    try {
      var raw = window.localStorage.getItem(MODEL_DRAFT_KEY)
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  }

  function expressionInputs() {
    return {
      toggle: $('expressionToggle'),
      fields: $('expressionFields'),
      target: $('lengthTarget'),
      max: $('lengthMax'),
      status: $('expressionStatus'),
      hint: $('expressionHint'),
      save: $('expressionSave'),
    }
  }

  function syncExpressionFields() {
    var inputs = expressionInputs()
    if (!inputs.toggle) return
    var on = inputs.toggle.checked === true
    if (inputs.fields) inputs.fields.hidden = !on
    if (inputs.status) inputs.status.textContent = on ? '按目标/上限约束' : '长度由人设决定'
    if (inputs.hint) inputs.hint.textContent = on ? '打开后按目标/上限字数约束表达' : '关闭时长度由人设决定'
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
      if (inputs.status) inputs.status.textContent = '保存中…'
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
          if (inputs.status) inputs.status.textContent = '保存失败：' + ((body && body.reason) || 'unknown')
        })
        .catch(function () {
          if (inputs.status) inputs.status.textContent = '保存失败：连不上宿主'
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

  function bindAdvanced() {
    var reset = $('resetLayout')
    if (reset) reset.addEventListener('click', function () { resetLayout() })
    var clear = $('clearDraft')
    if (clear) clear.addEventListener('click', function () {
      try { window.localStorage.removeItem(MODEL_DRAFT_KEY) } catch { /* ignore */ }
      setText('advancedHint', '已清空本地草稿')
      layoutToast('已清空本地草稿')
    })
  }

  function bindModelActions() {
    var button = $('modelSave')
    var test = $('modelTest')
    var rerankTest = $('rerankTest')
    function runConnectionTest(kind, buttonEl) {
      setModelStatus((kind === 'rerank' ? 'Rerank ' : 'Embedding ') + '测试中…')
      if (buttonEl) buttonEl.disabled = true
      fetch('/api/biocore/settings/test', {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({ kind }),
      })
        .then(function (response) { return response.ok ? response.json() : null })
        .then(function (body) {
          if (body && body.ok === true) {
            setModelStatus(kind + ' 连接正常 · ' + (body.elapsed_ms || 0) + 'ms')
            return
          }
          setModelStatus(kind + ' 连接失败：' + ((body && (body.detail || body.reason)) || 'no_response'))
        })
        .catch(function () { setModelStatus(kind + ' 连接失败：连不上宿主') })
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
      setModelStatus('保存中…')
      button.disabled = true
      fetch('/api/biocore/settings', {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({ confirm: true, embedding: values, rerank }),
      })
        .then(function (response) { return response.ok ? response.json() : null })
        .then(function (body) {
          if (body && body.ok === true) {
            setModelStatus(body.restart_required === false ? '已保存' : '已保存，重启后生效')
            return
          }
          saveModelDraft(values)
          setModelStatus('宿主接口未开启：已存本地草稿')
        })
        .catch(function () {
          saveModelDraft(values)
          setModelStatus('宿主接口未开启：已存本地草稿')
        })
        .then(function () { button.disabled = false })
    })
  }

  function loadSwitches() {
    if (!SESSION) return
    fetch('/api/biocore/switch', { headers: { accept: 'application/json' } })
      .then(function (response) { return response.json() })
      .then(function (body) {
        if (body && body.ok === true && body.switches) applySwitchValues(body.switches)
      })
      .catch(function () { /* keep defaults, POST still tries */ })
  }

  var TRACE_LIMIT = 3

  function traceFormatter() {
    return window.BioTraceView || null
  }

  function renderTrace(body) {
    var list = $('traceList')
    var hint = $('traceHint')
    if (!list) return
    list.textContent = ''
    var view = traceFormatter()
    var formatted = view
      ? view.formatPayload(body, TRACE_LIMIT)
      : { rows: [], empty: true, error: false, hint: '还没有记录' }
    setText('traceHint', formatted.hint)
    if (formatted.empty) {
      var empty = document.createElement('p')
      empty.className = 'empty'
      empty.textContent = formatted.error ? '读取失败' : '还没有记录'
      list.appendChild(empty)
      return
    }
    var labels = ['状态变化', '意图', '长度来源', '闸命中', '记忆']
    for (var i = 0; i < formatted.rows.length; i += 1) {
      var row = formatted.rows[i] || {}
      var item = document.createElement('article')
      item.className = 'trace-row'
      var head = document.createElement('div')
      head.className = 'trace-head'
      var time = document.createElement('span')
      time.textContent = row.time || '--:--'
      var badge = document.createElement('span')
      badge.className = 'trace-badge'
      badge.textContent = '#' + (formatted.rows.length - i)
      head.appendChild(time)
      head.appendChild(badge)
      var grid = document.createElement('dl')
      grid.className = 'trace-grid'
      var values = [row.changes, row.intent, row.length, row.gate, row.memory]
      for (var index = 0; index < labels.length; index += 1) {
        var dt = document.createElement('dt')
        dt.textContent = labels[index]
        var dd = document.createElement('dd')
        dd.textContent = values[index] || '—'
        grid.appendChild(dt)
        grid.appendChild(dd)
      }
      item.appendChild(head)
      item.appendChild(grid)
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
      .then(function (body) {
        renderTrace(body)
        return body
      })
      .catch(function () {
        renderTrace(null)
        return null
      })
  }

  var PAGE_START = Date.now()
  var layoutApi = typeof window !== 'undefined' ? window.BioCorePanelLayout : null
  var layoutState = null
  var layoutEditing = false
  var toastTimer = null
  var drag = null
  var pressTimer = null
  var pressStart = null
  var LONG_PRESS_MS = 400
  var ENTRY_LONG_PRESS_MS = 600
  var MOVE_CANCEL_PX = 8
  var entryTimer = null
  var entryStart = null

  function layoutStorage() {
    try { return window.localStorage } catch { return null }
  }

  function layoutCards() {
    var container = $('cards')
    if (!container) return []
    return Array.prototype.slice.call(container.querySelectorAll('[data-card-id]'))
  }

  /** Movable DOM slot for a card: grouped cards share one slot (channel grid). */
  function layoutSlotOf(card) {
    var slot = card && card.closest ? card.closest('[data-layout-slot]') : null
    return slot || card
  }

  function layoutToast(message) {
    var el = $('layoutToast')
    if (!el) return
    el.textContent = String(message || '')
    el.hidden = false
    if (toastTimer) window.clearTimeout(toastTimer)
    toastTimer = window.setTimeout(function () { el.hidden = true }, 2600)
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

  var activeTab = 'dashboard'

  function applyTabGroup() {
    if (!layoutApi) return
    var group = layoutEditing ? null : (layoutApi.TAB_GROUPS[activeTab] || [])
    var panel = $('panel')
    if (panel) {
      var tabsKnown = ['dashboard', 'memory', 'relation', 'settings', 'tools']
      for (var t = 0; t < tabsKnown.length; t += 1) {
        panel.classList.toggle('tab-' + tabsKnown[t], activeTab === tabsKnown[t])
      }
    }
    var cards = layoutCards()
    for (var i = 0; i < cards.length; i += 1) {
      var id = cards[i].getAttribute('data-card-id')
      cards[i].classList.toggle('tab-hidden', !!group && group.indexOf(id) < 0)
    }
    var items = document.querySelectorAll('[data-tab]')
    for (var j = 0; j < items.length; j += 1) {
      var on = items[j].getAttribute('data-tab') === activeTab
      items[j].classList.toggle('active', on)
      items[j].setAttribute('aria-selected', on ? 'true' : 'false')
    }
  }

  function setActiveTab(tab) {
    if (!layoutApi || !layoutApi.TAB_GROUPS[tab]) return
    activeTab = tab
    applyTabGroup()
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

  function applyLayout(next, persist) {
    if (!layoutApi) return
    layoutState = layoutApi.normalizeLayout(next)
    if (persist !== false) layoutApi.writeLayout(layoutStorage(), layoutState)
    var container = $('cards')
    if (!container) return
    var cards = layoutCards()
    var byId = {}
    for (var i = 0; i < cards.length; i += 1) {
      var card = cards[i]
      ensureHideButton(card)
      byId[card.getAttribute('data-card-id')] = card
    }
    var ordered = layoutApi.sortCards(Object.keys(byId), layoutState.order)
    var orderedSlots = []
    for (var j = 0; j < ordered.length; j += 1) {
      var id = ordered[j]
      var node = byId[id]
      if (!node) continue
      node.classList.toggle('is-hidden', layoutState.hidden.indexOf(id) >= 0)
      var slot = layoutSlotOf(node)
      if (orderedSlots.indexOf(slot) < 0) { orderedSlots.push(slot); container.appendChild(slot) }
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

  function setLayoutEditing(on) {
    layoutEditing = !!on
    var panel = $('panel')
    if (panel) panel.classList.toggle('layout-editing', layoutEditing)
    var title = $('pageTitle')
    if (title) title.textContent = layoutEditing ? '编辑仪表盘' : 'BioCore'
    var fab = $('layoutFab')
    if (fab) fab.hidden = layoutEditing
    var done = $('layoutDone')
    if (done) done.hidden = !layoutEditing
    var reset = $('layoutReset')
    if (reset) reset.hidden = !layoutEditing
    var cards = layoutCards()
    for (var i = 0; i < cards.length; i += 1) ensureHideButton(cards[i])
    ensureDeskHides()
    applyDeskHidden()
    renderAddCards()
    applyTabGroup()
    if (!layoutEditing) persistDomOrder()
  }

  function resetLayout(message) {
    if (layoutApi) layoutApi.clearLayout(layoutStorage())
    layoutState = layoutApi ? layoutApi.defaultLayout() : null
    applyLayout(layoutState, false)
    layoutToast(message || '已恢复默认布局')
  }

  function cardLabel(card) {
    var named = card.getAttribute('data-card-label')
    if (named) return named
    var title = card.querySelector('h1, h2')
    return title ? title.textContent.trim() : card.getAttribute('data-card-id')
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
    if (!layoutEditing) {
      section.hidden = true
      list.textContent = ''
      return
    }
    var hidden = hiddenCards()
    var deskHidden = readDeskHidden()
    var deskHiddenCards = deskCards().filter(function (card, index) {
      return deskHidden.indexOf(deskCardId(card, index)) >= 0
    })
    if (!hidden.length && !deskHiddenCards.length) {
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
    for (var d = 0; d < deskHiddenCards.length; d += 1) {
      var deskChip = document.createElement('button')
      deskChip.type = 'button'
      deskChip.className = 'add-chip'
      deskChip.setAttribute('data-desk-add', deskCardId(deskHiddenCards[d], 0))
      deskChip.textContent = '+ ' + deskLabel(deskHiddenCards[d])
      list.appendChild(deskChip)
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
    layoutToast('已添加「' + (label || id) + '」')
  }

  function cancelEntryPress() {
    if (entryTimer) {
      window.clearTimeout(entryTimer)
      entryTimer = null
    }
    entryStart = null
  }

  function onPagePointerDown(event) {
    if (layoutEditing) return
    if (event.button !== undefined && event.button !== 0) return
    var target = event.target
    if (target && target.closest
      && target.closest('input, textarea, button, select, a, [contenteditable]')) return
    entryStart = { x: event.clientX, y: event.clientY }
    if (entryTimer) window.clearTimeout(entryTimer)
    entryTimer = window.setTimeout(function () {
      entryTimer = null
      if (!entryStart) return
      entryStart = null
      setLayoutEditing(true)
      layoutToast('已进入编辑模式，长按卡片可拖动')
    }, ENTRY_LONG_PRESS_MS)
  }

  function beginDrag(card, event) {
    if (!layoutEditing || !layoutApi || !card) return
    drag = { card: card, pointerId: event.pointerId }
    card.classList.add('dragging')
    try { if (card.setPointerCapture) card.setPointerCapture(event.pointerId) } catch { /* ignore */ }
  }

  function updateDrag(event) {
    if (!drag || event.pointerId !== drag.pointerId) return
    var cards = layoutCards()
    var y = event.clientY
    for (var i = 0; i < cards.length; i += 1) {
      var candidate = cards[i]
      if (candidate === drag.card || candidate.classList.contains('is-hidden')) continue
      var rect = candidate.getBoundingClientRect()
      if (y >= rect.top && y <= rect.bottom) {
        var after = y > rect.top + rect.height / 2
        var reference = after ? candidate.nextSibling : candidate
        if (reference === drag.card) return
        if (!after && candidate.previousSibling === drag.card) return
        var container = $('cards')
        if (container) container.insertBefore(drag.card, reference)
        return
      }
    }
  }

  function endDrag() {
    if (!drag) return
    var card = drag.card
    card.classList.remove('dragging')
    try { if (card.releasePointerCapture) card.releasePointerCapture(drag.pointerId) } catch { /* ignore */ }
    drag = null
    persistDomOrder()
  }

  function onCardsPointerDown(event) {
    if (!layoutEditing) return
    var target = event.target
    var card = target && target.closest ? target.closest('[data-card-id]') : null
    if (!card) return
    if (target.closest('[data-layout-hide]')) return
    var intent = layoutApi && layoutApi.dragIntent
      ? layoutApi.dragIntent({ editing: layoutEditing, pointerType: event.pointerType })
      : { hold: true }
    if (!intent.hold) return
    if (event.cancelable) event.preventDefault()
    pressStart = { x: event.clientX, y: event.clientY, card: card, pointerId: event.pointerId }
    if (pressTimer) window.clearTimeout(pressTimer)
    pressTimer = window.setTimeout(function () {
      pressTimer = null
      if (!pressStart) return
      var pending = pressStart
      pressStart = null
      beginDrag(pending.card, { pointerId: pending.pointerId })
    }, LONG_PRESS_MS)
  }

  function onCardsPointerMove(event) {
    if (entryTimer && entryStart) {
      var edx = event.clientX - entryStart.x
      var edy = event.clientY - entryStart.y
      if (edx * edx + edy * edy > MOVE_CANCEL_PX * MOVE_CANCEL_PX) cancelEntryPress()
    }
    if (pressTimer && pressStart) {
      var dx = event.clientX - pressStart.x
      var dy = event.clientY - pressStart.y
      if (dx * dx + dy * dy > MOVE_CANCEL_PX * MOVE_CANCEL_PX) {
        window.clearTimeout(pressTimer)
        pressTimer = null
        pressStart = null
      }
      return
    }
    if (drag) {
      if (event.cancelable) event.preventDefault()
      updateDrag(event)
    }
  }

  function onCardsPointerUp() {
    cancelEntryPress()
    if (pressTimer) {
      window.clearTimeout(pressTimer)
      pressTimer = null
    }
    pressStart = null
    endDrag()
  }

  function onCardsClick(event) {
    var target = event.target
    var add = target && target.closest ? target.closest('[data-add-card]') : null
    if (add) {
      event.preventDefault()
      showHiddenCard(add.getAttribute('data-add-card'))
      return
    }
    var hide = target && target.closest ? target.closest('[data-layout-hide]') : null
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
    layoutToast('已隐藏「' + cardLabel(card) + '」，可在「添加卡片」恢复')
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
    var cards = $('cards')
    if (cards) {
      cards.addEventListener('pointerdown', onCardsPointerDown)
      cards.addEventListener('click', onCardsClick)
    }
    bindTabBar()
    document.addEventListener('pointerdown', onPagePointerDown)
    document.addEventListener('pointermove', onCardsPointerMove, { passive: false })
    document.addEventListener('pointerup', onCardsPointerUp)
    document.addEventListener('pointercancel', onCardsPointerUp)
    ensureDeskHides()
    applyDeskHidden()
    document.addEventListener('click', onDeskEditClick)
  }

  function boot() {
    railInit()
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
      renderEmpty('request_failed')
      setText('chartHint', '无数据')
      renderTrace({ ok: true, rows: [] })
      return
    }
    loadSwitches()
    loadSeries()
    fetchState()
    fetchTrace()
    function pollTick() {
      if (document.hidden) return
      fetchState()
      fetchTrace()
    }
    window.setInterval(pollTick, POLL_MS)
    window.setInterval(updateStatusClock, 1000)
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) return
      fetchState()
      fetchTrace()
    })
    window.addEventListener('resize', function () { renderChart(lastData) })
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot)
  } else {
    boot()
  }
})();