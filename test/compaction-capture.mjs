// 官方压缩（compaction/summary）真的落库了：喂一条合成事件，再回查 memory.db。
//
// 只证明一件事：session/event -> capture 分支 -> py-bridge -> pipeline -> compactions 表。
// 正文逐字比对（换行与双空格都是原文），重复事件不新增行（唯一约束幂等）。
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { compactionPayload, compactionText, messageKind,
  registerMemoryCapture } from '../lib/modules/memory-capture.js'

const SUMMARY = '# 压缩摘要\n\n 她提到周五要去复查，语气比上次松。\n\n- 复查改到了周六上午。'
const SID = 'comp-sess-1'
const EVENT_TIME = 1794635481765

const DUMP_PY = [
  'import json, sqlite3, sys',
  'conn = sqlite3.connect(sys.argv[1])',
  'rows = conn.execute("SELECT session_id, compaction_id, range_start, range_end,"',
  '                    " shadowed_count, shadowed_tokens, provider, model, text,"',
  '                    " usage_json, window_end FROM compactions ORDER BY id").fetchall()',
  'conn.close()',
  'open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(rows, ensure_ascii=False))',
].join('\n')

function compactionEvent() {
  return {
    type: 'compaction/summary',
    seq: 134380,
    time: EVENT_TIME,
    data: {
      compactionId: 'ad3f70e5-9014-4fa5-a0c3-22aa955300a0',
      sourceCommandId: 'cmd-2559fc4b-1',
      summary: [{ type: 'text', text: SUMMARY }],
      shadowedRange: { start: 129316, end: 134370 },
      shadowedSeqs: Array.from({ length: 1827 }, (_, i) => 129316 + i),
      shadowedTokenCount: 445820,
      provider: 'deepseek-account',
      model: 'deepseek-flash',
      maxTokens: 65536,
      usage: { inputTokens: 490, outputTokens: 3133, cacheReadTokens: 615552 },
    },
  }
}

function readRows() {
  const db = join(storeDir, 'sessions', SID, 'memory.db')
  execFileSync(process.env.BIOCORE_PYTHON || 'python', ['-c', DUMP_PY, db, dumpPath], {
    env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }),
    stdio: ['ignore', 'ignore', 'pipe'],
  })
  return JSON.parse(readFileSync(dumpPath, 'utf8'))
}

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }

const storeDir = mkdtempSync(join(tmpdir(), 'biocore-compaction-'))
const dumpPath = join(storeDir, 'dump.json')
try {
  const hooks = []
  const ctx = { on(name, listener) { hooks.push({ name, listener }); return () => {} } }
  const capture = registerMemoryCapture(ctx, () => ({ memoryEnabled: true }), storeDir)
  assert.ok(capture, 'capture registered')
  assert.equal(hooks[0].name, 'session/event')

  // 纯搬运层：字段与正文都能独立核对（不经过 python）
  const payload = compactionPayload(compactionEvent(), SID)
  assert.equal(payload.shadowed_count, 1827)
  assert.equal(payload.shadowed_tokens, 445820)
  assert.equal(payload.text, SUMMARY)
  assert.equal(payload.covered_at, EVENT_TIME)
  assert.equal(compactionText([{ type: 'text', text: 'a  b' }]), 'a  b')
  ok('compactionPayload 取出 range/count/tokens/text，正文不归一化')

  hooks[0].listener({ id: SID }, compactionEvent())
  await capture.drain()
  const first = readRows()
  assert.equal(first.length, 1, 'one compaction row')
  const row = first[0]
  assert.equal(row[0], SID)
  assert.equal(row[1], 'ad3f70e5-9014-4fa5-a0c3-22aa955300a0')
  assert.deepEqual([row[2], row[3]], [129316, 134370])
  assert.deepEqual([row[4], row[5]], [1827, 445820])
  assert.deepEqual([row[6], row[7]], ['deepseek-account', 'deepseek-flash'])
  assert.equal(row[8], SUMMARY, 'text 与事件里的摘要逐字一致')
  assert.equal(JSON.parse(row[9]).cacheReadTokens, 615552)
  assert.equal(Math.round(row[10] * 1000), EVENT_TIME, 'window_end 来自事件 time')
  ok('session/event -> capture 分支 -> memory.db 的 compactions 表')

  hooks[0].listener({ id: SID }, compactionEvent())
  await capture.drain()
  const second = readRows()
  assert.equal(second.length, 1, '同一 compactionId 不新增行')
  assert.deepEqual(second, first, '重复事件一行一字未变')
  ok('重复的 compaction/summary 幂等：不新增、不改旧行')

  hooks[0].listener({ id: SID }, { type: 'compaction/start', time: EVENT_TIME, data: {} })
  await capture.drain()
  assert.equal(readRows().length, 1, 'compaction/start 不写表')
  assert.equal(messageKind(compactionEvent()), null)
  ok('compaction/start 与消息分支都不受影响')

  capture.dispose()
} finally {
  rmSync(storeDir, { recursive: true, force: true })
}

console.log('\nall ' + passed + ' checks passed')
