/** 给模型的记忆检索工具 memory_search（工具通道，不走面板）。
 *
 * 背景：模型宁可用关键词去翻会话日志，也不看每轮注入的记忆。这个工具把"回忆过去"
 * 收到一条明确的动作上：召回排序复用注入那条路（engine.recall + 压缩向量直配），
 * 返回带日期/角色/原文的结构化结果，按时间新在前。0 次 chat 调用（最多一次 embedding）。
 *
 * 描述里写死三件事（需求原文）：
 *   a) 用途：回忆过去发生过什么、用户以前说过什么、之前定过什么结论；
 *   b) 优先用它：需要回忆时先调它，不要拿关键词/字符去翻会话日志或历史；
 *   c) 返回原文片段与压缩目录，按日期排序。
 */
import { memoryList, memoryRead, memorySearch } from '../py-bridge.js'

/** 宿主工具构造器：模块加载时取一次；宿主没有这个包时退回 null（单测自己注入）。 */
let HOST_DEFINE_TOOL = null
try {
  const hostTools = await import('@deepseek-ai/dsh-tools')
  HOST_DEFINE_TOOL = typeof hostTools.defineTool === 'function' ? hostTools.defineTool : null
} catch { HOST_DEFINE_TOOL = null }

export const TOOL_NAME = 'memory_search'
export const LIST_TOOL_NAME = 'memory_list'
export const READ_TOOL_NAME = 'memory_read'
export const LIST_LIMIT_DEFAULT = 50
export const LIST_LIMIT_MAX = 100
export const READ_TEXT_LIMIT = 2000
export const SENTENCE_LIMIT = 200
export const EVENT_TEXT_LIMIT = 4000
export const TOP_K_DEFAULT = 8
export const TOP_K_MIN = 1
export const TOP_K_MAX = 20
export const TEXT_LIMIT = 400
export const SCOPES = Object.freeze(['all', 'text', 'compaction'])

export const TOOL_DESCRIPTION = [
  '检索长期记忆：回忆过去发生过什么、用户以前说过什么、之前定过什么结论，都用它。',
  '命中给到句子与它所属事件的一行摘要（同一事件的多句命中会合并成一行）。',
  '要上下文就用返回的 event_id 调 memory_read 读整件；只要单条原文就用 entry_id。',
  '需要回忆时请**先调用本工具**——不要用关键词或字符匹配去翻会话日志、历史记录或文件来"找那句话"。',
].join('\n')

export const PARAMETERS = Object.freeze({
  query: {
    type: 'string',
    required: true,
    description: '要回忆的事情，用自然语言描述；不要只写一两个关键词。',
  },
  top_k: {
    type: 'integer',
    description: '返回几条，1–20，缺省 ' + TOP_K_DEFAULT + '。',
  },
  scope: {
    type: 'string',
    enum: [...SCOPES],
    description: 'all = 原文与压缩目录都查（缺省）；text = 只查原文；compaction = 只查压缩目录。',
  },
})

export const OUTPUT_SCHEMA = Object.freeze({
  type: 'object',
  additionalProperties: false,
  properties: {
    count: { type: 'integer', required: true },
    reason: { type: 'string' },
    results: {
      type: 'array',
      required: true,
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          date: { type: 'string', required: true },
          role: { type: 'string', required: true },
          sentence: { type: 'string', required: true },
          event_summary: { type: 'string', required: true },
          entry_id: { type: 'string', required: true },
          event_id: { type: 'string' },
          hits_in_event: { type: 'integer', required: true },
          match: { type: 'string', required: true },
          rerank: { type: 'boolean', required: true },
          rerank_model: { type: 'string' },
        },
      },
    },
  },
})

/** top_k 归一：非数字/越界都夹到 1–20（schema 说不清边界，这里兜）。 */
export function clampTopK(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return TOP_K_DEFAULT
  return Math.max(TOP_K_MIN, Math.min(TOP_K_MAX, Math.round(parsed)))
}

/** scope 归一：不认识就 all。 */
export function normalizeScope(value) {
  const text = String(value || '').trim().toLowerCase()
  return SCOPES.includes(text) ? text : 'all'
}

/** 一条结果 -> 工具输出项：文本截到 400 字，字段只留四个。 */
export function shapeResult(row) {
  const item = row && typeof row === 'object' ? row : {}
  return {
    date: String(item.date || ''),
    role: String(item.role || ''),
    text: String(item.text || '').slice(0, TEXT_LIMIT),
    kind: item.kind === 'compaction' ? 'compaction' : 'entry',
  }
}

/**
 * 造工具定义（注入 defineTool，单测不需要装宿主包）。
 * @returns defineTool(spec) 的返回值
 */
export function buildMemoryToolSpec(options) {
  const opts = options || {}
  const defineTool = opts.defineTool
  const search = typeof opts.search === 'function' ? opts.search : memorySearch
  const getStore = typeof opts.getStore === 'function' ? opts.getStore : () => ''
  if (typeof defineTool !== 'function') throw new Error('buildMemoryToolSpec: missing defineTool')
  return defineTool({
    name: TOOL_NAME,
    description: TOOL_DESCRIPTION,
    parameters: PARAMETERS,
    output: {
      schema: OUTPUT_SCHEMA,
      render: (_args, value) => {
        const rows = (value && value.results) || []
        if (!rows.length) {
          if (value && value.reason === 'nothing_beyond_context') {
            return [{ type: 'text', text: '这条会话没有更早的内容了（都还在当前上下文里）。' }]
          }
          return [{ type: 'text', text: '记忆库里没有找到相关内容。' }]
        }
        const lines = rows.map((row) => '- [' + (row.date || '未知日期') + '] '
          + (row.role ? row.role + '：' : '') + String(row.sentence || '')
          + (row.hits_in_event > 1 ? '（同一事件另有 ' + (row.hits_in_event - 1) + ' 句命中）' : '')
          + (row.event_summary ? '  ← 事件：' + row.event_summary : '')
          + '  {entry=' + (row.entry_id || '-') + (row.event_id ? ' event=' + row.event_id : '') + '}')
        return [{ type: 'text', text: '记忆检索（句子级，同一事件合并成一行）：\n'
          + lines.join('\n') + '\n要上下文用 memory_read event_id=…；要单条用 entry_id=…' }]
      },
    },
    async execute(args, exec) {
      const input = args && typeof args === 'object' ? args : {}
      const query = String(input.query || '').trim()
      if (!query) throw new Error('memory_search: query 必填')
      const sessionId = exec && exec.agent && exec.agent.session
        ? String(exec.agent.session.id || '') : ''
      if (!sessionId) throw new Error('memory_search: no owning session')
      const store = String(getStore() || '')
      const result = await search(store, sessionId, {
        query,
        top_k: clampTopK(input.top_k),
        scope: normalizeScope(input.scope),
        // include_context 是调试开关，模型看不到也传不进来（这里显式固定为 false）
        include_context: false,
      })
      const rows = result && Array.isArray(result.results) ? result.results : []
      const reranked = !!(result && result.rerank === true)
      const rerankModel = String((result && result.rerank_model) || '')
      const reason = String((result && result.reason) || '')
      return {
        count: rows.length,
        ...(reason ? { reason } : {}),
        results: rows.map((row) => ({
          date: String((row && row.date) || ''),
          role: String((row && row.role) || ''),
          sentence: String((row && row.sentence) || '').slice(0, SENTENCE_LIMIT),
          event_summary: String((row && row.event_summary) || '').slice(0, 40),
          entry_id: String((row && row.entry_id) || ''),
          event_id: String((row && row.event_id) || ''),
          hits_in_event: Number((row && row.hits_in_event) || 1),
          match: (row && row.match) === 'lexical' ? 'lexical' : 'embedding',
          rerank: reranked,
          rerank_model: rerankModel,
        })),
      }
    },
    presentCall: (args) => ({
      card: 'generic',
      title: 'Search memory',
      kind: 'search',
      rawInput: (args && typeof args === 'object' ? args.query : '') || '',
    }),
  })
}

export const LIST_DESCRIPTION = [
  '看记忆目录：按"事件"列，一行一件事——事件 id、日期范围、条目数、一行总结（≤40 字）。',
  '同一件事只出现一次（按 event_id 去重；老数据按总结文本去重）。',
  '要整件用 memory_read event_id=…，要单条用 entry_id；不要一次读超过 5 条。',
  '需要回忆时先 memory_list 再 memory_read——不要用关键词或字符匹配去翻会话日志、历史记录或文件。',
].join('\n')

export const READ_DESCRIPTION = [
  '按 id 取原文，两级：给 event_id 读整件（事件总结 + 全部成员条目，按时间拼接，≤4000 字）；',
  '给 entry_id 读单条（≤2000 字）。id 都来自 memory_list 或 memory_search。',
  '先 memory_list 看目录再挑需要的读；不要一次读超过 5 条。不要用关键词翻会话日志。',
].join('\n')

export const LIST_PARAMETERS = Object.freeze({
  query: {
    type: 'string',
    description: '可选：只想看跟某件事有关的目录时给一句自然语言；不给就列全部（新在前）。',
  },
  limit: {
    type: 'integer',
    description: '这一页几条，1–100，缺省 ' + LIST_LIMIT_DEFAULT + '。',
  },
  offset: {
    type: 'integer',
    description: '从第几条开始（分页用，缺省 0）。',
  },
})

export const READ_PARAMETERS = Object.freeze({
  event_id: {
    type: 'string',
    description: '事件 id（memory_list 的每行 id、或 memory_search 的 event_id）：读整件。',
  },
  entry_id: {
    type: 'string',
    description: '条目 id（memory_search 的 entry_id）：读单条原文。',
  },
})

export const LIST_OUTPUT_SCHEMA = Object.freeze({
  type: 'object',
  additionalProperties: false,
  properties: {
    total: { type: 'integer', required: true },
    reason: { type: 'string' },
    items: {
      type: 'array',
      required: true,
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          id: { type: 'string', required: true },
          date_range: { type: 'array', required: true, items: { type: 'string' } },
          entries: { type: 'integer', required: true },
          summary: { type: 'string', required: true },
          summary_source: { type: 'string', required: true },
        },
      },
    },
  },
})

export const READ_OUTPUT_SCHEMA = Object.freeze({
  type: 'object',
  additionalProperties: false,
  properties: {
    id: { type: 'string', required: true },
    date: { type: 'string', required: true },
    role: { type: 'string', required: true },
    kind: { type: 'string', required: true },
    level: { type: 'string', required: true },
    text: { type: 'string', required: true },
  },
})

/** limit 归一：1–100（非数字给缺省）。 */
export function clampLimit(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return LIST_LIMIT_DEFAULT
  return Math.max(1, Math.min(LIST_LIMIT_MAX, Math.round(parsed)))
}

/** offset 归一：>= 0。 */
export function clampOffset(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0) return 0
  return Math.round(parsed)
}

/** 造 memory_list 的工具定义（按事件列）。 */
export function buildMemoryListSpec(options) {
  const opts = options || {}
  const defineTool = opts.defineTool
  const list = typeof opts.list === 'function' ? opts.list : memoryList
  const getStore = typeof opts.getStore === 'function' ? opts.getStore : () => ''
  if (typeof defineTool !== 'function') throw new Error('buildMemoryListSpec: missing defineTool')
  return defineTool({
    name: LIST_TOOL_NAME,
    description: LIST_DESCRIPTION,
    parameters: LIST_PARAMETERS,
    output: {
      schema: LIST_OUTPUT_SCHEMA,
      render: (_args, value) => {
        const items = (value && value.items) || []
        if (!items.length) {
          if (value && value.reason === 'nothing_beyond_context') {
            return [{ type: 'text', text: '这条会话没有更早的内容了（都还在当前上下文里）。' }]
          }
          return [{ type: 'text', text: '记忆目录是空的。' }]
        }
        const lines = items.map((row) => '- ' + row.id + ' [' + (row.date_range || []).join(' ~ ')
          + '] ' + row.entries + ' 条：' + row.summary
          + (row.summary_source === 'first_sentence' ? '（无总结，这是首句）' : ''))
        return [{ type: 'text', text: '记忆目录（按事件，共 ' + (value.total || items.length)
          + ' 件，新在前）：\n' + lines.join('\n')
          + '\n要整件用 memory_read event_id=…（一次别超过 5 件）' }]
      },
    },
    async execute(args, exec) {
      const input = args && typeof args === 'object' ? args : {}
      const sessionId = exec && exec.agent && exec.agent.session
        ? String(exec.agent.session.id || '') : ''
      if (!sessionId) throw new Error('memory_list: no owning session')
      const payload = { limit: clampLimit(input.limit), offset: clampOffset(input.offset) }
      const query = String(input.query || '').trim()
      if (query) payload.query = query
      // include_context 固定不传（模型侧没有这个开关）
      const result = await list(String(getStore() || ''), sessionId, payload)
      const items = result && Array.isArray(result.items) ? result.items : []
      const listReason = String((result && result.reason) || '')
      return {
        total: Number(result && result.total != null ? result.total : items.length),
        ...(listReason ? { reason: listReason } : {}),
        items: items.map((row) => ({
          id: String((row && row.id) || ''),
          date_range: Array.isArray(row && row.date_range)
            ? row.date_range.map((item) => String(item || '')) : [],
          entries: Number((row && row.entries) || 0),
          summary: String((row && row.summary) || '').slice(0, 40),
          summary_source: (row && row.summary_source) === 'first_sentence'
            ? 'first_sentence' : 'event',
        })),
      }
    },
    presentCall: () => ({ card: 'generic', title: 'List memory events', kind: 'search', rawInput: '' }),
  })
}

/** 造 memory_read 的工具定义（event_id 读整件 / entry_id 读单条）。 */
export function buildMemoryReadSpec(options) {
  const opts = options || {}
  const defineTool = opts.defineTool
  const read = typeof opts.read === 'function' ? opts.read : memoryRead
  const getStore = typeof opts.getStore === 'function' ? opts.getStore : () => ''
  if (typeof defineTool !== 'function') throw new Error('buildMemoryReadSpec: missing defineTool')
  return defineTool({
    name: READ_TOOL_NAME,
    description: READ_DESCRIPTION,
    parameters: READ_PARAMETERS,
    output: {
      schema: READ_OUTPUT_SCHEMA,
      render: (_args, value) => {
        if (!value || value.ok === false) return [{ type: 'text', text: '没找到这条记忆。' }]
        return [{ type: 'text', text: '[' + value.date + '] ' + value.role + '：' + value.text }]
      },
    },
    async execute(args, exec) {
      const input = args && typeof args === 'object' ? args : {}
      const eventId = String(input.event_id || '').trim()
      const entryId = String(input.entry_id || input.id || '').trim()
      if (!eventId && !entryId) throw new Error('memory_read: 需要 event_id 或 entry_id')
      const sessionId = exec && exec.agent && exec.agent.session
        ? String(exec.agent.session.id || '') : ''
      if (!sessionId) throw new Error('memory_read: no owning session')
      const payload = eventId ? { event_id: eventId } : { entry_id: entryId }
      const result = await read(String(getStore() || ''), sessionId, payload)
      if (!result || result.ok !== true) throw new Error('memory_read: not_found')
      const isEvent = String(result.level || '') === 'event' || result.kind === 'event'
      return {
        id: String(result.id || eventId || entryId),
        date: String(result.date || ''),
        role: String(result.role || ''),
        kind: isEvent ? 'event' : (result.kind === 'compaction' ? 'compaction' : 'entry'),
        level: isEvent ? 'event' : 'entry',
        text: String(result.text || '').slice(0, isEvent ? EVENT_TEXT_LIMIT : READ_TEXT_LIMIT),
      }
    },
    presentCall: (args) => ({ card: 'generic', title: 'Read memory', kind: 'other',
      rawInput: (args && (args.event_id || args.entry_id)) || '' }),
  })
}

// 同一个宿主注册表只注册一次（key 是 tools 对象本身）：重载 / 重复 apply 都不会重复挂。
const HOST_REGISTERED = new WeakMap()

export function registerMemorySearchTool(ctx, getStore, options = {}) {
  if (!ctx) return null
  const opts = options || {}
  const builders = [
    [TOOL_NAME, buildMemoryToolSpec],
    [LIST_TOOL_NAME, buildMemoryListSpec],
    [READ_TOOL_NAME, buildMemoryReadSpec],
  ]
  const registered = new Set()
  const state = { where: '', dispose: null }
  const debug = (line) => {
    if (!process.env.BIOCORE_DEBUG_CAPTURE) return
    try { console.error(line) } catch { /* logging must not throw */ }
  }
  const done = () => registered.size >= builders.length
  const attach = (tools, where) => {
    if (done()) return true
    if (!tools || typeof tools.register !== 'function') return false
    const defineTool = typeof opts.defineTool === 'function' ? opts.defineTool : HOST_DEFINE_TOOL
    if (typeof defineTool !== 'function') {
      debug('[tool] registered=' + registered.size + '/' + builders.length
        + ' where=' + where + ' reason=no_define_tool')
      return false
    }
    let hostSet = HOST_REGISTERED.get(tools)
    if (!hostSet) { hostSet = new Set(); HOST_REGISTERED.set(tools, hostSet) }
    for (const [name, build] of builders) {
      if (registered.has(name) || hostSet.has(name)) continue
      try {
        const spec = build(Object.assign({}, opts, { getStore, defineTool }))
        const dispose = tools.register(spec)
        registered.add(name)
        hostSet.add(name)
        if (typeof dispose === 'function') state.dispose = dispose
        debug('[tool] registered=1 where=' + where + ' name=' + name)
      } catch (error) {
        debug('[tool] registered=0 where=' + where + ' name=' + name + ' error='
          + String((error && error.message) || error).slice(0, 160))
      }
    }
    if (registered.size) state.where = where
    debug('[tool] registered=' + registered.size + '/' + builders.length + ' where=' + where)
    return done()
  }

  // 三条路都留：哪条先成功就用哪条，registered 集合保证每个工具只注册一次。
  const disposers = []
  const stopWaiting = () => {
    while (disposers.length) {
      const dispose = disposers.pop()
      try { if (typeof dispose === 'function') dispose() } catch { /* ignore */ }
    }
  }
  const maybeReady = (where) => {
    if (done()) { stopWaiting(); return true }
    return attach(ctx && ctx.tools, where)
  }

  const hasTools = !!(ctx && ctx.tools && typeof ctx.tools.register === 'function')
  const canInject = typeof ctx.inject === 'function'
  debug('[tool] ctx.tools=' + (hasTools ? 1 : 0) + ' inject=' + (canInject ? 1 : 0)
    + ' registered=0')
  // 1) apply 时 ctx.tools 就绪
  maybeReady('ctx.tools')
  debug('[tool] after-apply registered=' + registered.size + '/' + builders.length)
  if (done()) return () => { try { if (state.dispose) state.dispose() } catch { /* ignore */ } }

  // 2) 事件触发：第一次会话事件时 tools 服务一定已经在了
  if (typeof ctx.on === 'function') {
    for (const name of ['session/event', 'agent/pre-step']) {
      try {
        const dispose = ctx.on(name, () => { maybeReady(name) })
        if (typeof dispose === 'function') {
          disposers.push(dispose)
          if (done()) stopWaiting()
        }
      } catch { /* 事件通道不可用 */ }
    }
  }

  // 3) cordis 的 inject(['tools'])：服务出现时回调
  if (canInject) {
    try {
      const dispose = ctx.inject(['tools'], (serviceCtx) => {
        attach(serviceCtx && serviceCtx.tools, 'inject')
        if (done()) stopWaiting()
      })
      if (typeof dispose === 'function') disposers.push(dispose)
    } catch { /* inject 通道不可用 */ }
  }

  return () => {
    stopWaiting()
    try { if (state.dispose) state.dispose() } catch { /* ignore */ }
  }
}

/** 三个工具一起注册（名字沿用旧导出，index.js 不用改）。 */
export const registerMemoryTools = registerMemorySearchTool
