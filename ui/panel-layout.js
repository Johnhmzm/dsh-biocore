/** BioCore panel layout model (v4, pure logic, no DOM).
 *
 * One source of truth for: card registry (id -> tab/label), tab groups,
 * hide/restore, ordering and localStorage persistence. panel.js only wires DOM
 * events; selftest.mjs exercises this file inside a bare vm sandbox.
 *
 * Classification rule (hard): a card belongs to exactly one tab, and a tab is
 * either all controls (settings) or all data (dashboard/memory/relation) or all
 * diagnostics (tools). Never mixed.
 *
 * The dashboard is three columns of stacked cards plus the 24h strip; the
 * column a card lives in is part of the markup, not of this model, so hiding
 * and reordering stay a flat list of ids.
 */
(function (root) {
  'use strict'

  var STORAGE_KEY = 'biocore-panel-layout-v2'
  var RAIL_KEY = 'biocore-panel-rail-v1'

  var TAB_ORDER = ['dashboard', 'memory', 'relation', 'settings', 'tools']
  var TAB_LABELS = {
    dashboard: '仪表盘',
    memory: '记忆',
    relation: '关系',
    settings: '配置',
    tools: '工具',
  }

  /** Card registry. col is the dashboard stack (1..3) or 0 for full width. */
  var CARD_META = [
    { id: 'intero', tab: 'dashboard', label: '自体感知', col: 1 },
    { id: 'log', tab: 'dashboard', label: '记忆', col: 1 },
    { id: 'state', tab: 'dashboard', label: '当前', col: 2 },
    { id: 'today', tab: 'dashboard', label: '今日', col: 2 },
    { id: 'loops', tab: 'dashboard', label: '待办', col: 2 },
    { id: 'dims', tab: 'dashboard', label: '维度', col: 3 },
    { id: 'cache', tab: 'dashboard', label: '缓存', col: 3 },
    { id: 'system', tab: 'dashboard', label: '系统', col: 3 },
    { id: 'chart', tab: 'dashboard', label: '24 小时', col: 0 },

    { id: 'memory', tab: 'memory', label: '最近记忆', col: 0 },
    { id: 'memory-summary', tab: 'memory', label: '记忆概览', col: 0 },

    { id: 'metrics', tab: 'relation', label: '关系数值', col: 0 },
    { id: 'narrative', tab: 'relation', label: '叙事摘要', col: 0 },

    { id: 'persona', tab: 'settings', label: '人设', col: 0 },
    { id: 'model', tab: 'settings', label: '模型与接口', col: 0 },
    { id: 'switches', tab: 'settings', label: '开关', col: 0 },
    { id: 'expression', tab: 'settings', label: '表达', col: 0 },
    { id: 'advanced', tab: 'settings', label: '维护', col: 0 },

    { id: 'trace', tab: 'tools', label: '这一轮为什么', col: 0 },
    { id: 'runtime', tab: 'tools', label: '运行时', col: 0 },
    { id: 'diagnostics', tab: 'tools', label: '诊断', col: 0 },
  ]

  var DEFAULT_ORDER = CARD_META.map(function (card) { return card.id })

  var META_BY_ID = {}
  for (var i = 0; i < CARD_META.length; i += 1) META_BY_ID[CARD_META[i].id] = CARD_META[i]

  var TAB_GROUPS = {}
  for (var t = 0; t < TAB_ORDER.length; t += 1) TAB_GROUPS[TAB_ORDER[t]] = []
  for (var m = 0; m < CARD_META.length; m += 1) TAB_GROUPS[CARD_META[m].tab].push(CARD_META[m].id)

  function cardMeta(id) {
    return META_BY_ID[id] || null
  }

  function tabOf(id) {
    var meta = META_BY_ID[id]
    return meta ? meta.tab : ''
  }

  function defaultLayout() {
    return { order: DEFAULT_ORDER.slice(), hidden: [] }
  }

  function normalizeOrder(order) {
    var out = []
    var seen = {}
    var input = Array.isArray(order) ? order : []
    for (var i = 0; i < input.length; i += 1) {
      var id = input[i]
      if (typeof id === 'string' && DEFAULT_ORDER.indexOf(id) >= 0 && !seen[id]) {
        seen[id] = 1
        out.push(id)
      }
    }
    for (var j = 0; j < DEFAULT_ORDER.length; j += 1) {
      var known = DEFAULT_ORDER[j]
      if (!seen[known]) {
        seen[known] = 1
        out.push(known)
      }
    }
    return out
  }

  function normalizeHidden(hidden, order) {
    var out = []
    var input = Array.isArray(hidden) ? hidden : []
    for (var i = 0; i < input.length; i += 1) {
      var id = input[i]
      if (typeof id === 'string' && order.indexOf(id) >= 0 && out.indexOf(id) < 0) out.push(id)
    }
    return out
  }

  function normalizeLayout(value) {
    var raw = value && typeof value === 'object' ? value : {}
    var order = normalizeOrder(raw.order)
    var hidden = normalizeHidden(raw.hidden, order)
    var visible = order.filter(function (id) { return hidden.indexOf(id) < 0 })
    if (!visible.length) return defaultLayout()
    return { order: order, hidden: hidden }
  }

  function visibleIds(layout) {
    var normalized = normalizeLayout(layout)
    return normalized.order.filter(function (id) { return normalized.hidden.indexOf(id) < 0 })
  }

  function sortCards(ids, order) {
    var list = Array.isArray(ids) ? ids.slice() : []
    var ranked = normalizeOrder(order)
    var out = []
    var seen = {}
    for (var i = 0; i < ranked.length; i += 1) {
      var id = ranked[i]
      if (list.indexOf(id) >= 0 && !seen[id]) { seen[id] = 1; out.push(id) }
    }
    for (var j = 0; j < list.length; j += 1) {
      var extra = list[j]
      if (!seen[extra]) { seen[extra] = 1; out.push(extra) }
    }
    return out
  }

  function moveCard(layout, id, delta) {
    var normalized = normalizeLayout(layout)
    var index = normalized.order.indexOf(id)
    if (index < 0) return normalized
    var target = index + Number(delta || 0)
    if (!Number.isFinite(target)) return normalized
    target = Math.max(0, Math.min(normalized.order.length - 1, target))
    if (target === index) return normalized
    var order = normalized.order.slice()
    order.splice(index, 1)
    order.splice(target, 0, id)
    return { order: order, hidden: normalized.hidden.slice() }
  }

  function hideCard(layout, id) {
    var normalized = normalizeLayout(layout)
    if (normalized.hidden.indexOf(id) >= 0) return normalized
    var hidden = normalized.hidden.concat([id])
    var visible = normalized.order.filter(function (cardId) { return hidden.indexOf(cardId) < 0 })
    if (!visible.length) {
      var restored = defaultLayout()
      restored.restored = true
      return restored
    }
    return { order: normalized.order.slice(), hidden: hidden }
  }

  function showCard(layout, id) {
    var normalized = normalizeLayout(layout)
    if (normalized.hidden.indexOf(id) < 0) return normalized
    var hidden = normalized.hidden.filter(function (cardId) { return cardId !== id })
    return { order: normalized.order.slice(), hidden: hidden }
  }

  /** Pointer intent: only edit mode drags; touch needs a long press first. */
  function dragIntent(options) {
    var opts = options || {}
    if (!opts.editing) return { start: false, hold: false }
    return { start: false, hold: true }
  }

  /** Cards belonging to one tab, in the given order. */
  function cardsForTab(ids, tab) {
    var list = Array.isArray(ids) ? ids : []
    var group = TAB_GROUPS[tab] || TAB_GROUPS.dashboard
    return list.filter(function (id) { return group.indexOf(id) >= 0 })
  }

  function readLayout(storage) {
    try {
      var raw = storage && typeof storage.getItem === 'function' ? storage.getItem(STORAGE_KEY) : null
      if (!raw) return defaultLayout()
      return normalizeLayout(JSON.parse(raw))
    } catch {
      return defaultLayout()
    }
  }

  function writeLayout(storage, layout) {
    try {
      if (!storage || typeof storage.setItem !== 'function') return false
      storage.setItem(STORAGE_KEY, JSON.stringify(normalizeLayout(layout)))
      return true
    } catch {
      return false
    }
  }

  function clearLayout(storage) {
    try {
      if (!storage || typeof storage.removeItem !== 'function') return false
      storage.removeItem(STORAGE_KEY)
      return true
    } catch {
      return false
    }
  }

  var api = {
    STORAGE_KEY: STORAGE_KEY,
    RAIL_KEY: RAIL_KEY,
    TAB_ORDER: TAB_ORDER.slice(),
    TAB_LABELS: TAB_LABELS,
    TAB_GROUPS: TAB_GROUPS,
    CARD_META: CARD_META.slice(),
    DEFAULT_ORDER: DEFAULT_ORDER.slice(),
    cardMeta: cardMeta,
    tabOf: tabOf,
    cardsForTab: cardsForTab,
    defaultLayout: defaultLayout,
    normalizeOrder: normalizeOrder,
    normalizeLayout: normalizeLayout,
    visibleIds: visibleIds,
    sortCards: sortCards,
    moveCard: moveCard,
    hideCard: hideCard,
    showCard: showCard,
    dragIntent: dragIntent,
    readLayout: readLayout,
    writeLayout: writeLayout,
    clearLayout: clearLayout,
  }
  root.BioCorePanelLayout = api
})(typeof window !== 'undefined' ? window
  : (typeof globalThis !== 'undefined' ? globalThis : this));
