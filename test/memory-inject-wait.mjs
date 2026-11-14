// 记忆注入时序：刷新发生在 system-prompt/assemble 里，TTL 内复用，超时/无提问都安全。
//
// 不碰宿主：注入假的 systemPrompt/ctx/会话与假刷新函数。实机抓到过两次"晚一轮"，
// 这里把三条约束钉死：① 过期才刷；② 同一句在 TTL 内零调用；③ 超时/无 query 不动回合。
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  CONTEXT_NAME,
  DEFAULT_INJECT_TTL_MS,
  DEFAULT_INJECT_WAIT_MS,
  applyCachedText,
  createInjectionState,
  injectSettings,
  latestUserQuery,
  queueRefresh,
  registerMemoryInjection,
} from '../lib/modules/memory-inject.js'

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/** 假会话：表面上是三条消息，最后一句是 s.question。 */
function fakeSession(sessionId, question, extra = {}) {
  const events = []
  const push = (type, data) => { events.push({ type, seq: events.length, time: 1777800000000 + events.length * 1000, data }) }
  if (question !== null) {
    push('user/message', { source: { kind: 'user' }, content: [{ type: 'text', text: '更早的一句' }] })
  }
  push('assistant/message', { content: [{ type: 'text', text: '好' }] })
  if (question !== undefined && question !== null) {
    push('user/message', { source: { kind: 'user' }, content: [{ type: 'text', text: question }] })
  }
  return Object.assign({
    id: sessionId,
    eventAt(seq) { return events[seq] },
    get seq() { return events.length },
    surface: { nodes: events.map((_event, index) => index) },
  }, extra)
}

/** 一次 assemble：返回改过的 assembly、next 次数与耗时。 */
async function runAssemble(hook, sessionId, question, text = '旧文本') {
  const assembly = { contexts: [{ name: CONTEXT_NAME, order: 9510, text }] }
  let nextCalls = 0
  const started = Date.now()
  await hook(assembly, { agent: { session: fakeSession(sessionId, question) } },
    async () => { nextCalls += 1 })
  return { assembly, nextCalls, elapsed: Date.now() - started, text: assembly.contexts[0].text }
}

try {
  // ---- ① 缓存过期：assemble 里刷新，注入的是新文本 ----------------------
  {
    const state = createInjectionState()
    state.set('s1', '旧文本')
    let calls = 0
    let seenQuery = ''
    const hooks = []
    const ctx = {
      inject: (names, cb) => cb({ systemPrompt: { context: () => () => {} } }),
      on: (name, handler, options) => { hooks.push({ name, handler, options }); return () => {} },
    }
    registerMemoryInjection(ctx, () => ({ memoryEnabled: true, injectWaitMs: 3000 }),
      tmpdir(), state, {
        refresh: (sessionId, query) => {
          calls += 1
          seenQuery = query
          return queueRefresh(state, sessionId, async () => {
            await sleep(60)
            return { ok: true, text: '新文本（3085 字那种）' }
          }, { query })
        },
      })
    const hook = hooks.find((item) => item.name === 'system-prompt/assemble')
    assert.ok(hook, '必须挂在 system-prompt/assemble')
    const out = await runAssemble(hook.handler, 's1', '我们最早聊了什么？按时间说三件')
    assert.equal(calls, 1, '过期就该刷一次')
    assert.equal(seenQuery, '我们最早聊了什么？按时间说三件', '用这一轮的提问召回')
    assert.equal(out.text, '新文本（3085 字那种）', '注入的是新文本')
    assert.equal(out.nextCalls, 1)
    assert.ok(out.elapsed >= 50, '刷新确实被等待（' + out.elapsed + 'ms）')
    ok('assemble 时缓存过期：当场刷新，同一轮就注入新文本')
  }

  // ---- ② TTL 内且同一句：零刷新调用 -----------------------------------
  {
    const state = createInjectionState()
    state.set('s2', '缓存文本')
    state.lastRefresh.set('s2', { at: Date.now(), query: '同一句' })
    let calls = 0
    const hooks = []
    const ctx = {
      inject: (names, cb) => cb({ systemPrompt: { context: () => () => {} } }),
      on: (name, handler, options) => { hooks.push({ name, handler, options }); return () => {} },
    }
    registerMemoryInjection(ctx, () => ({ memoryEnabled: true }), tmpdir(), state, {
      refresh: () => { calls += 1; return Promise.resolve('不该调用') },
    })
    const hook = hooks.find((item) => item.name === 'system-prompt/assemble')
    const out = await runAssemble(hook.handler, 's2', '同一句')
    assert.equal(calls, 0, 'TTL 内不该刷新')
    assert.equal(out.text, '缓存文本')
    assert.ok(out.elapsed < 40, '不该等（' + out.elapsed + 'ms）')
    ok('TTL 内且同一句提问：不刷新、不等待、直接用缓存')
  }

  // ---- ③ 刷新超时：旧文本 + 回合继续 ----------------------------------
  {
    const state = createInjectionState()
    state.set('s3', '旧文本')
    const hooks = []
    const ctx = {
      inject: (names, cb) => cb({ systemPrompt: { context: () => () => {} } }),
      on: (name, handler, options) => { hooks.push({ name, handler, options }); return () => {} },
    }
    registerMemoryInjection(ctx, () => ({ memoryEnabled: true, injectWaitMs: 300 }),
      tmpdir(), state, {
        refresh: () => new Promise(() => {}),      // 永不 resolve
        now: () => Date.now(),
      })
    const hook = hooks.find((item) => item.name === 'system-prompt/assemble')
    const started = Date.now()
    const out = await runAssemble(hook.handler, 's3', '新的一句')
    const elapsed = Date.now() - started
    assert.ok(elapsed >= 250 && elapsed < 2000,
      '按显式配置的 300ms 上限放行（实际 ' + elapsed + 'ms）')
    assert.equal(out.text, '旧文本', '超时用旧文本')
    assert.equal(out.nextCalls, 1, '回合继续')
    ok('刷新超时（显式 300ms）：放行、用旧文本、next 照常')
  }

  // ---- ④ 拿不到提问：不刷新、不报错 -----------------------------------
  {
    const state = createInjectionState()
    state.set('s4', '旧文本')
    let calls = 0
    const hooks = []
    const ctx = {
      inject: (names, cb) => cb({ systemPrompt: { context: () => () => {} } }),
      on: (name, handler, options) => { hooks.push({ name, handler, options }); return () => {} },
    }
    registerMemoryInjection(ctx, () => ({ memoryEnabled: true }), tmpdir(), state, {
      refresh: () => { calls += 1; return Promise.resolve('不该调用') },
    })
    const hook = hooks.find((item) => item.name === 'system-prompt/assemble')
    const out = await runAssemble(hook.handler, 's4', null)   // 会话里一条 user/message 都没有
    assert.equal(calls, 0, '没有提问就不刷新')
    assert.equal(out.text, '旧文本')
    assert.equal(out.nextCalls, 1)
    assert.equal(latestUserQuery(fakeSession('s4', null)), '', '没有真人消息就是空')
    assert.equal(latestUserQuery(fakeSession('s4', '这一句')), '这一句', '有就用最近一条')
    ok('拿不到这一轮提问：不刷新、不报错、回合继续')
  }

  // ---- ⑤ 预热监听仍在，并记下 lastRefresh ------------------------------
  {
    const state = createInjectionState()
    let calls = 0
    const hooks = []
    const ctx = {
      inject: (names, cb) => cb({ systemPrompt: { context: () => () => {} } }),
      on: (name, handler, options) => { hooks.push({ name, handler, options }); return () => {} },
    }
    registerMemoryInjection(ctx, () => ({ memoryEnabled: true }), tmpdir(), state, {
      refresh: (sessionId, query) => {
        calls += 1
        return queueRefresh(state, sessionId, async () => ({ ok: true, text: '预热文本' }), { query })
      },
    })
    const warm = hooks.find((item) => item.name === 'session/event')
    assert.ok(warm, '预热监听保留')
    warm.handler({ id: 's5' }, { type: 'user/message',
      data: { source: { kind: 'user' }, content: [{ type: 'text', text: '预热这一句' }] } })
    await sleep(20)
    assert.equal(calls, 1)
    assert.equal(state.get('s5'), '预热文本')
    assert.equal(state.lastRefresh.get('s5').query, '预热这一句')
    // 紧接着的一次 assemble：同一句、TTL 内 -> 零刷新
    const hook = hooks.find((item) => item.name === 'system-prompt:never') ||
      hooks.find((item) => item.name === 'system-prompt/assemble')
    const out = await runAssemble(hook.handler, 's5', '预热这一句')
    assert.equal(calls, 1, '预热之后本轮不再刷')
    assert.equal(out.text, '预热文本')
    ok('user/message 降级为预热：先刷一次，assemble 里 TTL 命中零调用')
  }

  // ---- ⑥ 开关关掉：不刷新 ---------------------------------------------
  {
    const state = createInjectionState()
    state.set('s6', '旧文本')
    let calls = 0
    const hooks = []
    const ctx = {
      inject: (names, cb) => cb({ systemPrompt: { context: () => () => {} } }),
      on: (name, handler, options) => { hooks.push({ name, handler, options }); return () => {} },
    }
    registerMemoryInjection(ctx, () => ({ memoryEnabled: false }), tmpdir(), state, {
      refresh: () => { calls += 1; return Promise.resolve('x') },
    })
    const hook = hooks.find((item) => item.name === 'system-prompt/assemble')
    const out = await runAssemble(hook.handler, 's6', '一句提问')
    assert.equal(calls, 0)
    assert.equal(out.text, '旧文本')
    ok('memoryEnabled=false：不刷新、原样放行')
  }

  // ---- ⑧ 默认配置：先等召回，不是 0 毫秒放行 --------------------------
  {
    const state = createInjectionState()
    state.set('s8', '旧文本')
    const hooks = []
    const ctx = {
      inject: (names, cb) => cb({ systemPrompt: { context: () => () => {} } }),
      on: (name, handler, options) => { hooks.push({ name, handler, options }); return () => {} },
    }
    // 不给 injectWaitMs / 不写 core_config -> 用缺省 10000ms，真的会等
    registerMemoryInjection(ctx, () => ({ memoryEnabled: true }), tmpdir(), state, {
      refresh: (sessionId, query) => queueRefresh(state, sessionId, async () => {
        await sleep(120)
        return { ok: true, text: '缺省也会等到的文本' }
      }, { query }),
    })
    const hook = hooks.find((item) => item.name === 'system-prompt/assemble')
    const out = await runAssemble(hook.handler, 's8', '一句新提问')
    assert.ok(out.elapsed >= 110, '缺省先等召回（实际 ' + out.elapsed + 'ms）')
    assert.equal(out.text, '缺省也会等到的文本')
    assert.equal(DEFAULT_INJECT_WAIT_MS, 10000)
    ok('默认配置：先等召回结果再让模型回复（不是 0 毫秒放行）')
  }

  // ---- ⑨ TTL 内但提问变了：必须重新召回 -------------------------------
  {
    const state = createInjectionState()
    state.set('s9', '第一句的缓存')
    state.lastRefresh.set('s9', { at: Date.now(), query: '第一句' })
    let calls = 0
    const hooks = []
    const ctx = {
      inject: (names, cb) => cb({ systemPrompt: { context: () => () => {} } }),
      on: (name, handler, options) => { hooks.push({ name, handler, options }); return () => {} },
    }
    registerMemoryInjection(ctx, () => ({ memoryEnabled: true }), tmpdir(), state, {
      refresh: (sessionId, query) => {
        calls += 1
        return queueRefresh(state, sessionId, async () => ({ ok: true, text: '第二句的新召回' }), { query })
      },
    })
    const hook = hooks.find((item) => item.name === 'system-prompt/assemble')
    const out = await runAssemble(hook.handler, 's9', '第二句')
    assert.equal(calls, 1, '提问变了就必须重新召回（TTL 不救）')
    assert.equal(out.text, '第二句的新召回')
    // 同一句再来一次：这次 TTL 命中，不再调
    await runAssemble(hook.handler, 's9', '第二句')
    assert.equal(calls, 1)
    ok('TTL 只对同一句生效：提问一换就重新召回并等待')
  }

  // ---- ⑩ inject_wait_ms=0：永不超时（一直等到召回返回） ---------------
  {
    const make = () => {
      const state = createInjectionState()
      state.set('s10', '旧文本')
      const hooks = []
      const ctx = {
        inject: (names, cb) => cb({ systemPrompt: { context: () => () => {} } }),
        on: (name, handler, options) => { hooks.push({ name, handler, options }); return () => {} },
      }
      return { state, hooks, ctx }
    }
    // 有限上限（30ms）配一个 150ms 才返回的召回：会超时 -> 旧文本
    {
      const { state, hooks, ctx } = make()
      registerMemoryInjection(ctx, () => ({ memoryEnabled: true, injectWaitMs: 30 }),
        tmpdir(), state, {
          refresh: (sessionId, query) => queueRefresh(state, sessionId, async () => {
            await sleep(150)
            return { ok: true, text: '迟到的召回' }
          }, { query }),
        })
      const out = await runAssemble(hooks.find((h) => h.name === 'system-prompt/assemble').handler,
        's10', '这句')
      assert.equal(out.text, '旧文本', '有限上限时超时就用旧文本')
    }
    // 0 = 不限时：同一个 150ms 的召回会等到，并且用上新文本
    {
      const { state, hooks, ctx } = make()
      registerMemoryInjection(ctx, () => ({ memoryEnabled: true, injectWaitMs: 0 }),
        tmpdir(), state, {
          refresh: (sessionId, query) => queueRefresh(state, sessionId, async () => {
            await sleep(150)
            return { ok: true, text: '等到的新召回' }
          }, { query }),
        })
      const started = Date.now()
      const out = await runAssemble(hooks.find((h) => h.name === 'system-prompt/assemble').handler,
        's10', '这句')
      assert.ok(Date.now() - started >= 140, '0 = 一直等到召回返回')
      assert.equal(out.text, '等到的新召回')
    }
    ok('inject_wait_ms=0：永不超时，等到召回返回才继续')
  }

  // ---- ⑦ 设置与文本落位 ------------------------------------------------
  {
    const dir = mkdtempSync(join(tmpdir(), 'biocore-inj-'))
    try {
      const store = join(dir, 'memory')
      mkdirSync(store, { recursive: true })
      writeFileSync(join(dir, 'core_config.json'),
        JSON.stringify({ recall: { inject_wait_ms: 800, inject_ttl_ms: 40000 } }))
      const got = injectSettings(store, new Map())
      assert.equal(got.waitMs, 800)
      assert.equal(got.ttlMs, 40000)
      const bare = mkdtempSync(join(tmpdir(), 'biocore-inj-bare-'))
      try {
        const def = injectSettings(join(bare, 'memory'), new Map())
        assert.equal(def.waitMs, DEFAULT_INJECT_WAIT_MS)
        assert.equal(def.ttlMs, DEFAULT_INJECT_TTL_MS)
      } finally { rmSync(bare, { recursive: true, force: true }) }
      const state = createInjectionState()
      state.set('s7', '记忆块')
      const assembly = { contexts: [{ name: 'other', order: 9000, text: 'x' },
        { name: 'later', order: 9900, text: 'y' }] }
      applyCachedText(assembly, state, 's7')
      assert.deepEqual(assembly.contexts.map((item) => item.name), ['other', CONTEXT_NAME, 'later'])
      assert.equal(assembly.contexts[1].text, '记忆块')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
    ok('inject_wait_ms / inject_ttl_ms 读配置；assembly 没这条 context 时按 order 插入')
  }
} finally {
  // 无临时文件
}

console.log('\nall ' + passed + ' checks passed')
