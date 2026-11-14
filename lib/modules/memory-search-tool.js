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
import { memorySearch } from '../py-bridge.js'

/** 宿主工具构造器：模块加载时取一次；宿主没有这个包时退回 null（单测自己注入）。 */
let HOST_DEFINE_TOOL = null
try {
  const hostTools = await import('@deepseek-ai/dsh-tools')
  HOST_DEFINE_TOOL = typeof hostTools.defineTool === 'function' ? hostTools.defineTool : null
} catch { HOST_DEFINE_TOOL = null }

export const TOOL_NAME = 'memory_search'
export const TOP_K_DEFAULT = 8
export const TOP_K_MIN = 1
export const TOP_K_MAX = 20
export const TEXT_LIMIT = 400
export const SCOPES = Object.freeze(['all', 'text', 'compaction'])

export const TOOL_DESCRIPTION = [
  '检索长期记忆：回忆过去发生过什么、用户以前说过什么、之前定过什么结论，都用它。',
  '需要回忆时请**先调用本工具**——不要用关键词或字符匹配去翻会话日志、历史记录或文件来"找那句话"；',
  '字符匹配只能找到字面相同的话，找不到换了说法的事实。',
  '返回内容是记忆库里的原文片段与压缩目录，每条都带日期与角色，并按日期排序（新的在前）。',
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
    results: {
      type: 'array',
      required: true,
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          date: { type: 'string', required: true },
          role: { type: 'string', required: true },
          text: { type: 'string', required: true },
          kind: { type: 'string', required: true, enum: ['entry', 'compaction'] },
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
        if (!rows.length) return [{ type: 'text', text: '记忆库里没有找到相关内容。' }]
        const lines = rows.map((row) => '- [' + (row.date || '未知日期') + '] '
          + (row.kind === 'compaction' ? '（压缩目录）' : (row.role ? row.role + '：' : ''))
          + String(row.text || '').replace(/\s+/g, ' '))
        return [{ type: 'text', text: '记忆检索结果（新在前）：\n' + lines.join('\n') }]
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
      })
      const rows = result && Array.isArray(result.results) ? result.results : []
      const shaped = rows.map(shapeResult)
      return { count: shaped.length, results: shaped }
    },
    presentCall: (args) => ({
      card: 'generic',
      title: 'Search memory',
      kind: 'search',
      rawInput: (args && typeof args === 'object' ? args.query : '') || '',
    }),
  })
}

/**
 * 在宿主上注册这个工具。优先直接注册（ctx.tools 已就绪），否则用 cordis 的
 * ctx.inject(['tools'], ...) 等它出现。没有 tools 服务就安静跳过（headless 档）。
 * @returns 清理函数（可空）
 */
export function registerMemorySearchTool(ctx, getStore, options = {}) {
  if (!ctx) return null
  const opts = options || {}
  const attach = (serviceCtx) => {
    try {
      if (!serviceCtx || !serviceCtx.tools || typeof serviceCtx.tools.register !== 'function') {
        return undefined
      }
      const defineTool = typeof opts.defineTool === 'function' ? opts.defineTool : HOST_DEFINE_TOOL
      const spec = buildMemoryToolSpec(Object.assign({}, opts, { getStore, defineTool }))
      const dispose = serviceCtx.tools.register(spec)
      return typeof dispose === 'function' ? dispose : undefined
    } catch { return undefined }
  }
  try {
    if (ctx.tools && typeof ctx.tools.register === 'function') return attach(ctx) || null
    if (typeof ctx.inject === 'function') {
      let dispose = null
      try {
        ctx.inject(['tools'], (serviceCtx) => { dispose = attach(serviceCtx) || null })
      } catch { return null }
      return () => { try { if (typeof dispose === 'function') dispose() } catch { /* ignore */ } }
    }
  } catch { /* tools 通道不可用：不影响插件其它部分 */ }
  return null
}
