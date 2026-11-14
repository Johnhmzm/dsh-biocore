/** BioCore 的压缩产物：继承官方 BasicCompactionEngine，只覆盖 summarize()。
 *
 * 官方源码（dsh-compaction-basic 的类注释）写明 summarize() 是**唯一的子类定制点**，
 * 重放前缀、区间选择、工具配对校验、durable 写入、溢出恢复、重试上限全部固定。
 * 所以这里不自己写触发：request-error / 溢出 / 保留尾部 / 锁 全继承官方的。
 *
 * summarize() 只做两件事不同：
 *   1) 提示词：让模型按我们的「压缩目录」格式写（一行一件，只写原文事实）；
 *   2) 解析：取模型正文（与官方一致）——失败就退到桥的 compaction_index（0 调用）。
 * 其余字段（provider/model/maxTokens/usage/rawOutput/llmStreamCall）与官方逐字一致，
 * 保证 compaction/summary 事件可复原。
 *
 * 另外多覆盖了 compactIfNeeded()，但只做一件事：每次自动检查前把用户配置的阈值
 * （core_config.json 的 recall.compaction_threshold_tokens / _ratio / _context_window）
 * 刷进 this.config，再原样交给官方判定——判定流程一行没改。溢出恢复
 * （trigger==='context-overflow'）不看阈值，原样透传。
 */
import { appendFileSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { compactionIndex } from '../py-bridge.js'
import { resolveMemoryDir } from './memory-store.js'

/** 降级路径（库内已有摘要）的署名：一眼能看出这份摘要没调模型。 */
export const FALLBACK_PROVIDER = 'biocore-index'
export const FALLBACK_MODEL = 'biocore-index-1'
/** 目录最长多少字符（官方还会再按"摘要必须比被压内容小"卡一道）。 */
export const DIRECTORY_MAX_CHARS = 2000
/** 提示词：目录格式 + 约束（只写事实 / 每行 ≤40 字 / 总量 ≤20%）。 */
export const DIRECTORY_RULES = [
  '不要再执行任何任务，也不要调用任何工具：只把上面的对话压成一份「压缩目录」。',
  '',
  '严格按下面的格式输出，不要加别的段落、不要用 Markdown 标题：',
  '【压缩目录】覆盖 HH:MM–HH:MM，共 N 条消息',
  '- HH:MM 这件事是什么；若原文给了结论，写「结论是 …」',
  '- HH:MM …',
  '',
  '规则：',
  '- 只写原文里明确出现的事实。原文没说的不要推断，不要润色，不要总结成抽象概念。',
  '- 每行不超过 40 个字；一行只写一件事，按时间先后排。',
  '- 同一时间窗里重复发生的同一件事只写一行，把它并进那一行，不要在相邻几行里重复列同一条。',
  '- 目录只陈述事实：不要写问句，不要加问号，不要猜测对方想问什么。',
  '- 总字数不超过被压内容的 20%：宁可少写几条，也不要写长。',
  '- 分不出"件事"时就按时间分段（例如每 5–10 分钟一行），不要硬凑。',
  '- 路径、命令、报错、数字这些需要复原的细节照抄原文。',
  '- 只输出目录正文，不要解释你在做什么，也不要提到"压缩"或"上下文"。',
].join('\n')

/** core_config.json 里我们能改的三个压缩键（都在 recall.* 下，与其它 recall 设置同源）。 */
export const COMPACTION_SETTING_KEYS = Object.freeze([
  'compaction_threshold_tokens',
  'compaction_threshold_ratio',
  'compaction_context_window',
])

/** 从 <store>/core_config.json（或上一层）读压缩阈值；按 mtime 缓存，改完下一次检查就生效。 */
export function readCompactionSettings(store, cache) {
  const empty = { tokens: 0, ratio: 0, window: 0, path: '' }
  const base = String(store || '')
  if (!base) return empty
  const seen = cache instanceof Map ? cache : new Map()
  for (const path of [join(base, 'core_config.json'), join(base, '..', 'core_config.json')]) {
    let stamp = 0
    try { stamp = statSync(path).mtimeMs } catch { continue }
    const hit = seen.get(path)
    if (hit && hit.stamp === stamp) return hit.value
    let doc = null
    try { doc = JSON.parse(readFileSync(path, 'utf8')) } catch { doc = null }
    const recall = doc && typeof doc === 'object' && doc.recall && typeof doc.recall === 'object'
      ? doc.recall : {}
    const number = (value) => {
      const parsed = Number(value)
      return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
    }
    const value = {
      tokens: Math.floor(number(recall.compaction_threshold_tokens)),
      ratio: Math.min(0.95, number(recall.compaction_threshold_ratio)),
      window: Math.floor(number(recall.compaction_context_window)),
      path,
    }
    seen.set(path, { stamp, value })
    return value
  }
  return empty
}

/** 一行调试输出：有 BIOCORE_DEBUG_FILE 就追加到文件（PowerShell 的 stderr 会折行），否则打到 stderr。 */
function debugThreshold(line) {
  try {
    const file = process.env.BIOCORE_DEBUG_FILE
    if (file) appendFileSync(file, line + '\n')
    else console.error(line)
  } catch { /* logging must not throw */ }
}

/** 本地时钟 HH:MM（目录里的时间列）。 */
export function clockOf(stamp) {
  const value = Number(stamp)
  if (!Number.isFinite(value) || value <= 0) return '--:--'
  const date = new Date(value)
  const pad = (n) => String(n).padStart(2, '0')
  return pad(date.getHours()) + ':' + pad(date.getMinutes())
}

/** 当前表面的时间跨度与消息条数：给提示词一个精确的头，也给降级路径一个时间窗。 */
export function surfaceSpan(session) {
  const out = { start: null, end: null, messages: 0, roles: { user: 0, assistant: 0 } }
  const nodes = session && session.surface ? session.surface.nodes : []
  for (const seq of nodes || []) {
    const event = session.eventAt(seq)
    if (!event) continue
    const stamp = Number(event.time)
    if (Number.isFinite(stamp) && stamp > 0) {
      if (out.start === null || stamp < out.start) out.start = stamp
      if (out.end === null || stamp > out.end) out.end = stamp
    }
    if (event.type === 'user/message') {
      out.messages += 1
      out.roles.user += 1
    } else if (event.type === 'assistant/message') {
      out.messages += 1
      out.roles.assistant += 1
    }
  }
  return out
}

/** 把提示词里那两处占位（时间跨度、条数）换成实测值。 */
export function summarizationInstruction(span) {
  if (!span || span.start === null) return DIRECTORY_RULES
  const head = '本次要压缩的区间：' + clockOf(span.start) + '–' + clockOf(span.end)
    + '，共 ' + span.messages + ' 条消息（我 ' + span.roles.user + ' / 她 ' + span.roles.assistant + '）。'
  return head + '\n\n' + DIRECTORY_RULES
}

/** 模型输出 -> 我们认的目录正文（补齐头、裁掉超长尾巴；不切半行）。 */
export function frameDirectory(text, span, maxChars = DIRECTORY_MAX_CHARS) {
  const raw = String(text || '').trim()
  if (!raw) return ''
  const lines = raw.split('\n').map((line) => line.replace(/\s+$/, '')).filter((line) => line.trim())
  if (lines.length === 0) return ''
  let head = lines[0].trim()
  if (!head.startsWith('【压缩目录】')) {
    const fallbackHead = '【压缩目录】覆盖 ' + clockOf(span && span.start) + '–'
      + clockOf(span && span.end) + '，共 ' + ((span && span.messages) || 0) + ' 条消息'
    lines.unshift(head.startsWith('-') ? fallbackHead : fallbackHead)
    head = lines[0]
  }
  const capped = []
  let used = 0
  for (const line of lines) {
    if (used + line.length + 1 > maxChars) break
    capped.push(line)
    used += line.length + 1
  }
  if (capped.length === 0) return raw.slice(0, maxChars)
  if (capped.length < lines.length) capped.push('（目录过长，已按上限截断）')
  return capped.join('\n')
}

/**
 * 造出引擎类：继承官方实现，只覆盖 summarize()。
 * @param deps - { BasicCompactionEngine, BlockAssembler, LlmError, contentHasImage,
 *                 directory? }；directory 是降级路径（默认调桥），测试可注入。
 */
export function createCompactionEngineClass(deps) {
  const {
    BasicCompactionEngine,
    BlockAssembler,
    LlmError,
    contentHasImage,
    directory,
    store,
  } = deps || {}
  if (typeof BasicCompactionEngine !== 'function') {
    throw new Error('createCompactionEngineClass: missing BasicCompactionEngine')
  }
  if (typeof BlockAssembler !== 'function' || typeof LlmError !== 'function') {
    throw new Error('createCompactionEngineClass: missing BlockAssembler/LlmError')
  }
  const fallbackDirectory = typeof directory === 'function'
    ? directory
    : (store, sessionId, payload) => compactionIndex(store, sessionId, payload)

  return class BiocoreCompactionEngine extends BasicCompactionEngine {
    constructor(ctx, config) {
      super(ctx, config)
      // 官方 resolved config 是冻结的：留一份原样，用户把阈值改回 0/0 时恢复它。
      this.baseConfig = { ...(this.config || {}) }
      this.settingCache = new Map()
      this.settingStore = String(store || '') || resolveMemoryDir({})
    }

    /**
     * 唯一多出来的入口：每次自动压缩检查前，把 core_config.json 里的阈值刷进 this.config，
     * 再原样交给官方判定（不重写任何判定流程）。溢出恢复（trigger==='context-overflow'）
     * 不看阈值，也照原样透传。
     */
    async compactIfNeeded(agent, trigger, signal) {
      const settings = readCompactionSettings(this.settingStore, this.settingCache)
      this.applyThresholdSettings(settings)
      if (process.env.BIOCORE_DEBUG_CAPTURE) {
        debugThreshold('[thr] trigger=' + trigger + ' tokens=' + settings.tokens
          + ' ratio=' + settings.ratio + ' window=' + settings.window
          + ' -> ratio=' + this.config.thresholdRatio
          + ' headroom=' + this.config.headroomTokens
          + ' retain=' + (this.config.retainTokens === undefined
            ? ('ratio:' + this.config.retainRatio) : this.config.retainTokens)
          + ' measured=' + (() => {
            try { return this.ctx.tokenMeter.measure(agent.session).totalTokens } catch { return '?' }
          })()
          + ' nodes=' + (() => {
            try {
              return this.ctx.tokenMeter.measure(agent.session).nodes
                .map((node) => node.seq + ':' + node.tokens).join(',')
            } catch { return '?' }
          })())
      }
      if (trigger !== 'pressure' || !(settings.window > 0)) {
        return super.compactIfNeeded(agent, trigger, signal)
      }
      // 适配器没声明窗口时，用配置的窗口兜底：只在这一次调用期间补 resolveModelInfo。
      const llm = this.ctx.llm
      if (!llm || typeof llm.resolveModelInfo !== 'function') {
        return super.compactIfNeeded(agent, trigger, signal)
      }
      const original = llm.resolveModelInfo
      llm.resolveModelInfo = async (provider, model, infoSignal) => {
        const info = await original.call(llm, provider, model, infoSignal)
        const declared = info && info.context ? Number(info.context.contextWindow) : 0
        if (declared > 0) return info
        return { ...(info || {}), context: { ...((info || {}).context || {}), contextWindow: settings.window } }
      }
      try {
        const result = await super.compactIfNeeded(agent, trigger, signal)
        if (process.env.BIOCORE_DEBUG_CAPTURE) {
          debugThreshold('[thr] super -> ' + (result === null ? 'null' : 'compacted'))
        }
        return result
      } catch (error) {
        if (process.env.BIOCORE_DEBUG_CAPTURE) {
          debugThreshold('[thr] super threw ' + String((error && error.name) || '')
            + ': ' + String((error && error.message) || error).slice(0, 160))
        }
        throw error
      } finally {
        llm.resolveModelInfo = original
      }
    }

    /**
     * 把用户设置换算成官方认的 config：绝对阈值优先，其次比例，都 0 就恢复 YAML 原值。
     * 绝对阈值换算成等价 ratio（官方公式 threshold = floor(min(W*ratio, W-reserved-headroom))），
     * 并把保留量压到阈值的 20%（官方要求 retainTokens < thresholdTokens）。
     */
    applyThresholdSettings(settings) {
      const base = this.baseConfig || {}
      const window = Number(settings.window) || 0
      const tokens = Number(settings.tokens) || 0
      const ratio = Number(settings.ratio) || 0
      if (tokens > 0 && window > 0) {
        this.config = {
          ...base,
          thresholdRatio: Math.min(0.95, tokens / window),
          headroomTokens: 0,
          retainTokens: Math.max(1, Math.floor(tokens * 0.2)),
          retainRatio: undefined,
        }
        return
      }
      if (ratio > 0) {
        this.config = { ...base, thresholdRatio: ratio }
        return
      }
      this.config = { ...base }
    }

    async summarize(input, agent, signal) {
      // ---- 目标模型：与官方 summarize() 同一条解析顺序（config > 最近一次路由 > agent 选项）
      const config = this.config || {}
      const latest = agent.session.requestHeader && agent.session.requestHeader()
      const latestConfig = latest && latest.config ? latest.config : undefined
      const configured = String(config.summarizationProvider || '').length > 0
        ? { provider: String(config.summarizationProvider), model: String(config.summarizationModel) }
        : undefined
      const options = agent.options || {}
      const agentTarget = options.provider && options.model
        ? { provider: String(options.provider), model: String(options.model) }
        : undefined
      const target = configured || latestConfig || agentTarget
      if (!target || !target.provider || !target.model) {
        throw new Error('no provider/model available for summarization: set both BasicCompactionConfig summarization fields, route one request, or set both AgentOptions fields')
      }

      const span = surfaceSpan(agent.session)
      const instruction = summarizationInstruction(span)

      // ---- 与官方 summarizeWithLlm 逐字一致：同一份前缀 + 我们的提示词，一次 stream
      let modelError = null
      try {
        const assembler = new BlockAssembler()
        const messages = [...input.messages, {
          role: 'user',
          content: [{ type: 'text', text: instruction }],
        }]
        const request = {
          provider: target.provider,
          model: target.model,
          messages,
          toolHistory: agent.session.toolHistory(),
          ...(input.tools === undefined ? {} : { tools: [...input.tools] }),
          maxTokens: config.maxTokens,
          sessionId: agent.session.id,
          purpose: 'compaction',
          ...(signal === undefined ? {} : { signal }),
        }
        for await (const chunk of this.ctx.llm.stream(request)) assembler.push(chunk)
        const failure = finishError(assembler.finish, LlmError)
        if (failure !== undefined) throw failure
        const rawOutput = assembler.blocks()
        const summary = summaryText(rawOutput, contentHasImage, LlmError)
        const text = summary.map((block) => block.text).join('\n').trim()
        if (!text) throw new Error('summarization produced no text summary content')
        const framed = frameDirectory(text, span)
        if (!framed) throw new Error('summarization produced no usable directory content')
        return {
          summary: [{ type: 'text', text: framed }],
          rawOutput,
          llmStreamCall: true,
          provider: request.provider,
          model: request.model,
          maxTokens: config.maxTokens,
          ...(assembler.usage === undefined ? {} : { usage: assembler.usage }),
        }
      } catch (error) {
        if (signal && signal.aborted) throw error
        modelError = error
        if (process.env.BIOCORE_DEBUG_CAPTURE) {
          try {
            console.error('[sum] fallback code=' + String((error && error.code) || '')
              + ' msg=' + String((error && error.message) || error).slice(0, 120))
          } catch { /* logging must not throw */ }
        }
      }

      // ---- 降级：模型不行就退回库内已有摘要（0 次模型调用，provider 能区分）
      this.ctx.logger.warn('biocore compaction: model summary failed ('
        + (modelError instanceof Error ? modelError.message : String(modelError))
        + '); falling back to the stored index')
      const text = await directoryFromStore(this.ctx, agent, span, fallbackDirectory, store)
      if (!text) {
        throw modelError instanceof Error ? modelError
          : new Error(String(modelError || 'summarization failed and no stored index is available'))
      }
      return {
        summary: [{ type: 'text', text }],
        rawOutput: [{ type: 'text', text }],
        provider: FALLBACK_PROVIDER,
        model: FALLBACK_MODEL,
      }
    }

  }
}

/** 降级内容：桥按库内已有摘要拼目录；拿不到就返回空串（调用方抛原模型错误）。
 *
 * 放在类外面是刻意的：子类的原型上只允许有 summarize 一个自有方法。
 */
async function directoryFromStore(ctx, agent, span, call, store) {
  try {
    const sessionId = String(agent.session.id || '')
    if (!sessionId) return ''
    const memoryStore = String(store || '') || resolveMemoryDir({})
    const result = await call(memoryStore, sessionId, {
      start_ts: Number(span.start) / 1000,
      end_ts: Number(span.end) / 1000,
      messages: span.messages,
      roles: span.roles,
    })
    if (!result || result.ok !== true || typeof result.text !== 'string') return ''
    return frameDirectory(result.text, span)
  } catch (error) {
    ctx.logger.warn('biocore compaction: stored index unavailable: '
      + (error instanceof Error ? error.message : String(error)))
    return ''
  }
}

/** 官方 finishError 的等价物（各类终止原因都要显式失败，不能写出半截摘要）。 */
function finishError(finish, LlmError) {
  if (!finish) return undefined
  if (finish.kind === 'error' || finish.kind === 'aborted') {
    return new LlmError(finish.failure.message, finish.failure.code, finish.failure)
  }
  if (finish.kind === 'max-tokens') {
    const error = new Error('summarization truncated at the token cap (incomplete checkpoint)')
    error.code = 'MAX_TOKENS'
    return error
  }
  return undefined
}

/** 官方 summaryText 的等价物：拒图、只留 text 块。 */
function summaryText(blocks, contentHasImage, LlmError) {
  if (typeof contentHasImage === 'function' && contentHasImage(blocks)) {
    throw new LlmError('compaction summary cannot contain image output', 'UNSUPPORTED_CONTENT')
  }
  return (blocks || []).filter((block) => block && block.type === 'text'
    && typeof block.text === 'string')
}
