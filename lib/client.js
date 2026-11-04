/** dsh-biocore-ui client half.
 *
 * Right-sidebar tab "生物核": first screen = 5 numbers (v / 绷紧 / 暖 / 沉 / 清)
 * + 3 words (语气 / 关系 / 自主). The slot gives us the current session id; we
 * poll /api/biocore/state?session=<id> every 5s and switch data when the
 * session changes. Failures show 未连接; no dialogs, no external dependencies.
 */
window.__ModuleLoader__.load({
  id: 'dsh-biocore',
  factory: (require) => {
    const React = require('react')
    const h = React.createElement

    const STATE_URL = '/api/biocore/state'
    const POLL_MS = 5000
    const PANEL_ID = 'biocore-ui'
    const ACCENT = 'var(--dsh-color-accent, currentColor)'
    const MUTED = 'rgba(127, 127, 127, 0.72)'
    const LINE = 'rgba(127, 127, 127, 0.22)'
    const SWITCH_URL = '/api/biocore/switch'
    // The only four switches the host accepts (same order as lib/modules/switch.js).
    const SWITCH_META = [
      { key: 'safety_enabled', label: '安全闸', hint: '打开后可能拦截/改写回复' },
      { key: 'boundary_enabled', label: '底线', hint: '越界时先守住边界' },
      { key: 'crisis_safety_enabled', label: '危机兜底', hint: '危机信号优先安全' },
      { key: 'proactive_send', label: '主动回复', hint: '允许私聊主动发消息' },
    ]

    function stateUrl(sessionId) {
      return STATE_URL + '?session=' + encodeURIComponent(String(sessionId || ''))
    }

    function switchMeta(key) {
      return SWITCH_META.find((item) => item.key === key) || null
    }

    function switchLabel(key) {
      const meta = switchMeta(key)
      return meta ? meta.label : String(key || '')
    }

    function confirmationText(key, next) {
      return '确定要' + (next ? '打开' : '关闭') + switchLabel(key) + '吗？'
    }

    function switchErrorText(reason) {
      const code = String(reason || 'unknown')
      const map = {
        unknown_key: '未知开关（只支持四个）',
        confirm_required: '缺少二次确认',
        bad_value: '开关值必须是布尔',
        bad_payload: '请求格式错误',
        settings_unavailable: '设置服务不可用',
        entry_not_found: '找不到 BioCore 设置项',
        conflict: '设置已被修改，请刷新后重试',
        write_failed: '写入宿主设置失败',
        network: '网络错误，请重试',
        method_not_allowed: '不支持的方法',
      }
      return '写入失败：' + (map[code] || ('未知原因（' + code + '）'))
    }

    /** Four native switches at the bottom of the pane; host settings is the authority. */
    function SwitchSection(props) {
      const values = (props && props.switches) || null
      const busy = (props && props.busy) || ''
      const error = (props && props.error) || ''
      const rows = SWITCH_META.map((meta) => h('label', {
        key: meta.key,
        style: {
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '6px 0', borderTop: '1px solid ' + LINE, fontSize: 12,
          cursor: values ? 'pointer' : 'default',
        },
      },
      h('span', { style: { display: 'flex', flexDirection: 'column' } },
        h('span', null, meta.label),
        h('span', { style: { color: MUTED, fontSize: 11 } }, meta.hint)),
      h('input', {
        type: 'checkbox',
        role: 'switch',
        checked: !!(values && values[meta.key] === true),
        disabled: !values || busy === meta.key,
        onChange: (event) => {
          const current = !!(values && values[meta.key] === true)
          const next = !!(event && event.target && event.target.checked)
          if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
            if (!window.confirm(confirmationText(meta.key, next))) {
              // Cancelled: reset the native checkbox to the last known value.
              if (event && event.target) event.target.checked = current
              return
            }
          }
          if (props && typeof props.onToggle === 'function') props.onToggle(meta.key, next)
        },
        style: { marginLeft: 10 },
      })))
      return h('div', { style: { marginTop: 10, paddingTop: 8, borderTop: '1px solid ' + LINE } },
        h('div', { style: { fontSize: 11, color: MUTED, marginBottom: 2 } }, '开关（写宿主设置）'),
        h('div', null, rows),
        error ? h('div', { role: 'status', style: { color: '#d9534f', fontSize: 11, paddingTop: 4 } }, error) : null,
        values ? null : h('div', { style: { color: MUTED, fontSize: 11, paddingTop: 4 } }, '开关状态加载中…'))
    }

    function useSwitches() {
      const [state, setState] = React.useState({ switches: null, busy: '', error: '' })
      const load = () => {
        if (typeof fetch !== 'function') return
        fetch(SWITCH_URL, { headers: { accept: 'application/json' } })
          .then((response) => response.json())
          .then((data) => {
            if (data && data.ok && data.switches) {
              setState((prev) => ({ ...prev, switches: data.switches }))
            }
          })
          .catch(() => { /* keep last known values */ })
      }
      React.useEffect(() => { load() }, [])
      const toggle = (key, next) => {
        const previous = state.switches ? { ...state.switches } : null
        // Optimistic flip; rollback on any failure below.
        setState((prev) => ({
          ...prev,
          busy: key,
          error: '',
          switches: prev.switches ? { ...prev.switches, [key]: next } : prev.switches,
        }))
        if (typeof fetch !== 'function') {
          setState((prev) => ({ ...prev, busy: '', error: switchErrorText('network') }))
          return
        }
        fetch(SWITCH_URL, {
          method: 'POST',
          headers: { 'content-type': 'application/json', accept: 'application/json' },
          body: JSON.stringify({ key, value: next, confirm: true }),
        })
          .then((response) => response.json())
          .then((data) => {
            if (data && data.ok) {
              setState((prev) => ({
                ...prev,
                busy: '',
                error: '',
                switches: data.switches || prev.switches,
              }))
              load() // refresh authoritative values
            } else {
              setState((prev) => ({
                ...prev,
                busy: '',
                error: switchErrorText(data && data.reason),
                switches: previous,
              }))
            }
          })
          .catch(() => {
            setState((prev) => ({
              ...prev,
              busy: '',
              error: switchErrorText('network'),
              switches: previous,
            }))
          })
      }
      return { switches: state.switches, busy: state.busy, error: state.error, toggle, reload: load }
    }

    function useBiocoreState(sessionId) {
      const [view, setView] = React.useState({ data: null, error: false })
      React.useEffect(() => {
        if (!sessionId) {
          setView({ data: null, error: false })
          return undefined
        }
        let alive = true
        setView({ data: null, error: false })
        const tick = () => {
          fetch(stateUrl(sessionId), { headers: { accept: 'application/json' } })
            .then((response) => response.json())
            .then((data) => { if (alive) setView({ data, error: false }) })
            .catch(() => { if (alive) setView({ data: null, error: true }) })
        }
        tick()
        const timer = setInterval(tick, POLL_MS)
        return () => { alive = false; clearInterval(timer) }
      }, [sessionId])
      return view
    }

    function text(value) {
      return value === undefined || value === null || value === '' ? '-' : String(value)
    }

    function bar(value, low, high) {
      const span = high - low || 1
      const ratio = Math.max(0, Math.min(1, (Number(value) - low) / span))
      return h('div', {
        style: {
          height: 4, borderRadius: 2, marginTop: 4,
          background: 'rgba(127, 127, 127, 0.18)',
        },
      }, h('div', {
        style: {
          height: '100%', width: Math.round(ratio * 100) + '%',
          borderRadius: 2, background: 'currentColor', opacity: 0.45,
        },
      }))
    }

    function metric(label, value, low, high, digits, delta) {
      const shown = Number.isFinite(Number(value))
        ? Number(value).toFixed(digits === undefined ? 2 : digits)
        : '-'
      return h('div', { style: { minWidth: 0 } },
        h('div', {
          style: {
            display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
            gap: 4, fontSize: 11, color: MUTED,
          },
        },
        h('span', null, label),
        h('span', { style: { fontVariantNumeric: 'tabular-nums' } },
          shown + ' ' + (delta || '—'))),
        bar(value, low, high))
    }

    function word(label, value) {
      return h('div', {
        style: {
          display: 'flex', justifyContent: 'space-between',
          padding: '6px 0', borderTop: '1px solid ' + LINE, fontSize: 12,
        },
      }, h('span', { style: { color: MUTED } }, label), h('span', null, text(value)))
    }

    function relativeTime(ms) {
      const value = Number(ms)
      if (!Number.isFinite(value) || value <= 0) return ''
      const seconds = Math.max(0, Math.round((Date.now() - value) / 1000))
      if (seconds < 60) return seconds + ' 秒前'
      if (seconds < 3600) return Math.round(seconds / 60) + ' 分钟前'
      return Math.round(seconds / 3600) + ' 小时前'
    }

    function shortSession(sessionId) {
      const value = String(sessionId || '')
      if (!value) return ''
      return value.length > 10 ? value.slice(0, 8) : value
    }

    const SPARK_POINTS = 60
    const SPARK_HEIGHT = 24

    /** '+0.04' / '-0.02' / '±0.00' / '—' (no previous turn). */
    function formatDelta(previous, current) {
      const before = Number(previous)
      const after = Number(current)
      if (!Number.isFinite(before) || !Number.isFinite(after)) return '—'
      const delta = after - before
      if (Math.abs(delta) < 0.005) return '±0.00'
      return (delta > 0 ? '+' : '') + delta.toFixed(2)
    }

    /** Per-turn delta from the state history (last two turns). */
    function historyDelta(data, field) {
      if (!data || data.available === false || !Array.isArray(data.history)) return '—'
      const history = data.history
      if (history.length < 2) return '—'
      return formatDelta(history[history.length - 2][field], history[history.length - 1][field])
    }

    function sparkValues(data) {
      if (!data || data.available === false || !Array.isArray(data.history)) return []
      const values = []
      for (const item of data.history) {
        const value = Number(item && item.v)
        if (Number.isFinite(value)) values.push(value)
      }
      return values.slice(-SPARK_POINTS)
    }

    /** Tiny 24px sparkline for the tab (detailed curve stays on the panel page). */
    function buildSparkline(data) {
      const values = sparkValues(data)
      if (values.length < 2) return null
      const width = 100
      const height = SPARK_HEIGHT
      const pad = 2
      const span = width - pad * 2
      const points = values.map((value, index) => {
        const x = pad + (span * index) / (values.length - 1)
        const y = pad + ((1 - Math.max(-1, Math.min(1, value))) / 2) * (height - pad * 2)
        return x.toFixed(2) + ',' + y.toFixed(2)
      }).join(' ')
      return h('svg', {
        className: 'biocore-sparkline',
        viewBox: '0 0 ' + width + ' ' + height,
        preserveAspectRatio: 'none',
        height: SPARK_HEIGHT,
        style: { display: 'block', width: '100%', height: SPARK_HEIGHT + 'px', color: ACCENT },
      }, h('polyline', {
        points,
        fill: 'none',
        stroke: 'currentColor',
        strokeWidth: 1.6,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        vectorEffect: 'non-scaling-stroke',
      }))
    }

    function statusOf(data, error, sessionId) {
      if (!sessionId) {
        return { ok: false, label: '未连接', suggestion: '从聊天会话打开生物核面板', reason: 'no_session' }
      }
      if (error || !data) {
        return { ok: false, label: '连不上宿主', suggestion: '确认 3081 上的 DSH 与插件已加载，再试一次', reason: 'request_failed' }
      }
      if (data.available === false) {
        const reason = String(data.reason || '')
        if (reason === 'bad_state') {
          return { ok: false, label: '状态文件坏了', suggestion: '删掉该会话的 state 文件后重试，或发一条新消息重建', reason }
        }
        if (reason === 'no_state_yet') {
          return { ok: false, label: '这个会话还没聊过', suggestion: '发一条消息后就有状态', reason }
        }
        return { ok: false, label: '连不上宿主', suggestion: '确认 3081 上的 DSH 与插件已加载，再试一次', reason: reason || 'unavailable' }
      }
      return { ok: true, label: '已连接', suggestion: '', reason: '' }
    }

    /** Pure render: used by the panel and by test/selftest.mjs. */
    function renderPanel(data, error, sessionId) {
      const status = statusOf(data, error, sessionId)
      const available = status.ok
      const tone = available && data.tone ? data.tone.label : ''
      const relation = available && data.relation ? data.relation.stage : ''
      const autonomy = available && data.autonomy ? data.autonomy.state : ''
      const intero = available && data.intero ? data.intero : {}
      const updated = available ? relativeTime(data.updated_at) : ''
      const session = shortSession(sessionId)
      const vDelta = available ? historyDelta(data, 'v') : '—'
      const vArrow = vDelta === '—' || vDelta === '±0.00' ? '→' : (vDelta.charAt(0) === '+' ? '↑' : '↓')
      const arrowColor = vArrow === '↑' ? '#2fae62' : (vArrow === '↓' ? '#e04843' : MUTED)
      const sparkline = available ? buildSparkline(data) : null
      return h('div', {
        style: {
          padding: '12px 14px', fontSize: 12, color: 'inherit',
          fontFamily: 'inherit', lineHeight: 1.5,
        },
      },
      h('div', {
        style: {
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          paddingBottom: 8, borderBottom: '1px solid ' + LINE,
        },
      },
      h('span', { style: { fontSize: 13, fontWeight: 600 } }, '生物核'),
      h('span', { style: { color: MUTED, fontSize: 11 } },
        h('span', {
          style: {
            display: 'inline-block', width: 6, height: 6, borderRadius: 3,
            marginRight: 5, verticalAlign: 'middle',
            background: available ? ACCENT : 'rgba(127, 127, 127, 0.5)',
          },
        }),
        status.label + (updated ? ' · ' + updated : ''))),

      h('div', { style: { paddingTop: 10 } },
        h('div', { style: { fontSize: 11, color: MUTED, marginBottom: 2 } }, '心情'),
        h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 6 } },
          h('span', { style: { fontSize: 28, fontWeight: 600, fontVariantNumeric: 'tabular-nums' } },
            available ? Number(data.v).toFixed(2) : '-'),
          h('span', { style: { fontSize: 13, color: arrowColor } }, vArrow),
          h('span', { style: { fontSize: 11, color: MUTED, fontVariantNumeric: 'tabular-nums' } }, vDelta)),
        bar(available ? data.v : 0, -1, 1)),

      sparkline ? h('div', { style: { paddingTop: 8 } }, sparkline) : null,

      h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 14px', paddingTop: 12 } },
        metric('绷紧', available ? intero.tension : null, 0, 1, 2,
          available ? historyDelta(data, 'tension') : '—'),
        metric('暖', available ? intero.warmth : null, 0, 1, 2,
          available ? historyDelta(data, 'warmth') : '—'),
        metric('沉', available ? intero.heaviness : null, 0, 1, 2,
          available ? historyDelta(data, 'heaviness') : '—'),
        metric('清', available ? intero.clarity : null, 0, 1, 2,
          available ? historyDelta(data, 'clarity') : '—')),


      h('div', { style: { paddingTop: 10 } },
        word('语气', tone || (available ? '正常' : '')),
        word('关系', relation),
        word('自主', autonomy)),

      status.ok ? null : h('div', {
        style: { marginTop: 8, color: MUTED, fontSize: 11 },
      }, status.suggestion),

      h('div', {
        style: {
          marginTop: 10, paddingTop: 8, borderTop: '1px solid ' + LINE,
          display: 'flex', justifyContent: 'space-between', color: MUTED, fontSize: 11,
        },
      },
      h('span', null, '记忆 ' + (available ? text(data.memory_count) : '-') + ' 条'),
      h('span', null,
        (session ? '会话 ' + session + ' · ' : '') + '只读 · 5 秒')))
    }

    const PANEL_PAGE_URL = '/api/biocore-ui/ui/panel.html'
    const MAIN_PANEL_KEY = 'biocore-panel'

    function panelPageUrl(sessionId) {
      return PANEL_PAGE_URL + '?session=' + encodeURIComponent(String(sessionId || ''))
    }

    /** Main-slot iframe URL: omit the query entirely when no Session is known. */
    function panelIframeUrl(sessionId) {
      const id = String(sessionId || '').trim()
      return id ? PANEL_PAGE_URL + '?session=' + encodeURIComponent(id) : PANEL_PAGE_URL
    }

    /** Pick the current Session, then the most recent one; '' when none. */
    function pickSessionId(state) {
      const byId = state && state.byId && typeof state.byId === 'object' ? state.byId : {}
      const entries = Object.values(byId).filter((item) => item && typeof item === 'object')
      const active = entries.find((item) => item.retainedBy
        && Number(item.retainedBy.mainView) > 0 && item.id)
      if (active) return String(active.id)
      let newest = ''
      let stamp = -1
      for (const item of entries) {
        if (!item || !item.id) continue
        const at = Number(item.updatedAt || item.lastActivityAt || item.createdAt || 0)
        if (at > stamp) {
          stamp = at
          newest = String(item.id)
        }
      }
      return newest
    }

    let selectMainPanel = null

    /** Used by the tab's expand button: in-app main panel, not a popup. */
    function setMainPanelSelector(fn) {
      selectMainPanel = typeof fn === 'function' ? fn : null
    }

    function openInstrumentPanel(sessionId) {
      const url = panelPageUrl(sessionId)
      if (selectMainPanel) {
        try {
          if (selectMainPanel(MAIN_PANEL_KEY)) return url
        } catch { /* fall through to window.open */ }
      }
      try {
        const opened = window.open(url, '_blank', 'noopener')
        if (!opened && window.location) window.location.assign(url)
      } catch { /* popup blocked: the right-sidebar tab still works */ }
      return url
    }

    function mainPanelIframe(sessionId) {
      return h('iframe', {
        src: panelIframeUrl(sessionId),
        title: 'BioCore 仪器面板',
        style: {
          width: '100%', height: '100%', border: 0,
          display: 'block', background: 'transparent',
        },
      })
    }

    /** Main keyed slot occupant: an iframe of the standalone panel. */
    function BiocoreMainPanel(props) {
      const useSessions = props && props.useSessions
      const sessionId = typeof useSessions === 'function'
        ? useSessions((state) => pickSessionId(state))
        : ''
      return h('div', {
        style: { width: '100%', height: '100%', minHeight: 0, overflow: 'hidden' },
      }, mainPanelIframe(sessionId))
    }

    function ExpandButton(props) {
      const sessionId = props && props.sessionId
      return h('button', {
        type: 'button',
        onClick: () => openInstrumentPanel(sessionId),
        style: {
          display: 'block', width: 'calc(100% - 28px)', margin: '10px 14px 14px',
          padding: '7px 10px', fontSize: 12, fontFamily: 'inherit',
          color: 'inherit', background: 'transparent', cursor: 'pointer',
          border: '1px solid ' + LINE, borderRadius: 6,
        },
      }, '展开独立面板')
    }

    function Panel(props) {
      const sessionId = props && props.sessionId
      const view = useBiocoreState(sessionId)
      const switches = useSwitches()
      return h('div', null,
        renderPanel(view.data, view.error, sessionId),
        h(SwitchSection, {
          switches: switches.switches,
          busy: switches.busy,
          error: switches.error,
          onToggle: switches.toggle,
        }),
        h(ExpandButton, { sessionId }))
    }

    function Title() {
      return h('span', { style: { fontSize: 12, fontWeight: 600 } }, '生物核')
    }

    function Icon(props) {
      const size = (props && props.size) || 16
      return h('svg', {
        width: size, height: size, viewBox: '0 0 16 16', 'aria-hidden': true,
        style: { display: 'block' },
      },
      h('circle', { cx: 8, cy: 8, r: 6.2, fill: 'none', stroke: 'currentColor', strokeWidth: 1.4 }),
      h('circle', { cx: 8, cy: 8, r: 2.2, fill: 'currentColor', opacity: 0.7 }))
    }

    function effect(ctx, run) {
      if (ctx && typeof ctx.effect === 'function') return ctx.effect(run)
      return run()
    }

    function apply(ctx) {
      effect(ctx, () => ctx.sidebarRightTabs.register({
        id: PANEL_ID,
        kind: 'biocore',
        multiple: false,
        priority: 'builtin',
        title: () => '生物核',
        guide: [{
          id: 'open',
          order: 30,
          title: () => '生物核',
          description: () => '当前会话的生物核状态（只读）',
          icon: Icon,
        }],
      }))
      effect(ctx, () => ctx.slots.inject('sidebar.right.pane.tab', () => ctx.slots.register({
        name: 'sidebar.right.pane.tab',
        key: PANEL_ID,
        inject: (sessionId) => ({ sessionId }),
      }, Panel)))
      effect(ctx, () => ctx.slots.inject('sidebar.right.pane.tab.title', () => ctx.slots.register({
        name: 'sidebar.right.pane.tab.title',
        key: PANEL_ID,
      }, Title)))

      // task-213: second entry — left sidebar row + root main keyed slot.
      // Slot registration is client-side in DSH (same pattern as
      // dsh-biocore-memory); the host half only serves the static iframe page.
      if (ctx && ctx.layout && typeof ctx.layout.selectPanel === 'function') {
        setMainPanelSelector((key) => {
          ctx.layout.selectPanel(key)
          return true
        })
      }
      effect(ctx, () => ctx.slots.inject('main', () => ctx.slots.register({
        name: 'main',
        key: MAIN_PANEL_KEY,
        inject: () => ({}),
      }, BiocoreMainPanel)))
      effect(ctx, () => ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
        name: 'sidebar.panellist',
        id: MAIN_PANEL_KEY,
        order: 20,
        label: () => 'BioCore',
      }, Icon)))
    }

    const module = { exports: {} }
    module.exports.apply = apply
    module.exports.inject = ['slots', 'sidebarRightTabs', 'layout']
    module.exports.PANEL_ID = PANEL_ID
    module.exports.POLL_MS = POLL_MS
    module.exports.__test = {
      renderPanel, statusOf, Panel, Title, Icon, stateUrl,
      SWITCH_URL, SWITCH_META, switchLabel, confirmationText,
      switchErrorText, SwitchSection, panelPageUrl, openInstrumentPanel,
      ExpandButton, PANEL_PAGE_URL,
      panelIframeUrl, pickSessionId, mainPanelIframe, BiocoreMainPanel,
      MAIN_PANEL_KEY, setMainPanelSelector,
      formatDelta, historyDelta, buildSparkline, sparkValues,
      SPARK_POINTS, SPARK_HEIGHT,
    }
    return module.exports
  },
})
