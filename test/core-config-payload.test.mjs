// coreConfigPayload：宿主只改自己那九个开关，核心自己写下的开关（body）必须活着。
// 2026-11-16：switches 整块覆盖把 core_config.switches.body 抹掉，宿主与核心
// 于是各按各的缺省判 body，3081 上心跳一行都没有。
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { coreConfigPayload } from '../lib/index.js'

function dataDir(coreConfig, flags) {
  const dir = mkdtempSync(join(tmpdir(), 'bio-config-payload-'))
  writeFileSync(join(dir, 'core_config.json'), JSON.stringify(coreConfig || {}), 'utf8')
  if (flags) writeFileSync(join(dir, 'flags.json'), JSON.stringify(flags), 'utf8')
  return dir
}

test('a core-owned switch survives the host re-configure', () => {
  const dir = dataDir({ switches: { body: false, core_enabled: true } },
    { core_enabled: true, mood_recall: false })
  const payload = coreConfigPayload(dir)
  assert.equal(payload.switches.body, false, 'switches.body 被宿主抹掉了')
  assert.equal(payload.switches.mood_recall, false, '面板开关必须以 flags.json 为准')
  assert.equal(payload.switches.core_enabled, true)
})

test('panel switches stay authoritative over carried values', () => {
  const dir = dataDir({ switches: { body: true, core_enabled: false } },
    { core_enabled: true })
  const payload = coreConfigPayload(dir)
  assert.equal(payload.switches.core_enabled, true)
  assert.equal(payload.switches.body, true)
})

test('missing core_config.json still yields the panel switch set', () => {
  const dir = mkdtempSync(join(tmpdir(), 'bio-config-empty-'))
  const payload = coreConfigPayload(dir)
  assert.equal(typeof payload.switches.core_enabled, 'boolean')
  assert.equal(payload.data_dir, dir)
})
