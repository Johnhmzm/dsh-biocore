/** Thin tool glue: definitions + forwarding to pipeline.tool_call.
 *
 * All remember/recall/forget policy (dedupe, limits, per-session store) lives
 * in the python pipeline; this file only registers the model-facing schemas
 * and forwards args/exec session id.
 */
import { memoryTool } from '../py-bridge.js'

export const MEMORY_TOOL_NAMES = Object.freeze([
  'biocore_remember', 'biocore_recall', 'biocore_forget',
])

function stringProp(description) {
  return { type: 'string', description }
}

export function sessionIdFromExec(exec) {
  try {
    if (!exec) return 'global'
    const raw = (exec.agent && exec.agent.sessionId)
      || (exec.agent && exec.agent.session && exec.agent.session.id)
      || (exec.session && exec.session.id)
      || exec.sessionId
    return raw == null || raw === '' ? 'global' : String(raw)
  } catch { return 'global' }
}

export function buildMemoryToolDefinitions(storeDir, getConfig) {
  const cfg = () => {
    try { return (typeof getConfig === 'function' ? getConfig() : getConfig) || {} } catch { return {} }
  }
  const run = async (name, args, exec) => {
    if (cfg().memoryEnabled === false) return { ok: false, reason: 'memory_disabled' }
    const response = await memoryTool(storeDir, sessionIdFromExec(exec), name, args || {})
    const flat = response && response.result && typeof response.result === 'object'
      ? response.result : {}
    if (response && response.ok === true) return { ok: true, ...flat }
    return { ok: false, ...flat,
      reason: (response && response.reason) || flat.reason || 'memory_bridge_failed' }
  }
  return [
    {
      name: 'biocore_remember',
      description: '记住一条值得长期保留的事（一句话）。',
      parameters: {
        type: 'object',
        properties: {
          text: stringProp('要记住的一句话'),
          tags: { type: 'array', items: { type: 'string' }, description: '可选标签' },
        },
        required: ['text'],
        additionalProperties: false,
      },
      execute: async (args, exec) => run('biocore_remember', args, exec),
    },
    {
      name: 'biocore_recall',
      description: '按关键词搜索记忆，返回最相关的几条。',
      parameters: {
        type: 'object',
        properties: {
          query: stringProp('要搜索的关键词'),
          limit: { type: 'integer', description: '返回条数（1-10，默认 3）' },
        },
        required: ['query'],
        additionalProperties: false,
      },
      execute: async (args, exec) => run('biocore_recall', args, exec),
    },
    {
      name: 'biocore_forget',
      description: '忘掉一条记忆：按 id 或最匹配的查询。',
      parameters: {
        type: 'object',
        properties: {
          id: stringProp('要忘掉的记忆 id'),
          query: stringProp('没有 id 时用于匹配的关键词'),
        },
        additionalProperties: false,
      },
      execute: async (args, exec) => run('biocore_forget', args, exec),
    },
  ]
}

export function registerMemoryTools(ctx, getConfig, storeDir) {
  if (!ctx || typeof ctx.inject !== 'function') return null
  const definitions = buildMemoryToolDefinitions(storeDir, getConfig)
  const disposers = []
  const registered = []
  try {
    ctx.inject(['tools'], (serviceCtx) => {
      const tools = serviceCtx && serviceCtx.tools
      if (!tools || typeof tools.register !== 'function') return
      for (const definition of definitions) {
        try {
          const dispose = tools.register(definition)
          if (typeof dispose === 'function') disposers.push(dispose)
          registered.push(definition.name)
        } catch { /* duplicate name or rejected definition */ }
      }
    })
  } catch { /* cordis inject unavailable */ }
  return {
    registered,
    definitions,
    dispose: () => { for (const dispose of disposers) { try { dispose() } catch { /* ignore */ } } },
  }
}
