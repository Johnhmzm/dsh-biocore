// 压缩记录落库：一级读会话对象（内存快照，不节流），二级扫日志（60 秒节流）。
//
// 上半段不碰 python：注入假时钟 / 假 stat / 假 record / 假 scan / 假 session，
// 只证明「什么时候走哪一级」。下半段真走一遍 bridge 落库。
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { compactionScan } from '../lib/py-bridge.js'
import {
  SCAN_INTERVAL_MS,
  compactionsOf,
  lastSeqOf,
  memoryEventsOf,
  registerCompactionScan,
  shouldScan,
  statOf,
} from '../lib/modules/compaction-scan.js'

const PYTHON = process.env.BIOCORE_PYTHON || 'python'
const PY_ENV = Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' })

// 造一份多帧 zstd 日志：只读第一帧的实现会漏掉后面的事件
const MAKE_PY = [
  'import json, sys, zstandard as zstd',
  'from pathlib import Path',
  'root, sid, texts = Path(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])',
  'log = root / "--ws--" / ("session-" + sid) / "session.v4.jsonl.zstd"',
  'log.parent.mkdir(parents=True, exist_ok=True)',
  'events = [{"type": "turn/start", "time": 1, "data": {"turn": 1}}]',
  'for i, text in enumerate(texts):',
  '    events.append({"type": "compaction/summary", "time": 1000 + i, "data": {',
  '        "compactionId": "cid-%d" % i, "summary": [{"type": "text", "text": text}],',
  '        "shadowedRange": {"start": i * 10, "end": i * 10 + 5},',
  '        "shadowedSeqs": [0, 1, 2, 3, 4], "shadowedTokenCount": 100 * i}})',
  'frames = [events[:1], events[1:]]',
  'with open(str(log), "wb") as handle:',
  '    for frame in frames:',
  '        body = "\\n".join(json.dumps(e, ensure_ascii=False) for e in frame)',
  '        handle.write(zstd.ZstdCompressor().compress((body + "\\n").encode("utf-8")))',
  'print(str(log))',
].join('\n')

const READ_PY = [
  'import json, sqlite3, sys',
  'conn = sqlite3.connect(sys.argv[1])',
  'cursor = conn.execute("SELECT compaction_id, text FROM compactions ORDER BY id")',
  'rows = cursor.fetchall()',
  'conn.close()',
  'open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(rows, ensure_ascii=False))',
].join('\n')

const SUMMARY = '# 压缩摘要\n\n 保留  两个空格。'

/** 一条 compaction/summary 事件（形状与日志里的一致）。 */
function compactionEvent(index, text = SUMMARY + index) {
  return {
    type: 'compaction/summary',
    seq: 100 + index,
    time: 1794638238383 + index,
    data: {
      compactionId: 'cid-' + index,
      summary: [{ type: 'text', text }],
      shadowedRange: { start: index * 10, end: index * 10 + 5 },
      shadowedSeqs: [0, 1, 2, 3, 4],
      shadowedTokenCount: 100 * index,
    },
  }
}

/** 宿主会话对象：只实现用到的那点接口。 */
function fakeSession(id, events, mode = 'snapshot') {
  if (mode === 'none') return { id }
  if (mode === 'own') return { id, ownEvents: () => events }
  if (mode === 'throws') {
    return {
      id,
      snapshotEvents() { throw new Error('deprecated: gone') },
      ownEvents: () => events,
    }
  }
  return { id, snapshotEvents: (from) => events.filter((e) => Number(e.seq) >= from) }
}

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }

/** 注入点齐全的注册器：假时钟、假 stat、假 record、假 scan。 */
function harness(options = {}) {
  const hooks = []
  const calls = { scan: [], record: [] }
  let clock = 1000
  let stats = { mtimeMs: 10, size: 8 }
  const ctx = { on(name, listener) { hooks.push({ name, listener }); return () => {} } }
  const mod = registerCompactionScan(ctx, () => options.config || { compactionScan: true },
    'C:/some/store', {
      now: () => clock,
      stat: () => (stats === null ? null : stats),
      scan: async (dir, sessionId) => {
        calls.scan.push({ dir, sessionId })
        return options.result || { ok: true, log: 'C:/log.zstd', inserted: 1, skipped: 0 }
      },
      record: async (dir, sessionId, payload) => {
        calls.record.push({ dir, sessionId, id: payload && payload.compaction_id,
          text: payload && payload.text })
        return options.recordResult || { ok: true, recorded: 1, skipped: 0 }
      },
    })
  return {
    hooks, calls, mod,
    advance(ms) { clock += ms },
    setStats(value) { stats = value },
    fire(session, type = 'turn/start') {
      const target = typeof session === 'string' ? { id: session } : session
      return hooks[0].listener(target, { type, data: { turn: 1 } })
    },
  }
}

const unitDir = mkdtempSync(join(tmpdir(), 'biocore-scan-'))
const work = mkdtempSync(join(tmpdir(), 'biocore-scan-int-'))
try {
  const logFile = join(unitDir, 'session.v4.jsonl.zstd')
  writeFileSync(logFile, 'frame-1\n')

  // ---- 纯函数 ----------------------------------------------------------
  const snapshot = fakeSession('s', [compactionEvent(0), { type: 'user/message', seq: 5 }])
  assert.deepEqual(memoryEventsOf(snapshot, 0).source, 'snapshot')
  assert.equal(memoryEventsOf(snapshot, 0).events.length, 2)
  assert.equal(memoryEventsOf(fakeSession('s', [compactionEvent(0)], 'own'), 0).source, 'own')
  assert.equal(memoryEventsOf(fakeSession('s', [], 'none'), 0).events, null)
  assert.equal(memoryEventsOf(null, 0).events, null)
  const viaThrows = memoryEventsOf(fakeSession('s', [compactionEvent(0)], 'throws'), 0)
  assert.equal(viaThrows.source, 'own', 'snapshotEvents 抛异常就退到 ownEvents')
  assert.equal(compactionsOf([{ type: 'user/message' }, compactionEvent(0)]).length, 1)
  assert.equal(lastSeqOf([{ seq: 3 }, { seq: 9 }, { seq: 5 }]), 9)
  assert.equal(lastSeqOf([{ type: 'x' }]), null)
  assert.equal(shouldScan(null, 1000, null), true)
  assert.equal(shouldScan({ at: 1000, mtimeMs: 1, size: 2 }, 1000 + SCAN_INTERVAL_MS - 1,
    { mtimeMs: 9, size: 9 }), false)
  assert.equal(shouldScan({ at: 1000, mtimeMs: 1, size: 2 }, 1000 + SCAN_INTERVAL_MS,
    { mtimeMs: 1, size: 2 }), false)
  assert.equal(shouldScan({ at: 1000, mtimeMs: 1, size: 2 }, 1000 + SCAN_INTERVAL_MS,
    { mtimeMs: 1, size: 3 }), true)
  assert.equal(shouldScan({ at: 1000, mtimeMs: 1, size: 2 }, 1000 + SCAN_INTERVAL_MS, null), true)
  const stats = statOf(logFile)
  assert.ok(stats && stats.size > 0 && Number.isFinite(stats.mtimeMs))
  assert.equal(statOf(join(unitDir, 'missing.zstd')), null)
  ok('memoryEventsOf / compactionsOf / lastSeqOf / shouldScan / statOf 的边界')

  // ---- 一级命中：落库一次，第二次同样输入不重复 --------------------------
  const one = harness()
  const memSession = fakeSession('session-a',
    [{ type: 'turn/start', seq: 5 }, { type: 'user/message', seq: 6 },
      compactionEvent(0), compactionEvent(1)])
  assert.equal(one.fire(memSession), true)
  assert.equal(one.calls.record.length, 0, 'trigger 不等桥：此刻还没落库')
  await one.mod.drain()
  assert.equal(one.calls.record.length, 2)
  assert.deepEqual(one.calls.record.map((call) => call.id), ['cid-0', 'cid-1'])
  assert.equal(one.calls.record[0].text, SUMMARY + 0, '正文由事件逐字搬过来')
  assert.equal(one.calls.scan.length, 0, '一级命中就不碰日志')
  ok('一级命中：两条 compaction 各落库一次，且完全不扫日志')

  one.fire(memSession)
  await one.mod.drain()
  assert.equal(one.calls.record.length, 2, '同一批事件不再落库（compactionId 去重）')
  ok('一级重复输入不重复落库（已处理的 compactionId 记住）')

  // ---- 一级拿不到：退回日志扫描 ----------------------------------------
  const empty = harness()
  empty.fire(fakeSession('session-b', []))
  await empty.mod.drain()
  assert.equal(empty.calls.record.length, 0)
  assert.equal(empty.calls.scan.length, 1, '一条事件都拿不到 -> 走日志')

  const none = harness()
  none.fire(fakeSession('session-c', [], 'none'))
  await none.mod.drain()
  assert.equal(none.calls.scan.length, 1, '宿主没有这两个 API -> 走日志')
  ok('一级拿不到事件（空数组 / 没有 API）就退回日志扫描')

  const viaOwn = harness()
  viaOwn.fire(fakeSession('session-d', [compactionEvent(2)], 'throws'))
  await viaOwn.mod.drain()
  assert.deepEqual(viaOwn.calls.record.map((call) => call.id), ['cid-2'])
  assert.equal(viaOwn.calls.scan.length, 0, 'ownEvents 命中同样不扫日志')
  ok('snapshotEvents 不可用时 ownEvents 顶上，命中照样不扫日志')

  // ---- 节流只作用于二级 -----------------------------------------------
  const tiered = harness()
  const growing = fakeSession('session-e', [compactionEvent(0)], 'own')
  const growing2 = fakeSession('session-e', [compactionEvent(0), compactionEvent(3)], 'own')
  tiered.fire(growing)
  await tiered.mod.drain()
  assert.equal(tiered.calls.record.length, 1)
  tiered.advance(1000)   // 远小于 60 秒
  tiered.fire(growing2)  // 内存里多了一条新的压缩
  await tiered.mod.drain()
  assert.equal(tiered.calls.record.length, 2, '一级不节流：60 秒内新压缩照样落库')
  assert.equal(tiered.calls.scan.length, 0, '有 compaction 时二级根本不跑')
  ok('一级不节流：60 秒内的新压缩照样落库')

  const throttled = harness()
  const noCompaction = fakeSession('session-f', [{ type: 'user/message', seq: 5 }], 'own')
  throttled.fire(noCompaction)
  await throttled.mod.drain()
  assert.equal(throttled.calls.scan.length, 1)
  assert.equal(throttled.fire(noCompaction), true, '触发本身照旧返回 true')
  await throttled.mod.drain()
  assert.equal(throttled.calls.scan.length, 1, '60 秒内二级不重复扫')
  throttled.advance(SCAN_INTERVAL_MS)
  throttled.setStats({ mtimeMs: 11, size: 9 })
  throttled.fire(noCompaction)
  await throttled.mod.drain()
  assert.equal(throttled.calls.scan.length, 2, '过了窗口且日志变了，二级才再扫')
  ok('二级仍受 60 秒节流 + 日志签名约束')

  // ---- 自检日志格式 ----------------------------------------------------
  const previous = process.env.BIOCORE_DEBUG_CAPTURE
  process.env.BIOCORE_DEBUG_CAPTURE = '1'
  const lines = []
  const originalLog = console.log
  console.log = (...args) => { lines.push(args.join(' ')) }
  try {
    const dbg = harness()
    dbg.fire(fakeSession('session-g', [{ type: 'user/message', seq: 5 }, compactionEvent(0)]))
    await dbg.mod.drain()
  } finally {
    console.log = originalLog
    if (previous === undefined) delete process.env.BIOCORE_DEBUG_CAPTURE
    else process.env.BIOCORE_DEBUG_CAPTURE = previous
  }
  assert.ok(lines.includes('[scan] mem events=2 compactions=1'),
    '自检日志格式固定：拿到的事件数 / 其中的 compaction 数，实际输出 ' + JSON.stringify(lines))
  ok('BIOCORE_DEBUG_CAPTURE=1 时打印 [scan] mem events=<n> compactions=<m>')

  // ---- 基础行为仍成立 --------------------------------------------------
  const g = harness()
  g.fire('session-h', 'assistant/message')
  g.fire('session-h', 'compaction/summary')
  g.fire('session-h', 'step/start')
  await g.mod.drain()
  assert.equal(g.calls.scan.length, 0, 'assistant/message、compaction/*、step/* 都不触发')
  g.fire('session-h', 'user/message')
  await g.mod.drain()
  assert.equal(g.calls.scan.length, 1, 'user/message 自己也能触发')
  ok('只有 turn/start 与 user/message 触发，其余事件不看')

  const off = harness({ config: { compactionScan: false } })
  off.fire(fakeSession('session-i', [compactionEvent(0)]))
  await off.mod.drain()
  assert.equal(off.calls.record.length + off.calls.scan.length, 0)
  ok('compactionScan=false 时一级二级都不跑')

  const bad = harness({ result: { ok: false, reason: 'log_not_found', log: '' } })
  bad.fire('session-j')
  await bad.mod.drain()
  assert.equal(bad.calls.scan.length, 1)
  bad.advance(SCAN_INTERVAL_MS)
  bad.fire('session-j')
  await bad.mod.drain()
  assert.equal(bad.calls.scan.length, 2, '二级失败也只等节流，不会卡死')
  ok('二级失败静默：不抛异常，节流照旧')

  // ---- 集成：真走 python ----------------------------------------------
  const INT_SID = 'session-scan-int-1'
  const INT_TEXTS = ['# 摘要一\n\n 第一段 保留  两个空格。', '# 摘要二\n\n- 第二段。']
  const sessionsRoot = join(work, 'sessions-root')
  const store = join(work, 'mem')
  // 目录名永远是 session-<裸 id>；MAKE_PY 自己补前缀，所以这里传裸 id
  execFileSync(PYTHON, ['-c', MAKE_PY, sessionsRoot, INT_SID.replace(/^session-/, ''),
    JSON.stringify(INT_TEXTS)], { env: PY_ENV, stdio: ['ignore', 'pipe', 'pipe'] })

  const first = await compactionScan(store, INT_SID, { sessions_root: sessionsRoot })
  assert.equal(first.ok, true, 'bridge -> pipeline -> scan 走通')
  assert.equal(first.scanned, 2)
  assert.equal(first.inserted, 2)
  assert.equal(first.session_id, INT_SID, '默认落 runtime 目录（与 record/recall 一致）')
  const second = await compactionScan(store, INT_SID, { sessions_root: sessionsRoot })
  assert.equal(second.inserted, 0, '再扫一次一条不写')
  assert.equal(second.skipped, 2)

  const dumpPath = join(work, 'rows.json')
  execFileSync(PYTHON, ['-c', READ_PY, join(store, 'sessions', INT_SID, 'memory.db'), dumpPath], {
    env: PY_ENV, stdio: ['ignore', 'ignore', 'pipe'],
  })
  const rows = JSON.parse(readFileSync(dumpPath, 'utf8'))
  assert.deepEqual(rows.map((row) => row[0]), ['cid-0', 'cid-1'])
  assert.deepEqual(rows.map((row) => row[1]), INT_TEXTS, '正文与日志逐字一致')
  ok('集成（二级）：compactionScan -> memory.db，正文逐字、第二次零写入')

  const empty2 = await compactionScan(store, 'session-does-not-exist', { sessions_root: sessionsRoot })
  assert.equal(empty2.ok, false)
  assert.equal(empty2.reason, 'log_not_found')
  assert.equal(empty2.inserted, 0)
  ok('集成（二级）：日志缺失时 ok=false，不抛不建库')
} finally {
  rmSync(unitDir, { recursive: true, force: true })
  rmSync(work, { recursive: true, force: true })
}

console.log('\nall ' + passed + ' checks passed')
