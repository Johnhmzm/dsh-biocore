// 三层检索：句子（search）→ 事件（list/read event_id）→ 全文（read entry_id）。
import assert from 'node:assert/strict'
import {
  EVENT_TEXT_LIMIT,
  LIST_TOOL_NAME,
  READ_TEXT_LIMIT,
  READ_TOOL_NAME,
  SENTENCE_LIMIT,
  TOOL_NAME,
  buildMemoryListSpec,
  buildMemoryReadSpec,
  buildMemoryToolSpec,
  clampLimit,
  clampOffset,
  registerMemoryTools,
} from '../lib/modules/memory-search-tool.js'

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }
const defineTool = (spec) => spec

try {
  // ---- search：句子级字段 / 同事件合并 / hits_in_event -------------------
  {
    const spec = buildMemoryToolSpec({
      defineTool,
      getStore: () => 'C:/store/memory',
      search: async () => ({ ok: true, results: [
        { date: '2026-11-12 21:31', role: 'assistant', sentence: 's'.repeat(400),
          event_summary: 'e'.repeat(80), entry_id: 'e1', event_id: 'EV1', hits_in_event: 3 },
        { date: '2026-11-12 21:30', role: 'user', sentence: '短句',
          event_summary: '事件二', entry_id: 'e2', event_id: '', hits_in_event: 1 },
      ] }),
    })
    const value = await spec.execute({ query: '解压日志用的什么脚本' },
      { agent: { session: { id: 'session-a' } } })
    assert.equal(value.count, 2)
    const first = value.results[0]
    assert.deepEqual(Object.keys(first).sort(),
      ['date', 'entry_id', 'event_id', 'event_summary', 'hits_in_event', 'role', 'sentence'])
    assert.equal(first.sentence.length, SENTENCE_LIMIT, '句子截到 200 字')
    assert.equal(first.event_summary.length, 40, '事件摘要截到 40 字')
    assert.equal(first.hits_in_event, 3)
    assert.equal(value.results[1].event_id, '', '没有事件时 event_id 为空')
    const schemaKeys = Object.keys(spec.output.schema.properties.results.items.properties).sort()
    assert.deepEqual(schemaKeys, ['date', 'entry_id', 'event_id', 'event_summary', 'hits_in_event', 'role', 'sentence'])
    ok('search：句子级六字段 + hits_in_event，句子 200 字、事件摘要 40 字')
  }

  // ---- list：按事件聚合（一行一件，去重口径在 python 侧） ---------------
  {
    const spec = buildMemoryListSpec({
      defineTool,
      list: async () => ({ ok: true, total: 2, items: [
        { id: 'EV1', date_range: ['2026-11-12 21:00', '2026-11-12 21:31'], entries: 15,
          summary: '解压日志', summary_source: 'event' },
        { id: 'sum-1', date_range: ['2026-11-12 21:40', '2026-11-12 21:40'], entries: 2,
          summary: '首句当总结', summary_source: 'first_sentence' },
      ] }),
    })
    const value = await spec.execute({ limit: 50 }, { agent: { session: { id: 's' } } })
    assert.equal(value.total, 2)
    assert.deepEqual(Object.keys(value.items[0]).sort(),
      ['date_range', 'entries', 'id', 'summary', 'summary_source'])
    assert.equal(value.items[0].entries, 15, '条目数要透传')
    assert.equal(value.items[1].summary_source, 'first_sentence', '无总结要标出来')
    const rendered = spec.output.render({}, value)[0].text
    assert.ok(rendered.includes('按事件'))
    ok('list：按事件一行一件，带日期范围与条目数，缺总结会标注')
  }

  // ---- read：event_id 读整件 / entry_id 读单条 -------------------------
  {
    const spec = buildMemoryReadSpec({
      defineTool,
      read: async (store, sessionId, payload) => {
        if (payload.event_id === 'EV1') {
          return { ok: true, id: 'EV1', date: '2026-11-12 21:00', role: 'event',
                   kind: 'event', level: 'event', entries: 15, text: 'x'.repeat(9000) }
        }
        if (payload.entry_id === 'e1') {
          return { ok: true, id: 'e1', date: '2026-11-12 21:31', role: 'assistant',
                   kind: 'entry', level: 'entry', text: 'y'.repeat(5000) }
        }
        return { ok: false, reason: 'not_found' }
      },
    })
    const event = await spec.execute({ event_id: 'EV1' }, { agent: { session: { id: 's' } } })
    assert.equal(event.level, 'event')
    assert.equal(event.text.length, EVENT_TEXT_LIMIT, '整件截到 4000 字')
    const entry = await spec.execute({ entry_id: 'e1' }, { agent: { session: { id: 's' } } })
    assert.equal(entry.level, 'entry')
    assert.equal(entry.text.length, READ_TEXT_LIMIT, '单条截到 2000 字')
    await assert.rejects(() => spec.execute({}, { agent: { session: { id: 's' } } }),
      /需要 event_id 或 entry_id/)
    await assert.rejects(() => spec.execute({ event_id: 'nope' }, { agent: { session: { id: 's' } } }),
      /not_found/)
    ok('read：event_id 读整件（截 4000）、entry_id 读单条（截 2000）、缺参报错')
  }

  // ---- 注册：三个一起挂 -------------------------------------------------
  {
    const registered = []
    const ctx = { on() { return () => {} },
      tools: { register(spec) { registered.push(spec.name); return () => {} } } }
    registerMemoryTools(ctx, () => '/s', { defineTool, search: async () => ({ results: [] }),
      list: async () => ({ total: 0, items: [] }), read: async () => ({ ok: false }) })
    assert.deepEqual(registered.sort(), [LIST_TOOL_NAME, READ_TOOL_NAME, TOOL_NAME].sort())
    assert.equal(clampLimit(0), 1)
    assert.equal(clampOffset(-1), 0)
    ok('注册：memory_search / memory_list / memory_read 一起挂上')
  }
} finally {
  // 无临时文件
}

console.log('\nall ' + passed + ' checks passed')
