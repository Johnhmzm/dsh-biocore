/** Pure layout logic for the standalone panel (task-214).
 *
 * No DOM here so test/selftest.mjs can exercise order/hidden/restore/storage
 * without a browser. panel.js owns the DOM wiring; every card carries
 * data-card-id and the layout is persisted in localStorage.
 */
(function (root) {
  'use strict'

  var STORAGE_KEY = 'biocore-panel-layout-v2'
  var DEFAULT_ORDER = [
    'mood', 'tension', 'warmth', 'heaviness', 'clarity', 'chart',
    'memory', 'memory-summary', 'metrics',
    'persona', 'model', 'switches', 'expression', 'advanced',
    'runtime', 'trace',
  ]
  var TAB_ORDER = ['dashboard', 'memory', 'relation', 'settings', 'tools']
  var TAB_GROUPS = Object.freeze({
    dashboard: ['mood', 'tension', 'warmth', 'heaviness', 'clarity', 'chart'],
    memory: ['memory', 'memory-summary'],
    relation: ['metrics'],
    settings: ['persona', 'model', 'switches', 'expression', 'advanced'],
    tools: ['runtime', 'trace'],
  })

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
      if (list.indexOf(id) >= 0 && !seen[id]) {
        seen[id] = 1
        out.push(id)
      }
    }
    for (var j = 0; j < list.length; j += 1) {
      var extra = list[j]
      if (!seen[extra]) {
        seen[extra] = 1
        out.push(extra)
      }
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

  /** Pure pointer-intent: only edit mode drags, always via a long press
   *  (the card itself is the handle — no separate ⠿ control). */
  function dragIntent(options) {
    var opts = options || {}
    if (!opts.editing) return { start: false, hold: false }
    return { start: false, hold: true }
  }

  /** Re-show one hidden card (undo of hideCard). */
  function showCard(layout, id) {
    var normalized = normalizeLayout(layout)
    if (normalized.hidden.indexOf(id) < 0) return normalized
    var hidden = normalized.hidden.filter(function (cardId) { return cardId !== id })
    return { order: normalized.order.slice(), hidden: hidden }
  }

  /** Cards shown for one bottom-nav group (no data refetch, DOM filter only). */
  function cardsForTab(ids, tab) {
    var list = Array.isArray(ids) ? ids : []
    var group = TAB_GROUPS[tab] || TAB_GROUPS.dashboard
    return list.filter(function (id) { return group.indexOf(id) >= 0 })
  }

  function readLayout(storage) {
    try {
      var raw = storage && typeof storage.getItem === 'function'
        ? storage.getItem(STORAGE_KEY) : null
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
    DEFAULT_ORDER: DEFAULT_ORDER.slice(),
    TAB_ORDER: TAB_ORDER.slice(),
    TAB_GROUPS: TAB_GROUPS,
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