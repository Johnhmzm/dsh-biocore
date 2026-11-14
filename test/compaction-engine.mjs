// 压缩子类：只覆盖 summarize()，其余全是官方的。
//
// 不碰宿主：注入假的 BasicCompactionEngine / BlockAssembler / LlmError，
// 证明三件事——① 类里只有 summarize 一个自有方法；② 成功路径的提示词与返回字段；
// ③ 模型失败时退到库内目录（0 调用），两边都失败才抛。
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  DIRECTORY_MAX_CHARS,
  FALLBACK_MODEL,
  FALLBACK_PROVIDER,
  clockOf,
  createCompactionEngineClass,
  frameDirectory,
  summarizationInstruction,
  surfaceSpan,
} from '../lib/modules/compaction-engine.js'

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }

// ---- 假宿主 --------------------------------------------------------------
class FakeLlmError extends Error {
  constructor(message, code, options) { super(message, options); this.code = code }
}
class FakeAssembler {
  constructor() { this.parts = []; this.usage = undefined; this.finish = { kind: 'stop' } }
  push(chunk) {
    if (!chunk) return
    if (typeof chunk.text === 'string') this.parts.push(chunk.text)
    if (chunk.usage !== undefined) this.usage = chunk.usage
    if (chunk.finish !== undefined) this.finish = chunk.finish
  }
  blocks() { return [{ type: 'text', text: this.parts.join('') }] }
}
class FakeBasicEngine {
  constructor(ctx) { this.ctx = ctx; this.config = {} }
  compactIfNeeded() { return 'official-compactIfNeeded' }
  compactNow() { return 'official-compactNow' }
  compactRegion() { return 'official-compactRegion' }
  _registerAutomaticCompaction() { return 'official-register' }
}
const seam = {
  BasicCompactionEngine: FakeBasicEngine,
  BlockAssembler: FakeAssembler,
  LlmError: FakeLlmError,
  contentHasImage: () => false,
}

/** 一段有时间的表面：3 条消息。 */
function makeSession(overrides = {}) {
  const events = []
  const base = new Date(2026, 10, 14, 16, 20, 0).getTime()
  const push = (type, time, extra = {}) => {
    const event = Object.assign({ type, seq: events.length, time }, extra)
    events.push(event)
    return event
  }
  push('user/message', base)
  push('assistant/message', base + 60_000)
  push('user/message', base + 120_000)
  return Object.assign({
    id: 'session-t',
    events,
    surface: { nodes: [0, 1, 2] },
    eventAt(seq) { return this.events[seq] },
    requestHeader() { return { config: { provider: 'deepseek-account', model: 'deepseek-flash' } } },
    toolHistory() { return [] },
  }, overrides)
}

function makeCtx(stream, streamOptions = {}) {
  return {
    llm: { stream },
    logger: { info() {}, warn() {} },
    ...streamOptions,
  }
}

function makeAgent(session) {
  return { session, options: {} }
}

function engineOf(ctx, deps = {}) {
  const Engine = createCompactionEngineClass(Object.assign({}, seam, deps))
  return new Engine(ctx)
}

const span = { start: new Date(2026, 10, 14, 16, 20, 0).getTime(),
  end: new Date(2026, 10, 14, 16, 22, 0).getTime(), messages: 3,
  roles: { user: 2, assistant: 1 } }

try {
  // ---- 纯函数 ------------------------------------------------------------
  {
    assert.equal(clockOf(span.start), '16:20')
    assert.equal(clockOf(null), '--:--')
    const session = makeSession()
    const measured = surfaceSpan(session)
    assert.equal(measured.messages, 3)
    assert.deepEqual(measured.roles, { user: 2, assistant: 1 })
    assert.equal(clockOf(measured.start), '16:20')
    const instruction = summarizationInstruction(measured)
    assert.ok(instruction.includes('【压缩目录】覆盖 HH:MM–HH:MM'))
    assert.ok(instruction.includes('每行不超过 40 个字'))
    assert.ok(instruction.includes('20%'))
    assert.ok(instruction.startsWith('本次要压缩的区间：16:20–16:22，共 3 条消息'))
    const framed = frameDirectory('先是一句结论\n- 16:21 发生了某事', span)
    assert.ok(framed.startsWith('【压缩目录】覆盖 16:20–16:22，共 3 条消息'), '模型没写头就补一个')
    const long = Array.from({ length: 200 }, (_, i) => '- 16:21 第 ' + i + ' 条目录').join('\n')
    const capped = frameDirectory(long, span, 300)
    assert.ok(capped.length <= 320)
    assert.ok(capped.endsWith('（目录过长，已按上限截断）'))
    assert.ok(DIRECTORY_MAX_CHARS > 0)
    ok('clockOf / surfaceSpan / 提示词 / frameDirectory 的边界')
  }

  // ---- 自有方法只有 summarize + 阈值刷新入口 -----------------------------
  {
    const Engine = createCompactionEngineClass(seam)
    const own = Object.getOwnPropertyNames(Engine.prototype).sort()
    assert.deepEqual(own, ['applyThresholdSettings', 'compactIfNeeded', 'constructor', 'summarize'],
      '只允许这三个自有方法（外加 constructor）')
    for (const name of ['compactNow', 'compactRegion', '_registerAutomaticCompaction']) {
      assert.equal(Engine.prototype[name], FakeBasicEngine.prototype[name],
        name + ' 必须原封不动继承官方实现')
    }
    ok('只覆盖 summarize() 与阈值刷新：锁/区间/溢出恢复仍继承官方')
  }

  // ---- 阈值可配置：改文件下一次检查就生效 ---------------------------------
  {
    const dir = mkdtempSync(join(tmpdir(), 'biocore-threshold-'))
    try {
      const store = join(dir, 'memory')
      mkdirSync(store, { recursive: true })
      const configPath = join(dir, 'core_config.json')
      const write = (recall) => writeFileSync(configPath, JSON.stringify({ recall }))
      const Engine = createCompactionEngineClass(Object.assign({}, seam, { store }))
      const ctx = makeCtx(async function* () {})
      ctx.llm.resolveModelInfo = async () => ({ context: { contextWindow: 1000000 } })
      const engine = new Engine(ctx)
      engine.baseConfig = { thresholdRatio: 0.8, headroomTokens: 65536, retainRatio: 0.16,
        compactionRetries: 1, maxOverflowRetries: 1, auto: true }
      engine.config = { ...engine.baseConfig }
      const agent = makeAgent(makeSession())

      write({ compaction_threshold_tokens: 60000, compaction_context_window: 1000000 })
      assert.equal(await engine.compactIfNeeded(agent, 'pressure', undefined), 'official-compactIfNeeded')
      assert.ok(Math.abs(engine.config.thresholdRatio - 0.06) < 1e-9, '绝对阈值换算成 ratio=60000/1M')
      assert.equal(engine.config.headroomTokens, 0)
      assert.equal(engine.config.retainTokens, 12000, '保留量 = 阈值 20%')

      // 改文件不需要重启：下一次检查就用新值
      write({ compaction_threshold_ratio: 0.5 })
      await engine.compactIfNeeded(agent, 'pressure', undefined)
      assert.equal(engine.config.thresholdRatio, 0.5)
      assert.equal(engine.config.headroomTokens, 65536, '比例模式用回 YAML 的 headroom')

      // 绝对阈值优先于比例
      write({ compaction_threshold_tokens: 60000, compaction_threshold_ratio: 0.02,
        compaction_context_window: 1000000 })
      await engine.compactIfNeeded(agent, 'pressure', undefined)
      assert.ok(Math.abs(engine.config.thresholdRatio - 0.06) < 1e-9, '绝对阈值优先')

      // 两个都 0 -> 恢复 YAML 原值（与官方一致）
      write({})
      await engine.compactIfNeeded(agent, 'pressure', undefined)
      assert.deepEqual(engine.config, engine.baseConfig)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
    ok('阈值可配置：改文件立即生效、绝对优先、0/0 回落到 YAML')
  }

  // ---- 窗口兜底 ----------------------------------------------------------
  {
    const dir = mkdtempSync(join(tmpdir(), 'biocore-window-'))
    try {
      const store = join(dir, 'memory')
      mkdirSync(store, { recursive: true })
      writeFileSync(join(dir, 'core_config.json'), JSON.stringify({ recall: {
        compaction_threshold_ratio: 0.5, compaction_context_window: 100000 } }))
      const Engine = createCompactionEngineClass(Object.assign({}, seam, { store }))
      let sawWindow = 0
      const ctx = makeCtx(async function* () {})
      const original = async () => ({ context: undefined })
      ctx.llm.resolveModelInfo = async (provider, model, signal) => {
        sawWindow += 1
        return original(provider, model, signal)
      }
      const engine = new Engine(ctx)
      engine.baseConfig = { thresholdRatio: 0.8, headroomTokens: 65536, retainRatio: 0.16 }
      engine.config = { ...engine.baseConfig }
      const agent = makeAgent(makeSession())
      // super 里会调 resolveModelInfo；窗口由我们补
      FakeBasicEngine.prototype.compactIfNeeded = async function () {
        const info = await this.ctx.llm.resolveModelInfo('p', 'm')
        return info.context && info.context.contextWindow === 100000 ? 'window-ok' : 'window-missing'
      }
      try {
        assert.equal(await engine.compactIfNeeded(agent, 'pressure', undefined), 'window-ok')
        assert.equal(engine.config.thresholdRatio, 0.5)
        assert.equal(typeof ctx.llm.resolveModelInfo, 'function', '调用结束要还原')
      } finally {
        FakeBasicEngine.prototype.compactIfNeeded = function () { return 'official-compactIfNeeded' }
      }
      // 溢出恢复不看阈值，也不打窗口补丁
      let patchedDuringOverflow = null
      const before = ctx.llm.resolveModelInfo
      FakeBasicEngine.prototype.compactIfNeeded = async function () {
        patchedDuringOverflow = this.ctx.llm.resolveModelInfo !== before
        return 'overflow-path'
      }
      try {
        assert.equal(await engine.compactIfNeeded(agent, 'context-overflow', undefined), 'overflow-path')
        assert.equal(patchedDuringOverflow, false, '溢出路径不该被阈值逻辑碰')
      } finally {
        FakeBasicEngine.prototype.compactIfNeeded = function () { return 'official-compactIfNeeded' }
      }
      assert.ok(sawWindow >= 1)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
    ok('窗口兜底：适配器没窗口时按配置窗口算比例；溢出恢复不受影响')
  }

  // ---- 成功路径：提示词 + 返回字段 ---------------------------------------
  {
    let seen = null
    const ctx = makeCtx(async function* (request) { seen = request.received = request; yield { text: '【压缩目录】覆盖 16:20–16:22，共 3 条消息\n- 16:20 讨论了压缩产物，结论是改成目录' } })
    const engine = engineOf(ctx)
    const result = await engine.summarize({ messages: [{ role: 'user', content: [{ type: 'text', text: '原文' }] }], tools: [] },
      makeAgent(makeSession()))
    const last = seen.messages[seen.messages.length - 1]
    const prompt = last.content[0].text
    assert.ok(prompt.includes('【压缩目录】'), '提示词里要有我们的目录格式')
    assert.ok(prompt.includes('只写原文里明确出现的事实'))
    assert.ok(prompt.includes('每行不超过 40 个字'))
    assert.ok(prompt.includes('总字数不超过被压内容的 20%'))
    assert.equal(seen.purpose, 'compaction')
    assert.equal(seen.provider, 'deepseek-account')
    assert.equal(seen.model, 'deepseek-flash')
    assert.equal(seen.sessionId, 'session-t')
    assert.deepEqual(result.summary.map((b) => b.type), ['text'])
    assert.ok(result.summary[0].text.startsWith('【压缩目录】覆盖 16:20–16:22，共 3 条消息'))
    assert.equal(result.llmStreamCall, true)
    assert.deepEqual(result.rawOutput, [{ type: 'text', text: '【压缩目录】覆盖 16:20–16:22，共 3 条消息\n- 16:20 讨论了压缩产物，结论是改成目录' }])
    assert.equal(result.provider, 'deepseek-account')
    assert.equal(result.model, 'deepseek-flash')
    assert.equal(result.maxTokens, undefined, 'config 没给 maxTokens 就不写这个字段')
    assert.ok(!('usage' in result), 'assembler 没给 usage 就不写这个字段')
    ok('summarize 成功：提示词含目录要求，返回 {summary,rawOutput,llmStreamCall:true,provider,model}')
  }

  // ---- 成功路径带 usage / maxTokens --------------------------------------
  {
    const ctx = makeCtx(async function* () {
      yield { text: '【压缩目录】覆盖 16:20–16:22，共 3 条消息'
        + '\n- 16:20 一条' }
      yield { usage: { inputTokens: 1200, outputTokens: 80 } }
    })
    const engine = engineOf(ctx)
    engine.config = { maxTokens: 4096 }
    const result = await engine.summarize({ messages: [] }, makeAgent(makeSession()))
    assert.equal(result.maxTokens, 4096)
    assert.deepEqual(result.usage, { inputTokens: 1200, outputTokens: 80 })
    ok('summarize 成功：maxTokens / usage 照官方填进返回对象')
  }

  // ---- 降级：模型抛错 -> 桥目录 -----------------------------------------
  {
    const calls = []
    const ctx = makeCtx(async function* () { throw new Error('provider 500') })
    const engine = engineOf(ctx, {
      directory: async (store, sessionId, payload) => {
        calls.push({ store, sessionId, payload })
        return { ok: true, text: '【压缩目录】覆盖 16:20–16:22，共 3 条消息\n- 16:21 库内已有的一条摘要' }
      },
    })
    const result = await engine.summarize({ messages: [] }, makeAgent(makeSession()))
    assert.equal(calls.length, 1)
    assert.equal(calls[0].sessionId, 'session-t')
    assert.equal(calls[0].payload.messages, 3)
    assert.equal(Math.round(calls[0].payload.start_ts * 1000), span.start)
    assert.equal(Math.round(calls[0].payload.end_ts * 1000), span.end)
    assert.equal(result.provider, FALLBACK_PROVIDER)
    assert.equal(result.model, FALLBACK_MODEL)
    assert.ok(!('llmStreamCall' in result), '降级不是 LLM 调用，不能带 llmStreamCall')
    assert.ok(result.summary[0].text.includes('库内已有的一条摘要'))
    ok('降级：模型失败 -> 库内目录，provider=biocore-index（无 llmStreamCall）')
  }

  // ---- 降级：空输出 / max-tokens 也算失败 -------------------------------
  {
    const empty = makeCtx(async function* () {})
    const engineA = engineOf(empty, { directory: async () => ({ ok: true, text: '【压缩目录】覆盖 16:20–16:22，共 3 条消息\n- 16:21 库内' }) })
    const a = await engineA.summarize({ messages: [] }, makeAgent(makeSession()))
    assert.equal(a.provider, FALLBACK_PROVIDER)
    const truncated = makeCtx(async function* () {
      yield { text: '【压缩目录】半截' }
      yield { finish: { kind: 'max-tokens' } }
    })
    const engineB = engineOf(truncated, { directory: async () => ({ ok: true, text: '【压缩目录】覆盖 16:20–16:22，共 3 条消息\n- 16:21 库内' }) })
    const b = await engineB.summarize({ messages: [] }, makeAgent(makeSession()))
    assert.equal(b.provider, FALLBACK_PROVIDER)
    ok('降级：空输出与 max-tokens 截断都退回库内目录')
  }

  // ---- 两边都失败 -> 抛原错误 -------------------------------------------
  {
    const ctx = makeCtx(async function* () { throw new Error('provider 500') })
    const engine = engineOf(ctx, { directory: async () => ({ ok: false, reason: 'no_store' }) })
    await assert.rejects(() => engine.summarize({ messages: [] }, makeAgent(makeSession())),
      /provider 500/)
    const engine2 = engineOf(ctx, { directory: async () => { throw new Error('bridge down') } })
    await assert.rejects(() => engine2.summarize({ messages: [] }, makeAgent(makeSession())),
      /provider 500/)
    ok('降级也失败：抛模型原本的错误（让官方错误路径处理）')
  }

  // ---- 取消不降级 --------------------------------------------------------
  {
    const controller = new AbortController()
    controller.abort()
    const ctx = makeCtx(async function* () { throw new Error('aborted') })
    let bridgeCalls = 0
    const engine = engineOf(ctx, { directory: async () => { bridgeCalls += 1; return { ok: true, text: 'x' } } })
    await assert.rejects(() => engine.summarize({ messages: [] }, makeAgent(makeSession()), controller.signal))
    assert.equal(bridgeCalls, 0, 'signal 已取消时不该再试降级')
    ok('signal 已取消：直接抛，不降级')
  }
} finally {
  // 无临时文件
}

console.log('\nall ' + passed + ' checks passed')
