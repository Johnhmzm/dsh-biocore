/** Pure formatters for the "why this turn" trace fold (task-210).
 *
 * Shared by the standalone panel (classic <script>) and the Node selftest.
 * Input row shape (python trace):
 *   {ts, session_id, state_delta:[{field,before,after,source}],
 *    intent:{kind,weight,reason}, constraints:{...},
 *    length:{scale,source}, gate:{action,rule,label},
 *    memory:{recalled_ids:[],captured_id:''}, modules:{ran,skipped}}
 */
(function (root, factory) {
  var api = factory()
  if (typeof module === 'object' && module.exports) module.exports = api
  if (root) root.BioTraceView = api
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict'

  var EMPTY_TEXT = '还没有记录'
  var GATE_MISS = '未命中'
  var DASH = '—'
  var MAX_CHANGES = 4

  function num(value) {
    var parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }

  function fmt(value) {
    var parsed = num(value)
    if (parsed === null) return value == null ? '' : String(value)
    if (Math.abs(parsed) < 0.005) return '0.00'
    return parsed.toFixed(2)
  }

  function text(value, limit) {
    var out = String(value == null ? '' : value).replace(/\s+/g, ' ').trim()
    return out.slice(0, limit || 160)
  }

  function asList(value) {
    return Array.isArray(value) ? value : []
  }

  function timeLabel(ts, now) {
    var value = num(ts)
    if (value === null) return '--:--'
    var ms = value > 1000000000000 ? value : value * 1000
    var date = new Date(ms)
    if (Number.isNaN(date.getTime())) return '--:--'
    var hh = String(date.getHours()).padStart(2, '0')
    var mm = String(date.getMinutes()).padStart(2, '0')
    return hh + ':' + mm
  }

  function formatChanges(row) {
    var rows = asList(row && row.state_delta)
      .filter(function (item) { return item && typeof item === 'object' && item.field })
    if (!rows.length) return '无变化'
    var parts = []
    for (var i = 0; i < rows.length && i < MAX_CHANGES; i += 1) {
      parts.push(text(rows[i].field, 18) + ' ' + fmt(rows[i].before) + '→' + fmt(rows[i].after))
    }
    if (rows.length > MAX_CHANGES) parts.push('+' + (rows.length - MAX_CHANGES))
    return parts.join(' · ')
  }

  function formatIntent(row) {
    var intent = row && row.intent
    if (typeof intent === 'string' && intent) return text(intent, 32)
    if (!intent || typeof intent !== 'object') return DASH
    var parts = []
    if (intent.kind) parts.push(text(intent.kind, 24))
    if (num(intent.weight) !== null) parts.push('权重 ' + fmt(intent.weight))
    if (intent.reason) parts.push(text(intent.reason, 40))
    return parts.length ? parts.join(' · ') : DASH
  }

  function formatLength(row) {
    var length = row && row.length
    if (!length || typeof length !== 'object') return DASH
    var source = length.source
    if (Array.isArray(source)) source = source.map(function (item) { return text(item, 20) }).filter(Boolean).join(',')
    var parts = []
    if (num(length.scale) !== null) parts.push('长度 ' + fmt(length.scale))
    if (source) parts.push(text(source, 36))
    return parts.length ? parts.join(' · ') : DASH
  }

  function formatGate(row) {
    var gate = row && row.gate
    if (!gate || typeof gate !== 'object') return DASH
    var action = text(gate.action || '', 18)
    var rule = text(gate.rule || '', 32)
    var label = text(gate.label || '', 24)
    if (!action && !rule) return DASH
    if (action === 'pass') return GATE_MISS
    var parts = []
    if (action) parts.push(action)
    if (rule) parts.push(rule)
    if (label) parts.push(label)
    return parts.join(' · ')
  }

  function formatMemory(row) {
    var memory = row && row.memory
    if (!memory || typeof memory !== 'object') return DASH
    var recalled = asList(memory.recalled_ids)
    var captured = text(memory.captured_id || '', 24)
    var parts = ['召回 ' + recalled.length]
    parts.push(captured ? '写入 ' + captured.slice(0, 12) : '未写入')
    return parts.join(' · ')
  }

  function formatRow(row) {
    var source = row && typeof row === 'object' ? row : {}
    var missing = []
    if (!asList(source.state_delta).length) missing.push('state_delta')
    if (!source.intent) missing.push('intent')
    if (!source.length) missing.push('length')
    if (!source.gate) missing.push('gate')
    if (!source.memory) missing.push('memory')
    return {
      ts: source.ts,
      time: timeLabel(source.ts),
      changes: formatChanges(source),
      intent: formatIntent(source),
      length: formatLength(source),
      gate: formatGate(source),
      memory: formatMemory(source),
      missing: missing,
    }
  }

  function formatPayload(payload, limit) {
    var max = Math.max(1, Math.min(3, num(limit) === null ? 3 : num(limit)))
    if (!payload || payload.ok === false) {
      return { rows: [], empty: true, error: true, hint: '读取失败' }
    }
    var rows = asList(payload.rows).filter(function (row) {
      return row && typeof row === 'object' && !Array.isArray(row)
    }).slice(0, max)
    if (!rows.length) return { rows: [], empty: true, error: false, hint: EMPTY_TEXT }
    return { rows: rows.map(formatRow), empty: false, error: false, hint: '最近 ' + rows.length + ' 轮' }
  }

  return {
    EMPTY_TEXT: EMPTY_TEXT,
    DASH: DASH,
    formatChanges: formatChanges,
    formatIntent: formatIntent,
    formatLength: formatLength,
    formatGate: formatGate,
    formatMemory: formatMemory,
    formatRow: formatRow,
    formatPayload: formatPayload,
    timeLabel: timeLabel,
  }
})
