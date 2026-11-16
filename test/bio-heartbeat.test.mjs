// 生命循环心跳：10s 唤醒 → 每 6 次（≈60s）一次 body_loop。
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  bodyEnabled, runBeat, registerBioHeartbeat, WAKE_MS, TICKS_PER_BEAT,
  MEMORY_EVERY_WAKES,
} from '../lib/modules/bio-heartbeat.js'
import { resetActivity, recordActivity, getActiveSession } from '../lib/modules/proactive.js'

const NOW = 1_800_000_000_000

function cfgDir(body) {
  const dir = mkdtempSync(join(tmpdir(), 'bio-heartbeat-'))
  writeFileSync(join(dir, 'core_config.json'),
    JSON.stringify({ switches: { body } }), 'utf8')
  return dir
}

function fakeCtx() {
  const hooks = []
  return {
    logger: { info() {}, warn() {} },
    on(name, handler) { hooks.push({ name, handler }); return () => { hooks.length = 0 } },
    hooks,
    messages: [],
  }
}

test('body switch comes from core_config.json', () => {
  assert.equal(bodyEnabled({}, cfgDir(false)), false)
  assert.equal(bodyEnabled({}, cfgDir(true)), true)
  assert.equal(bodyEnabled({ bodyEnabled: true }, cfgDir(false)), true)
  // 2026-11-16 核心把 body 缺省翻成"开"（pipeline._body_default_on），这道门跟着翻：
  // 以前"没有配置文件"= 关，于是没人写过 body:true 的实例永远不会跳（3081 零心跳行）。
  assert.equal(bodyEnabled({}, join(tmpdir(), 'missing-dir-xyz')), true)
  // 显式关掉仍然算数：config / switches 两个入口都要留，别把"关"做没了。
  assert.equal(bodyEnabled({ bodyEnabled: false }, cfgDir(true)), false)
  assert.equal(bodyEnabled({ switches: { body: false } }, cfgDir(true)), false)
})

test('BIOCORE_BODY overrides both ways', () => {
  const saved = process.env.BIOCORE_BODY
  try {
    process.env.BIOCORE_BODY = '0'
    assert.equal(bodyEnabled({}, cfgDir(true)), false)
    assert.equal(bodyEnabled({ bodyEnabled: true }, cfgDir(true)), false)
    process.env.BIOCORE_BODY = '1'
    assert.equal(bodyEnabled({}, cfgDir(false)), true)
    process.env.BIOCORE_BODY = ''
    assert.equal(bodyEnabled({}, cfgDir(false)), false)
  } finally {
    if (saved === undefined) delete process.env.BIOCORE_BODY
    else process.env.BIOCORE_BODY = saved
  }
})

test('body off: no python call, no session needed', async () => {
  resetActivity()
  let called = 0
  const out = await runBeat(fakeCtx(), {
    dataDir: cfgDir(false), getConfig: () => ({ dataDir: cfgDir(false) }),
    deps: { bodyLoop: async () => { called += 1 } },
  })
  assert.equal(out.skipped, true)
  assert.equal(out.reason, 'body_off')
  assert.equal(called, 0)
})

test('no active session: falls back to the last touched one', async () => {
  // 安静下来也要跳：没有"活跃会话"时找最近动过的那个，否则身体一停就是几个小时。
  resetActivity()
  let payload = null
  const out = await runBeat(fakeCtx(), {
    dataDir: cfgDir(true), getConfig: () => ({ dataDir: cfgDir(true) }),
    deps: {
      lastTouchedSession: () => 'session-old',
      bodyLoop: async (p) => { payload = p; return { ok: true, ticks: 1 } },
    },
  })
  assert.notEqual(out.skipped, true)
  assert.equal(payload.session_id, 'session-old')
})

test('no session anywhere: no python call', async () => {
  resetActivity()
  let called = 0
  const out = await runBeat(fakeCtx(), {
    dataDir: cfgDir(true), getConfig: () => ({ dataDir: cfgDir(true) }),
    deps: {
      lastTouchedSession: () => '',
      bodyLoop: async () => { called += 1 },
    },
  })
  assert.equal(out.reason, 'no_session')
  assert.equal(called, 0)
})

test('beat forwards session/store/now and returns python decision', async () => {
  resetActivity()
  recordActivity('s1', NOW - 60_000)
  let payload = null
  const out = await runBeat(fakeCtx(), {
    dataDir: cfgDir(true), now: NOW,
    getConfig: () => ({ dataDir: cfgDir(true) }),
    deps: { bodyLoop: async (value) => {
      payload = value
      return { ok: true, ticks: 2, over_threshold: true, intent_text: '想和你说说话', state: { valence: -0.1, stress: 0.2 } }
    } },
  })
  assert.equal(payload.session_id, 's1')
  assert.ok(String(payload.store).endsWith('memory'))
  assert.equal(payload.now, NOW / 1000)
  assert.equal(out.ticks, 2)
  assert.equal(out.over_threshold, true)
})

test('a throwing beat is contained and the next beat still runs', async () => {
  resetActivity()
  recordActivity('s1', NOW)
  let calls = 0
  const deps = {
    bodyLoop: async () => { calls += 1; throw new Error('worker down') },
  }
  const failed = await runBeat(fakeCtx(), { dataDir: cfgDir(true), getConfig: () => ({}), deps })
  assert.equal(failed.ok, false)
  assert.equal(failed.reason, 'error')
  deps.bodyLoop = async () => { calls += 1; return { ok: true, ticks: 1, state: {} } }
  const ok = await runBeat(fakeCtx(), { dataDir: cfgDir(true), getConfig: () => ({}), deps })
  assert.equal(ok.ok, true)
  assert.equal(calls, 2)
})

test('register installs the activity hook and returns a disposer', () => {
  resetActivity()
  const ctx = fakeCtx()
  const dispose = registerBioHeartbeat(ctx, {
    getConfig: () => ({}), dataDir: cfgDir(false), wakeMs: 1000,
  })
  assert.equal(typeof dispose, 'function')
  assert.equal(ctx.hooks.length, 1)
  ctx.hooks[0].handler({ id: 's9' }, { type: 'user/message', data: { source: { kind: 'user' } } })
  assert.equal(getActiveSession(), 's9')
  dispose()
  resetActivity()
})

test('cadence constants match the AstrBot life loop', () => {
  assert.equal(WAKE_MS, 10_000)
  assert.equal(TICKS_PER_BEAT, 6)
  assert.equal(MEMORY_EVERY_WAKES, 360)
})
