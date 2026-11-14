// memory_search 工具：注册、schema、执行、截断、排序、空库与 scope 过滤。
//
// 不碰宿主：注入 defineTool/ctx/search，宿主包只在真实运行里出现。
import assert from 'node:assert/strict'
import {
  PARAMETERS,
  TEXT_LIMIT,
  TOOL_DESCRIPTION,
  TOOL_NAME,
  buildMemoryToolSpec,
  clampTopK,
  normalizeScope,
  registerMemorySearchTool,
  shapeResult,
} from '../lib/modules/memory-search-tool.js'

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }

function fakeDefineTool(spec) { return spec }

const LONG = 'x'.repeat(1200)

try {
  // ---- 描述：三件事都在 ------------------------------------------------
  {
    assert.ok(TOOL_DESCRIPTION.includes('回忆过去'), 'a) 用途')
    assert.ok(TOOL_DESCRIPTION.includes('先调用本工具'), 'b) 优先用它')
    assert.ok(TOOL_DESCRIPTION.includes('不要用关键词或字符匹配'), 'b) 明确禁止关键词翻历史')
    assert.ok(TOOL_DESCRIPTION.includes('原文片段与压缩目录'), 'c) 返回什么')
    assert.ok(TOOL_DESCRIPTION.includes('按日期排序'), 'c) 排序')
    ok('描述写清用途 / 优先用它 / 返回原文与压缩目录并按日期排序')
  }

  // ---- schema ----------------------------------------------------------
  {
    const spec = buildMemoryToolSpec({ defineTool: fakeDefineTool, search: async () => ({ results: [] }) })
    assert.equal(spec.name, TOOL_NAME)
    assert.equal(spec.parameters.query.required, true)
    assert.equal(spec.parameters.query.type, 'string')
    assert.equal(spec.parameters.top_k.type, 'integer')
    assert.equal(spec.parameters.scope.type, 'string')
    assert.deepEqual(spec.parameters.scope.enum, ['all', 'text', 'compaction'])
    assert.equal(spec.output.schema.properties.results.items.properties.text.type, 'string')
    assert.equal(clampTopK(0), 1, 'top_k 下界')
    assert.equal(clampTopK(99), 20, 'top_k 上界')
    assert.equal(clampTopK('abc'), 8, '非数字 -> 缺省')
    assert.equal(clampTopK(5), 5)
    assert.equal(normalizeScope('COMPACTION'), 'compaction')
    assert.equal(normalizeScope('nope'), 'all')
    ok('schema：query 必填、top_k 夹紧 1–20、scope 枚举；输出结构固定')
  }

  // ---- 执行：结构 / 截断 / 排序 ---------------------------------------
  {
    let seen = null
    const spec = buildMemoryToolSpec({
      defineTool: fakeDefineTool,
      getStore: () => 'C:/store/memory',
      search: async (store, sessionId, payload) => {
        seen = { store, sessionId, payload }
        return { ok: true, results: [
          { date: '2026-11-12 21:00', role: 'user', text: LONG, kind: 'entry' },
          { date: '2026-11-12 22:10', role: 'compaction', text: '【压缩目录】覆盖 …', kind: 'compaction' },
        ] }
      },
    })
    const value = await spec.execute({ query: '我们最开始是为了什么开始改记忆的', top_k: 9,
      scope: 'all' }, { agent: { session: { id: 'session-a' } } })
    assert.equal(seen.store, 'C:/store/memory')
    assert.equal(seen.sessionId, 'session-a')
    assert.equal(seen.payload.query, '我们最开始是为了什么开始改记忆的')
    assert.equal(seen.payload.top_k, 9)
    assert.equal(value.count, 2)
    assert.equal(value.results[0].text.length, TEXT_LIMIT, '截断到 400 字')
    assert.equal(value.results[1].kind, 'compaction')
    assert.deepEqual(Object.keys(value.results[0]).sort(), ['date', 'kind', 'role', 'text'])
    const rendered = spec.output.render({}, value)[0].text
    assert.ok(rendered.includes('记忆检索结果'))
    ok('执行：参数透传、结构四字段、正文截断 400 字')
  }

  // ---- 空库 / 无 query / 无 session ------------------------------------
  {
    const spec = buildMemoryToolSpec({ defineTool: fakeDefineTool, getStore: () => '/s',
      search: async () => ({ ok: true, results: [] }) })
    const value = await spec.execute({ query: '随便问问' }, { agent: { session: { id: 's' } } })
    assert.equal(value.count, 0, '空库返回空列表且不报错')
    assert.deepEqual(value.results, [])
    await assert.rejects(() => spec.execute({}, { agent: { session: { id: 's' } } }),
      /query 必填/)
    await assert.rejects(() => spec.execute({ query: 'x' }, {}), /no owning session/)
    ok('空库 ok、缺 query/缺会话都显式报错')
  }

  // ---- 注册 ------------------------------------------------------------
  {
    const registered = []
    const ctx = {
      on() { return () => {} },
      inject(names, cb) {
        assert.deepEqual(names, ['tools'])
        cb({ tools: { register(spec) { registered.push(spec); return () => {} } } })
      },
    }
    const dispose = registerMemorySearchTool(ctx, () => '/s',
      { defineTool: fakeDefineTool, search: async () => ({ results: [] }) })
    assert.equal(registered.length, 1)
    assert.equal(registered[0].name, 'memory_search')
    assert.equal(typeof dispose, 'function')
    // 没有 tools 服务：安静返回 null
    assert.equal(registerMemorySearchTool({ on() {} }, () => '/s', { defineTool: fakeDefineTool }), null)
    ok('注册：走 ctx.inject(tools) 挂上；没有 tools 服务时安静跳过')
  }
} finally {
  // 无临时文件
}

console.log('\nall ' + passed + ' checks passed')
