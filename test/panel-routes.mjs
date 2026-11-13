// 旧设置路由改成转发后的四组契约（a–d）。用假 req/res + 注入 runAction，不碰真宿主。
import assert from 'node:assert/strict'
import test from 'node:test'
import { handleSettings, handleSettingsTest } from '../lib/index.js'

function fakeReq(body, method = 'POST') {
  const text = JSON.stringify(body || {})
  return {
    method,
    headers: { 'content-type': 'application/json', 'content-length': String(Buffer.byteLength(text)) },
    on(event, handler) {
      if (event === 'data') handler(Buffer.from(text))
      if (event === 'end') handler()
      return this
    },
    removeListener() { return this },
    destroy() {},
  }
}

function fakeRes() {
  const out = { code: 200, body: null }
  return {
    statusCode: 200,
    setHeader() {},
    writeHead(code) { this.statusCode = code; return this },
    end(payload) { out.body = payload ? JSON.parse(payload) : null },
    get result() { return { code: this.statusCode, body: out.body } },
  }
}

test('a) 旧读路由的字段集合包含改造前的字段，且不再有 restart_required', async () => {
  const res = fakeRes()
  const legacyKeys = ['ok', 'embedding', 'rerank', 'expression', 'autonomy', 'recall',
    'plugin_version', 'sanitize_reply']
  await handleSettings(fakeReq(null, 'GET'), res, process.env.TEST_DATA_DIR,
    { runAction: async () => ({ ok: true, models: { summarizer: { model: 'x' } },
      settings: { event_preset: 3 }, status: { portrait: { items: 1 } } }) })
  for (const key of legacyKeys) assert.ok(key in res.result.body, 'missing legacy key ' + key)
  assert.ok(!('restart_required' in res.result.body), 'restart_required 必须已删除')
  assert.ok(res.result.body.models, 'new field appended')
  assert.ok(res.result.body.status)
})

test('b) 旧写路由不再回 restart_required，写后不重启读回即新值', async () => {
  const res = fakeRes()
  const calls = []
  let written = null
  await handleSettings(fakeReq({ confirm: true, recall: { event_preset: 3 },
    embedding: { mode: 'auto', api_key: '••••' } }), res, process.env.TEST_DATA_DIR,
    { runAction: async (action, payload) => {
      calls.push([action, payload])
      written = payload.patch.settings.event_preset
      return { ok: true, settings: { event_preset: written }, models: {} }
    } })
  assert.ok(!('restart_required' in res.result.body), 'restart_required 必须已删除')
  assert.equal(calls.length, 1, '一次保存只允许一次写入')
  assert.equal(calls[0][0], 'settings_set')
  assert.equal(written, 3)
  assert.equal(res.result.body.settings.event_preset, 3, '读回就是新值（无需重启）')
  assert.ok(!('api_key' in calls[0][1].patch.model.embedding), '打码值不许覆盖真密钥')
})

test('c) 旧测试路由认 summarizer', async () => {
  const res = fakeRes()
  let seen = null
  await handleSettingsTest(fakeReq({ kind: 'summarizer' }), res, { dataDir: process.env.TEST_DATA_DIR,
    runAction: async (action, payload) => { seen = [action, payload]; return { ok: true, ms: 12, code: 'ok' } } })
  assert.equal(seen[0], 'test_connection')
  assert.equal(seen[1].target, 'summarizer')
  assert.equal(res.result.body.kind, 'summarizer')
  assert.equal(res.result.body.elapsed_ms, 12)
})

test('d) 白名单外动作走旧路由也不行（bad_kind）', async () => {
  const res = fakeRes()
  await handleSettingsTest(fakeReq({ kind: 'capture' }), res, { dataDir: process.env.TEST_DATA_DIR,
    runAction: async () => { throw new Error('should not run') } })
  assert.equal(res.result.code, 400)
  assert.equal(res.result.body.reason, 'bad_kind')
})
