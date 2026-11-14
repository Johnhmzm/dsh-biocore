// 压缩记录补扫：turn/start 触发一次，每会话 60 秒一次，日志没变不扫。
//
// 上半段不碰 python：注入假时钟 / 假 stat / 假 scan，只证明「什么时候扫」。
// 下半段真走一遍 bridge -> pipeline -> memory.db：造多帧 zstd 日志，断言正文逐字、
// 第二次零写入、日志缺失时 ok=false。
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { compactionScan } from '../lib/py-bridge.js'
import {
  SCAN_INTERVAL_MS,
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

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }

/** 一个注入点齐全的注册器：假时钟、假 stat、假 scan。 */
function harness(options = {}) {
  const hooks = []
  const calls = []
  let clock = 1000
  let stats = { mtimeMs: 10, size: 8 }
  const ctx = { on(name, listener) { hooks.push({ name, listener }); return () => {} } }
  const mod = registerCompactionScan(ctx, () => options.config || { compactionScan: true },
    'C:/some/store', {
      now: () => clock,
      stat: () => (stats === null ? null : stats),
      scan: async (dir, sessionId) => {
        calls.push({ dir, sessionId })
        return options.result || { ok: true, log: 'C:/log.zstd', inserted: 1, skipped: 0 }
      },
    })
  return {
    hooks, calls, mod,
    advance(ms) { clock += ms },
    setStats(value) { stats = value },
    fire(sessionId = 'session-a', type = 'turn/start') {
      return hooks[0].listener({ id: sessionId }, { type, data: { turn: 1 } })
    },
  }
}

const unitDir = mkdtempSync(join(tmpdir(), 'biocore-scan-'))
const work = mkdtempSync(join(tmpdir(), 'biocore-scan-int-'))
try {
  // ---- 节流层（不碰 python） --------------------------------------------
  const logFile = join(unitDir, 'session.v4.jsonl.zstd')
  writeFileSync(logFile, 'frame-1\n')

  const h = harness()
  assert.equal(h.hooks.length, 1)
  assert.equal(h.hooks[0].name, 'session/event')

  // 第一次 turn/start -> 扫一次；trigger 立刻返回（fire-and-forget）
  assert.equal(h.fire('session-a'), true)
  assert.equal(h.calls.length, 0, 'trigger 不等 python：此刻扫描还没开始')
  await h.mod.drain()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].dir, 'C:/some/store')
  assert.equal(h.calls[0].sessionId, 'session-a')
  ok('第一个 turn/start 触发一次扫描，且不阻塞调用方')

  // 60 秒内第二个 turn/start 不触发（哪怕日志变了）
  h.advance(1000)
  h.setStats({ mtimeMs: 99, size: 999 })
  assert.equal(h.fire('session-a'), false)
  await h.mod.drain()
  assert.equal(h.calls.length, 1, '60 秒内不重复扫')
  ok('60 秒内的第二个 turn/start 不触发')

  // 过了 60 秒但日志没变 -> 还是跳过
  h.advance(SCAN_INTERVAL_MS)
  h.setStats({ mtimeMs: 10, size: 8 })
  assert.equal(h.fire('session-a'), false)
  await h.mod.drain()
  assert.equal(h.calls.length, 1, 'mtime/size 没变就不扫')
  ok('过了 60 秒、日志没变，仍然跳过')

  // 过了 60 秒且日志变了 -> 扫
  h.advance(SCAN_INTERVAL_MS)
  h.setStats({ mtimeMs: 11, size: 9 })
  assert.equal(h.fire('session-a'), true)
  await h.mod.drain()
  assert.equal(h.calls.length, 2)
  ok('过了 60 秒且日志变了，触发第二次')

  // stat 拿不到时只按 60 秒判（拿不到不算「没变」）
  h.advance(SCAN_INTERVAL_MS)
  h.setStats(null)
  assert.equal(h.fire('session-a'), true)
  await h.mod.drain()
  assert.equal(h.calls.length, 3)
  ok('stat 不到日志时退回 60 秒节流')

  // 别的会话各算各的；只有 turn/start 与 user/message 触发，其余一概不看
  const g = harness()
  g.fire('session-b', 'assistant/message')
  g.fire('session-c', 'compaction/summary')
  g.fire('session-c', 'step/start')
  await g.mod.drain()
  assert.equal(g.calls.length, 0, 'assistant/message、compaction/*、step/* 都不看')
  g.fire('session-b', 'user/message')
  g.fire('session-c', 'turn/start')
  await g.mod.drain()
  assert.deepEqual(g.calls.map((call) => call.sessionId), ['session-b', 'session-c'])
  ok('turn/start 与 user/message 都触发，其余事件不看，且每会话独立节流')

  // 同一回合里两个触发事件只扫一次：节流窗内 user/message 不重复扫
  const twin = harness()
  assert.equal(twin.fire('session-a', 'turn/start'), true)
  await twin.mod.drain()
  assert.equal(twin.calls.length, 1)
  assert.equal(twin.fire('session-a', 'user/message'), false, '同一回合的 user/message 被节流挡住')
  twin.advance(1000)
  twin.setStats({ mtimeMs: 77, size: 77 })
  assert.equal(twin.fire('session-a', 'user/message'), false, '一分钟内第三条也不扫')
  await twin.mod.drain()
  assert.equal(twin.calls.length, 1, '一个回合只扫一次，不会一边 turn/start 一边 user/message 双扫')
  twin.advance(SCAN_INTERVAL_MS)
  assert.equal(twin.fire('session-a', 'user/message'), true, '过了窗口，user/message 单独也能触发')
  await twin.mod.drain()
  assert.equal(twin.calls.length, 2)
  ok('节流窗内 user/message 不重复扫；窗口外它自己也能触发')

  // 开关关掉：一次都不扫
  const off = harness({ config: { compactionScan: false } })
  assert.equal(off.fire('session-a'), false)
  await off.mod.drain()
  assert.equal(off.calls.length, 0)
  ok('compactionScan=false 时完全不扫')

  // 扫描失败静默：不抛、不影响后续
  const bad = harness({ result: { ok: false, reason: 'log_not_found', log: '' } })
  bad.fire('session-a')
  await bad.mod.drain()
  assert.equal(bad.calls.length, 1)
  bad.advance(SCAN_INTERVAL_MS)
  bad.fire('session-a')
  await bad.mod.drain()
  assert.equal(bad.calls.length, 2, '失败也只等节流，不会卡死')
  ok('扫描失败静默：不抛异常，节流照旧')

  // 纯函数：shouldScan / statOf
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
  ok('shouldScan / statOf 的边界')

  h.mod.dispose()
  g.mod.dispose()
  off.mod.dispose()
  bad.mod.dispose()

  // ---- 集成：真走 python ------------------------------------------------
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
  ok('集成：compactionScan -> memory.db，正文逐字、第二次零写入')

  const empty = await compactionScan(store, 'session-does-not-exist', { sessions_root: sessionsRoot })
  assert.equal(empty.ok, false)
  assert.equal(empty.reason, 'log_not_found')
  assert.equal(empty.inserted, 0)
  ok('集成：日志缺失时 ok=false，不抛不建库')
} finally {
  rmSync(unitDir, { recursive: true, force: true })
  rmSync(work, { recursive: true, force: true })
}

console.log('\nall ' + passed + ' checks passed')
