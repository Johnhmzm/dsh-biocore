/** dsh-biocore-ui client half.
 *
 * Right-sidebar tab "生物核": first screen = 5 numbers (v / 绷紧 / 暖 / 沉 / 清)
 * + 3 words (语气 / 关系 / 自主). Polls /api/biocore/state every 5s and shows
 * 未连接 on failure; no dialogs, no external dependencies.
 */
window.__ModuleLoader__.load({
  id: 'dsh-biocore-ui',
  factory: (require) => {
    const React = require('react')
    const h = React.createElement

    const STATE_URL = '/api/biocore/state'
    const POLL_MS = 5000
    const PANEL_ID = 'biocore-ui'
    const ACCENT = 'var(--dsh-color-accent, currentColor)'
    const MUTED = 'rgba(127, 127, 127, 0.72)'
    const LINE = 'rgba(127, 127, 127, 0.22)'

    function useBiocoreState() {
      const [view, setView] = React.useState({ data: null, error: false })
      React.useEffect(() => {
        let alive = true
        const tick = () => {
          fetch(STATE_URL, { headers: { accept: 'application/json' } })
            .then((response) => response.json())
            .then((data) => { if (alive) setView({ data, error: false }) })
            .catch(() => { if (alive) setView({ data: null, error: true }) })
        }
        tick()
        const timer = setInterval(tick, POLL_MS)
        return () => { alive = false; clearInterval(timer) }
      }, [])
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

    function metric(label, value, low, high, digits) {
      const shown = Number.isFinite(Number(value))
        ? Number(value).toFixed(digits === undefined ? 2 : digits)
        : '-'
      return h('div', { style: { minWidth: 0 } },
        h('div', { style: { display: 'flex', justifyContent: 'space-between', fontSize: 11, color: MUTED } },
          h('span', null, label), h('span', { style: { fontVariantNumeric: 'tabular-nums' } }, shown)),
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

    /** Pure render: used by the panel and by test/selftest.mjs. */
    function renderPanel(data, error) {
      const connected = !!data && !error
      const available = connected && data.available !== false
      const status = error || !data ? '未连接'
        : available ? '已连接' : '暂无状态'
      const tone = available && data.tone ? data.tone.label : ''
      const relation = available && data.relation ? data.relation.stage : ''
      const autonomy = available && data.autonomy ? data.autonomy.state : ''
      const intero = available && data.intero ? data.intero : {}
      const updated = available ? relativeTime(data.updated_at) : ''
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
        status + (updated ? ' · ' + updated : ''))),

      h('div', { style: { paddingTop: 10 } },
        h('div', { style: { ...{ fontSize: 11, color: MUTED }, marginBottom: 4 } }, '心情'),
        h('div', { style: { fontSize: 22, fontWeight: 600, fontVariantNumeric: 'tabular-nums' } },
          available ? Number(data.v).toFixed(2) : '-'),
        bar(available ? data.v : 0, -1, 1)),

      h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 14px', paddingTop: 12 } },
        metric('绷紧', available ? intero.tension : null, 0, 1, 2),
        metric('暖', available ? intero.warmth : null, 0, 1, 2),
        metric('沉', available ? intero.heaviness : null, 0, 1, 2),
        metric('清', available ? intero.clarity : null, 0, 1, 2)),

      h('div', { style: { paddingTop: 10 } },
        word('语气', tone || (available ? '正常' : '')),
        word('关系', relation),
        word('自主', autonomy)),

      h('div', {
        style: {
          marginTop: 10, paddingTop: 8, borderTop: '1px solid ' + LINE,
          display: 'flex', justifyContent: 'space-between', color: MUTED, fontSize: 11,
        },
      },
      h('span', null, '记忆 ' + (available ? text(data.memory_count) : '-') + ' 条'),
      h('span', null, '只读 · 每 5 秒刷新')))
    }

    function Panel() {
      const view = useBiocoreState()
      return renderPanel(view.data, view.error)
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
        inject: () => ({}),
      }, Panel)))
      effect(ctx, () => ctx.slots.inject('sidebar.right.pane.tab.title', () => ctx.slots.register({
        name: 'sidebar.right.pane.tab.title',
        key: PANEL_ID,
      }, Title)))
    }

    const module = { exports: {} }
    module.exports.apply = apply
    module.exports.inject = ['slots', 'sidebarRightTabs']
    module.exports.PANEL_ID = PANEL_ID
    module.exports.POLL_MS = POLL_MS
    module.exports.__test = { renderPanel, Panel, Title, Icon }
    return module.exports
  },
})
