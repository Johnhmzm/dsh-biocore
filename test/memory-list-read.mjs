// memory_list / memory_read：目录 + 取原文（三个工具共用一条注册路径）。
//
// 不碰宿主：注入 defineTool/ctx/list/read；宿主包只在真实运行里出现。
import assert from 'node:assert/strict'
import {
  LIST_TOOL_NAME,
  READ_TOOL_NAME,
  READ_TEXT_LIMIT,
  TOOL_NAME,
  buildMemoryListSpec,
  buildMemoryReadSpec,
  clampLimit,
  clampOffset,
  registerMemoryTools,
} from '../lib/modules/memory-search-tool.js'

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }
const defineTool = (spec) => spec

try {
  // ---- 描述：先目录再读原文，别翻日志，别一次读太多 --------------------
  {
    const list = buildMemoryListSpec({ defineTool, list: async () => ({ total: 0, items: [] }) })
    const read = buildMemoryReadSpec({ defineTool, read: async () => ({ ok: false }) })
    assert.ok(list.description.includes('先调它'), 'list 描述要说先看目录')
    assert.ok(list.description.includes('memory_read'), 'list 描述要指到 memory_read')
    assert.ok(list.description.includes('不要用关键词'), 'list 描述禁止关键词翻日志')
    assert.ok(read.description.includes('先 memory_list'), 'read 描述要指回 list')
    assert.ok(read.description.includes('不要一次读超过 5 条'), 'read 描述限读 5 条')
    assert.ok(read.parameters.id.required === true, 'read 的 id 必填')
    ok('两个工具的描述：先 list 再 read、禁止关键词翻日志、一次不超过 5 条')
  }

  // ---- list：结构 / 摘要 / 分页 / 空库 --------------------------------
  {
    const rows = [
      { id: 'e3', date: '2026-11-14 18:00', role: 'user', kind: 'entry', summary: 'y'.repeat(60) },
      { id: 'e2', date: '2026-11-13 10:00', role: 'assistant', kind: 'entry', summary: '第二条' },
      { id: 'c1', date: '2026-11-12 21:00', role: 'compaction', kind: 'compaction', summary: '【压缩目录】' },
    ]
    const seen = []
    const spec = buildMemoryListSpec({
      defineTool,
      getStore: () => 'C:/store/memory',
      list: async (store, sessionId, payload) => {
        seen.push({ store, sessionId, payload })
        const start = payload.offset || 0
        const size = payload.limit || 50
        return { ok: true, total: rows.length, items: rows.slice(start, start + size) }
      },
    })
    const value = await spec.execute({ limit: 2, offset: 0 },
      { agent: { session: { id: 'session-a' } } })
    assert.equal(value.total, 3)
    assert.equal(value.items.length, 2, '分页生效')
    assert.equal(value.items[0].summary.length, 40, '摘要截到 40 字')
    assert.deepEqual(Object.keys(value.items[0]).sort(), ['date', 'id', 'kind', 'role', 'summary'])
    assert.equal(seen[0].payload.limit, 2)
    assert.equal(seen[0].payload.offset, 0)
    assert.equal(seen[0].store, 'C:/store/memory')
    // 第二页
    const page2 = await spec.execute({ limit: 2, offset: 2 },
      { agent: { session: { id: 'session-a' } } })
    assert.deepEqual(page2.items.map((row) => row.id), ['c1'])
    // 空库
    const empty = buildMemoryListSpec({ defineTool, list: async () => ({ ok: true, total: 0, items: [] }) })
    const none = await empty.execute({}, { agent: { session: { id: 's' } } })
    assert.equal(none.total, 0)
    assert.deepEqual(none.items, [])
    assert.equal(clampLimit(0), 1)
    assert.equal(clampLimit(999), 100)
    assert.equal(clampLimit('x'), 50)
    assert.equal(clampOffset(-3), 0)
    ok('list：结构四字段+id、摘要 40 字、分页与空库、参数夹紧')
  }

  // ---- read：命中 / 找不到 / 截断 -------------------------------------
  {
    const spec = buildMemoryReadSpec({
      defineTool,
      read: async (store, sessionId, payload) => {
        if (payload.id === 'e1') {
          return { ok: true, id: 'e1', date: '2026-11-12 21:30', role: 'assistant',
                   kind: 'entry', text: 'z'.repeat(5000) }
        }
        return { ok: false, reason: 'not_found' }
      },
    })
    const hit = await spec.execute({ id: 'e1' }, { agent: { session: { id: 's' } } })
    assert.equal(hit.id, 'e1')
    assert.equal(hit.text.length, READ_TEXT_LIMIT, '正文截到 2000 字')
    assert.equal(hit.kind, 'entry')
    await assert.rejects(() => spec.execute({ id: 'nope' }, { agent: { session: { id: 's' } } }),
      /not_found/, '找不到就报 not_found')
    await assert.rejects(() => spec.execute({}, { agent: { session: { id: 's' } } }),
      /id 必填/)
    ok('read：命中返回正文（截 2000 字）、找不到报 not_found、缺 id 报错')
  }

  // ---- 注册：三个工具一次挂上，只挂一次 --------------------------------
  {
    const registered = []
    const ctx = {
      on() { return () => {} },
      tools: { register(spec) { registered.push(spec.name); return () => {} } },
    }
    registerMemoryTools(ctx, () => '/s', {
      defineTool,
      search: async () => ({ results: [] }),
      list: async () => ({ total: 0, items: [] }),
      read: async () => ({ ok: false }),
    })
    assert.deepEqual(registered.sort(), [LIST_TOOL_NAME, READ_TOOL_NAME, TOOL_NAME].sort())
    // 再调一次注册函数（换 ctx）也不重复
    const again = registerMemoryTools(ctx, () => '/s', { defineTool })
    assert.equal(registered.length, 3)
    assert.equal(typeof again, 'function')
    ok('注册：memory_search / memory_list / memory_read 一次挂全')
  }
} finally {
  // 无临时文件
}

console.log('\nall ' + passed + ' checks passed')
