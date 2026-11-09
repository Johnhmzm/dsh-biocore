// task-214: persona -> persistent DSH preset persona tests.
// The host configEditor is faked: it records the edit and applies it in memory.
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { EventEmitter } from 'node:events'
import {
  applyPersonaPreset, rollbackPersonaPreset, personaPresetStatus,
  withPersonaPrefix, findPersonaRow, personaHash, readPersonaText,
  defaultPresetId, findPresetEntry, PERSONA_PLUGIN_NAME,
  backupPath, statusPath, PERSONA_TEMPLATE_RE,
  sanitizePersonaTemplate, resolveCharName, PLACEHOLDER_RE
} from '../lib/modules/persona-preset.js'
import { handlePersonaApply } from '../lib/index.js'

const OLD_PREFIX = 'You are a coding agent powered by the {{model}} model.'

function fixture(presetId = 'ptc') {
  const original = {
    id: presetId,
    order: 2,
    plugins: [
      { id: 'persona', name: PERSONA_PLUGIN_NAME, config: { prefix: OLD_PREFIX } },
      { id: 'tool-fs', name: '@deepseek-ai/dsh-tool-fs' },
    ],
  }
  const entry = { options: { id: 'preset-' + presetId, name: '@deepseek-ai/dsh-agent-preset', config: structuredClone(original) } }
  const memory = { options: { id: 'dsh-biocore-memory', name: 'dsh-biocore-memory', config: { personaFile: 'C:/bio/persona.md', injectMaxChars: 1200 } } }
  const calls = []
  const editor = {
    documentPath: 'C:/tmp/profile/cordis.patch.yml',
    entries: () => [entry, memory],
    edit: async (target, change) => {
      const next = change(structuredClone(target.options.config), {})
      calls.push({ id: target.options.id, next: structuredClone(next) })
      target.options.config = next
    },
  }
  const presets = { defaultId: presetId }
  return { entry, memory, original, editor, calls, presets }
}

function tempDir() { return mkdtempSync(join(tmpdir(), 'bc-persona-preset-')) }

function fakeRes() {
  return {
    code: 0, body: null,
    writeHead(code) { this.code = code },
    end(text) { this.body = JSON.parse(text) },
  }
}

function postReq(body) {
  const req = new EventEmitter()
  req.method = 'POST'
  setTimeout(() => {
    req.emit('data', Buffer.from(JSON.stringify(body)))
    req.emit('end')
  }, 0)
  return req
}

test('withPersonaPrefix rewrites only the persona row', () => {
  const { entry } = fixture()
  const next = withPersonaPrefix(entry.options.config, '她是灰羽。')
  const persona = next.plugins.find((row) => row.name === PERSONA_PLUGIN_NAME)
  assert.equal(persona.config.prefix, '她是灰羽。')
  assert.equal(next.plugins.length, 2)
  assert.equal(next.plugins[1].name, '@deepseek-ai/dsh-tool-fs')
  assert.equal(next.order, 2)
})

test('withPersonaPrefix inserts a persona row when missing', () => {
  const next = withPersonaPrefix({ id: 'minimal', plugins: [{ id: 'tool', name: 'x' }] }, 'hi')
  assert.equal(next.plugins.length, 2)
  assert.equal(next.plugins[0].name, PERSONA_PLUGIN_NAME)
  assert.equal(next.plugins[0].config.prefix, 'hi')
})

test('default preset id and entry lookup use the registry default', () => {
  assert.equal(defaultPresetId({ defaultId: 'ptc' }), 'ptc')
  assert.equal(defaultPresetId(null), 'standard')
  const { editor } = fixture('ptc')
  assert.equal(findPresetEntry(editor, 'ptc').options.id, 'preset-ptc')
  assert.equal(findPresetEntry(editor, 'missing'), null)
})

test('apply writes the persona into the default preset and status', async () => {
  const dir = tempDir()
  try {
    const { entry, editor, calls, presets } = fixture('ptc')
    const result = await applyPersonaPreset({ editor, presets, dataDir: dir, text: '她是灰羽，安静但黏人。', now: 1234 })
    assert.equal(result.ok, true)
    assert.equal(result.reason, 'applied')
    assert.equal(result.preset_id, 'ptc')
    assert.equal(result.chars, 11)
    assert.equal(result.injection, 'off')
    assert.equal(result.injection_disabled, true)
    assert.equal(calls.length, 2)
    assert.equal(calls[0].id, 'preset-ptc')
    assert.equal(calls[1].id, 'dsh-biocore-memory')
    assert.equal(calls[1].next.personaFile, '')
    const persona = entry.options.config.plugins.find((row) => row.name === PERSONA_PLUGIN_NAME)
    assert.equal(persona.config.prefix, '她是灰羽，安静但黏人。')
    const status = JSON.parse(readFileSync(statusPath(dir), 'utf8'))
    assert.equal(status.applied, true)
    assert.equal(status.injection, 'off')
    const backup = JSON.parse(readFileSync(backupPath(dir), 'utf8'))
    assert.equal(backup.config.plugins[0].config.prefix, OLD_PREFIX)
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('apply keeps the first backup when re-applied', async () => {
  const dir = tempDir()
  try {
    const { editor, presets } = fixture('ptc')
    await applyPersonaPreset({ editor, presets, dataDir: dir, text: 'first', now: 1 })
    await applyPersonaPreset({ editor, presets, dataDir: dir, text: 'second', now: 2 })
    const backup = JSON.parse(readFileSync(backupPath(dir), 'utf8'))
    assert.equal(backup.config.plugins[0].config.prefix, OLD_PREFIX)
    const status = JSON.parse(readFileSync(statusPath(dir), 'utf8'))
    assert.equal(status.chars, 6)
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('sanitizePersonaTemplate replaces user/char and strips unknown placeholders', () => {
  const out = sanitizePersonaTemplate('你是{{user}}，我是{{char}}；{{foo}} 与 {{ bar }}。', { charName: '安吉拉' })
  assert.equal(out.ok, true)
  assert.equal(out.text, '你是「您」，我是「安吉拉」；foo 与 bar。')
  assert.equal(out.replaced_placeholders, 4)
  assert.ok(out.raw_chars > out.sanitized_chars)
  assert.equal(PLACEHOLDER_RE.test('{{user}}'), true)
  const fallback = sanitizePersonaTemplate('{{char}}', {})
  assert.equal(fallback.text, '「她」')
})

test('sanitizePersonaTemplate rejects surviving braces only after conversion', () => {
  const unsafe = sanitizePersonaTemplate('坏 {{未闭合 的括号', { charName: '她' })
  assert.equal(unsafe.ok, false)
  assert.equal(unsafe.reason, 'template_unsafe')
  const tail = sanitizePersonaTemplate('尾巴 }} 多出来', { charName: '她' })
  assert.equal(tail.reason, 'template_unsafe')
  assert.equal(PERSONA_TEMPLATE_RE.test('坏 {{未闭合 的括号'), true)
})

test('resolveCharName reads the persona card title and falls back', () => {
  const dir = tempDir()
  try {
    writeFileSync(join(dir, 'persona.md'), '# 角色扮演：安吉拉（Angela）\n\n正文', 'utf8')
    assert.equal(resolveCharName(dir), '安吉拉')
  } finally { rmSync(dir, { recursive: true, force: true }) }
  const empty = tempDir()
  try { assert.equal(resolveCharName(empty), '她') }
  finally { rmSync(empty, { recursive: true, force: true }) }
})

test('apply converts {{user}}/{{char}} and records raw vs sanitized chars', async () => {
  const dir = tempDir()
  try {
    writeFileSync(join(dir, 'persona.md'), '# 角色扮演：安吉拉（Angela）\n\n你是{{user}}，我是{{char}}。', 'utf8')
    const { entry, editor, presets } = fixture('ptc')
    const result = await applyPersonaPreset({ editor, presets, dataDir: dir, text: readPersonaText(dir), now: 1 })
    assert.equal(result.ok, true)
    assert.equal(result.char_name, '安吉拉')
    assert.equal(result.replaced_placeholders, 2)
    assert.ok(result.raw_chars > result.sanitized_chars)
    assert.equal(result.chars, result.sanitized_chars)
    const persona = entry.options.config.plugins.find((row) => row.name === PERSONA_PLUGIN_NAME)
    assert.match(persona.config.prefix, /你是「您」，我是「安吉拉」/)
    const status = JSON.parse(readFileSync(statusPath(dir), 'utf8'))
    assert.equal(status.raw_chars, result.raw_chars)
    assert.equal(status.sanitized_chars, result.sanitized_chars)
    assert.equal(status.char_name, '安吉拉')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('apply strips unknown placeholders and still rejects malformed braces', async () => {
  const dir = tempDir()
  try {
    const { editor, presets } = fixture('ptc')
    const ok = await applyPersonaPreset({ editor, presets, dataDir: dir, text: '保留 {{foo}} 去掉大括号', now: 1 })
    assert.equal(ok.ok, true)
    const unsafe = await applyPersonaPreset({ editor, presets, dataDir: dir, text: '坏 {{未闭合', now: 2 })
    assert.equal(unsafe.ok, false)
    assert.equal(unsafe.reason, 'template_unsafe')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('apply rejects empty persona without creating a backup', async () => {
  const dir = tempDir()
  try {
    const { editor, presets } = fixture('ptc')
    const empty = await applyPersonaPreset({ editor, presets, dataDir: dir, text: '   ' })
    assert.equal(empty.reason, 'empty_persona')
    assert.equal(existsSync(backupPath(dir)), false)
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('apply refuses without the host configEditor', async () => {
  const dir = tempDir()
  try {
    const out = await applyPersonaPreset({ editor: null, dataDir: dir, text: 'x' })
    assert.equal(out.ok, false)
    assert.equal(out.reason, 'host_unavailable')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('apply refuses when the preset entry is missing', async () => {
  const dir = tempDir()
  try {
    const editor = { documentPath: 'p', entries: () => [], edit: async () => {} }
    const out = await applyPersonaPreset({ editor, presets: { defaultId: 'ptc' }, dataDir: dir, text: 'x' })
    assert.equal(out.reason, 'no_preset_entry')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('rollback restores the original preset config', async () => {
  const dir = tempDir()
  try {
    const { entry, memory, editor, presets } = fixture('ptc')
    await applyPersonaPreset({ editor, presets, dataDir: dir, text: 'new persona', now: 1 })
    assert.notEqual(entry.options.config.plugins[0].config.prefix, OLD_PREFIX)
    assert.equal(memory.options.config.personaFile, '')
    const out = await rollbackPersonaPreset({ editor, dataDir: dir, now: 2 })
    assert.equal(out.ok, true)
    assert.equal(out.reason, 'rolled_back')
    assert.equal(entry.options.config.plugins[0].config.prefix, OLD_PREFIX)
    assert.equal(memory.options.config.personaFile, 'C:/bio/persona.md')
    const status = JSON.parse(readFileSync(statusPath(dir), 'utf8'))
    assert.equal(status.applied, false)
    assert.equal(status.injection, 'on')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('apply turns off the memory plugin persona injection with backup', async () => {
  const dir = tempDir()
  try {
    const { memory, editor, presets } = fixture('ptc')
    const out = await applyPersonaPreset({ editor, presets, dataDir: dir, text: 'x persona', now: 1 })
    assert.equal(out.injection_disabled, true)
    assert.equal(out.injection_detail.reason, 'disabled')
    assert.equal(memory.options.config.personaFile, '')
    const backup = JSON.parse(readFileSync(backupPath(dir), 'utf8'))
    assert.equal(backup.injection_backup.persona_file, 'C:/bio/persona.md')
    assert.equal(backup.injection_backup.entry_id, 'dsh-biocore-memory')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('rollback without backup is refused', async () => {
  const dir = tempDir()
  try {
    const { editor } = fixture('ptc')
    const out = await rollbackPersonaPreset({ editor, dataDir: dir })
    assert.equal(out.reason, 'no_backup')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('status reports drift and default-preset facts', async () => {
  const dir = tempDir()
  try {
    const { entry, editor, presets } = fixture('ptc')
    const before = personaPresetStatus(editor, presets, dir)
    assert.equal(before.applied, false)
    assert.equal(before.reason, 'not_applied')
    await applyPersonaPreset({ editor, presets, dataDir: dir, text: 'stable persona', now: 1 })
    const applied = personaPresetStatus(editor, presets, dir)
    assert.equal(applied.applied, true)
    assert.equal(applied.matches_preset, true)
    assert.equal(applied.is_default_preset, true)
    assert.equal(applied.reason, 'applied')
    entry.options.config.plugins[0].config.prefix = 'changed elsewhere'
    const drift = personaPresetStatus(editor, presets, dir)
    assert.equal(drift.applied, true)
    assert.equal(drift.matches_preset, false)
    assert.equal(drift.reason, 'drift')
    assert.equal(personaHash('x').length, 16)
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('readPersonaText prefers persona_profile.json then persona.md', () => {
  const dir = tempDir()
  try {
    writeFileSync(join(dir, 'persona.md'), 'md persona', 'utf8')
    assert.equal(readPersonaText(dir), 'md persona')
    writeFileSync(join(dir, 'persona_profile.json'), JSON.stringify({ persona: 'json persona' }), 'utf8')
    assert.equal(readPersonaText(dir), 'json persona')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('GET route returns the status payload', async () => {
  const dir = tempDir()
  try {
    writeFileSync(join(dir, 'persona.md'), 'persona body', 'utf8')
    const res = fakeRes()
    const req = new EventEmitter(); req.method = 'GET'
    await handlePersonaApply(req, res, { dataDir: dir, ctx: null })
    assert.equal(res.code, 200)
    assert.equal(res.body.ok, true)
    assert.equal(res.body.reason, 'not_applied')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('POST route applies through configEditor with confirmation', async () => {
  const dir = tempDir()
  try {
    writeFileSync(join(dir, 'persona.md'), '她是灰羽，安静但黏人。', 'utf8')
    const { editor, presets } = fixture('ptc')
    const ctx = { get: (name) => ({ configEditor: editor, agentPresets: presets }[name] || null) }
    const res = fakeRes()
    await handlePersonaApply(postReq({ confirm: true }), res, { dataDir: dir, ctx })
    assert.equal(res.code, 200)
    assert.equal(res.body.ok, true)
    assert.equal(res.body.applied, true)
    assert.equal(res.body.reason, 'applied')
    assert.equal(res.body.preset_id, 'ptc')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('POST route requires confirm and rejects missing persona', async () => {
  const dir = tempDir()
  try {
    const ctx = { get: () => null }
    const noConfirm = fakeRes()
    await handlePersonaApply(postReq({}), noConfirm, { dataDir: dir, ctx })
    assert.equal(noConfirm.code, 400)
    assert.equal(noConfirm.body.reason, 'confirm_required')
    const noPersona = fakeRes()
    await handlePersonaApply(postReq({ confirm: true }), noPersona, { dataDir: dir, ctx })
    assert.equal(noPersona.code, 400)
    assert.equal(noPersona.body.reason, 'empty_persona')
    const noHost = fakeRes()
    writeFileSync(join(dir, 'persona.md'), 'x', 'utf8')
    await handlePersonaApply(postReq({ confirm: true }), noHost, { dataDir: dir, ctx })
    assert.equal(noHost.code, 409)
    assert.equal(noHost.body.reason, 'host_unavailable')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('POST route rolls back with rollback:true', async () => {
  const dir = tempDir()
  try {
    writeFileSync(join(dir, 'persona.md'), 'persona', 'utf8')
    const { entry, editor, presets } = fixture('ptc')
    const ctx = { get: (name) => ({ configEditor: editor, agentPresets: presets }[name] || null) }
    await handlePersonaApply(postReq({ confirm: true }), fakeRes(), { dataDir: dir, ctx })
    assert.notEqual(entry.options.config.plugins[0].config.prefix, OLD_PREFIX)
    const res = fakeRes()
    await handlePersonaApply(postReq({ confirm: true, rollback: true }), res, { dataDir: dir, ctx })
    assert.equal(res.code, 200)
    assert.equal(res.body.reason, 'rolled_back')
    assert.equal(entry.options.config.plugins[0].config.prefix, OLD_PREFIX)
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
