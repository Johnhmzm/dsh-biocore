// 工具层不暴露 include_context；边界外为空时给一句明确话术。
import assert from 'node:assert/strict'
import {
  PARAMETERS,
  TOP_K_DEFAULT,
  LIST_PARAMETERS,
  buildMemoryListSpec,
  buildMemoryReadSpec,
  buildMemoryToolSpec,
} from '../lib/modules/memory-search-tool.js'

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }
const defineTool = (spec) => spec

try {
  // ---- schema 里没有 include_context；默认 top_k=8 ----------------------
  {
    const search = buildMemoryToolSpec({ defineTool, search: async () => ({ results: [] }) })
    const list = buildMemoryListSpec({ defineTool, list: async () => ({ items: [] }) })
    assert.equal('include_context' in search.parameters, false, 'search 不再暴露 include_context')
    assert.equal('include_context' in list.parameters, false, 'list 不再暴露 include_context')
    assert.equal('include_context' in PARAMETERS, false)
    assert.equal('include_context' in LIST_PARAMETERS, false)
    assert.equal(TOP_K_DEFAULT, 8, '默认 top_k = 8')
    ok('schema：没有 include_context；top_k 缺省 8')
  }

  // ---- 模型硬塞 include_context=true 也会被忽略 ------------------------
  {
    let seen = null
    const search = buildMemoryToolSpec({
      defineTool,
      search: async (store, sessionId, payload) => { seen = payload; return { results: [] } },
    })
    await search.execute({ query: 'x', include_context: true },
      { agent: { session: { id: 's' } } })
    assert.equal(seen.include_context, false, '传了也按边界过滤')

    let listSeen = null
    const list = buildMemoryListSpec({
      defineTool,
      list: async (store, sessionId, payload) => { listSeen = payload; return { items: [] } },
    })
    await list.execute({ include_context: true }, { agent: { session: { id: 's' } } })
    assert.equal(listSeen.include_context, undefined, 'list 侧干脆不往下传')
    ok('模型传 include_context 会被忽略')
  }

  // ---- 边界外为空时的渲染话术 -----------------------------------------
  {
    const search = buildMemoryToolSpec({
      defineTool,
      search: async () => ({ results: [], reason: 'nothing_beyond_context' }),
    })
    const value = await search.execute({ query: 'x' }, { agent: { session: { id: 's' } } })
    assert.equal(value.reason, 'nothing_beyond_context')
    const rendered = search.output.render({}, value)[0].text
    assert.equal(rendered, '这条会话没有更早的内容了（都还在当前上下文里）。')

    const list = buildMemoryListSpec({
      defineTool,
      list: async () => ({ items: [], total: 0, reason: 'nothing_beyond_context' }),
    })
    const listValue = await list.execute({}, { agent: { session: { id: 's' } } })
    assert.equal(list.output.render({}, listValue)[0].text,
      '这条会话没有更早的内容了（都还在当前上下文里）。')
    ok('空结果渲染成"没有更早的内容了"，不是空列表')
  }

  // ---- 普通空结果仍是"没找到" -----------------------------------------
  {
    const search = buildMemoryToolSpec({ defineTool, search: async () => ({ results: [] }) })
    const value = await search.execute({ query: 'x' }, { agent: { session: { id: 's' } } })
    assert.equal(search.output.render({}, value)[0].text, '记忆库里没有找到相关内容。')
    const read = buildMemoryReadSpec({ defineTool, read: async () => ({ ok: false }) })
    assert.equal(typeof read.execute, 'function')
    ok('无 reason 的空结果仍是"没找到"；read 工具照旧')
  }
} finally {
  // 无临时文件
}

console.log('\nall ' + passed + ' checks passed')
