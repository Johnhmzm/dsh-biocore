/** Self-test for dsh-biocore-ui (no browser; python bridge is expected here).
 *
 * Covers: projection, per-session state paths, /state degradation reasons,
 * lazy creation, shared data dir, /bio sync handler, session/event listener wiring,
 * flags.json switch persistence, client module load + pure panel render.
 *
 * Run: node test/selftest.mjs
 */
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const stateMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'state.js')).href)
const bridge = await import(pathToFileURL(join(root, 'lib', 'py-bridge.js')).href)
const host = await import(pathToFileURL(join(root, 'lib', 'index.js')).href)
const seriesRoute = await import(pathToFileURL(join(root, 'lib', 'modules', 'series.js')).href)
const coreTurn = await import(pathToFileURL(join(root, 'lib', 'modules', 'core-turn.js')).href)
const expressionInject = await import(pathToFileURL(join(root, 'lib', 'modules', 'expression-inject.js')).href)
const { cleanSessionId, projectState, readStateFile, sessionStateFile, payloadWithinLimit } = stateMod
const { parseSyncInput, summarizeTurn, makeCommandHandler, makeSessionEventListener } = host

// Keep shared-persona tests away from the real <home>/.dsh/biocore directory.
if (!process.env.BIOCORE_SHARED_PERSONA_DIR) {
  process.env.BIOCORE_SHARED_PERSONA_DIR = join(tmpdir(), 'biocore-ui-shared-persona-test')
}

const TEST_HOME = mkdtempSync(join(tmpdir(), 'biocore-ui-test-home-'))

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }
const tick = () => new Promise((resolve) => setImmediate(resolve))

const sample = {
  now: 1762300000,
  turn: 4,
  days_known: 60,
  core: {
    v: 0.42, social: 0.58, stress: 0.24,
    intero: { tension: 0.70, warmth: 0.20, heaviness: 0.50, clarity: 0.40 },
  },
  relationship: { closeness: 0.62, trust: 0.55, conflict: 0.05 },
  autonomy: { ready: 0.52 },
  memory: { count: 3 },
}

/* ---------- projection ---------- */
const body = projectState(sample, { mtimeMs: 1762300123000, sessionId: 'abc' })
assert.equal(body.available, true)
assert.equal(body.session_id, 'abc')
assert.equal(body.v, 0.42)
assert.deepEqual(body.intero, { tension: 0.7, warmth: 0.2, heaviness: 0.5, clarity: 0.4 })
assert.equal(body.tone.label, '先接住情�?)
assert.ok(['陌生', '熟悉', '亲近', '稳定'].includes(body.relation.stage))
assert.ok(['�?, '�?, '想联�?, '冷却'].includes(body.autonomy.state))
assert.equal(body.memory_count, 3)
for (const key of ['available', 'session_id', 'v', 'intero', 'tone', 'relation',
  'autonomy', 'memory_count', 'updated_at']) {
  assert.ok(key in body, 'missing ' + key)
}
assert.ok(payloadWithinLimit(body))
ok('projectState returns the documented subset (incl. session_id)')

/* ---------- session paths ---------- */
assert.equal(sessionStateFile('/tmp/state', 's-1'), join('/tmp/state', 'sessions', 's-1.json'))
assert.equal(cleanSessionId('s-1_a.b'), 's-1_a.b')
assert.equal(cleanSessionId('../evil'), null)
assert.equal(cleanSessionId('a/b'), null)
assert.equal(cleanSessionId(''), null)
assert.equal(cleanSessionId(null), null)
ok('per-session file paths are <stateDir>/sessions/<id>.json and ids are sanitized')

/* ---------- shared data directory (task-197) ---------- */
const dataDirMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'data-dir.js')).href)
const switchForDir = await import(pathToFileURL(join(root, 'lib', 'modules', 'switch.js')).href)
const { resolveDataDir, isProfileDir, PLUGIN_DATA_DIR } = dataDirMod
{
  const savedProfile = process.env.DSH_PROFILE_DIR
  const savedUiData = process.env.BIOCORE_UI_DATA_DIR
  const dirTmp = mkdtempSync(join(tmpdir(), 'biocore-ui-datadir-'))
  try {
    delete process.env.DSH_PROFILE_DIR
    delete process.env.BIOCORE_UI_DATA_DIR
    assert.equal(resolveDataDir({}, process.env, process.argv, TEST_HOME), PLUGIN_DATA_DIR)
    assert.equal(dirname(sessionStateFile(PLUGIN_DATA_DIR, 'fallback')),
      join(PLUGIN_DATA_DIR, 'sessions'))
    ok('data dir: no config/env -> <plugin>/data fallback (sessions too)')

    const goodProfile = join(dirTmp, 'profile-good')
    mkdirSync(goodProfile, { recursive: true })
    writeFileSync(join(goodProfile, 'package.json'), '{}', 'utf8')
    process.env.DSH_PROFILE_DIR = goodProfile
    assert.ok(isProfileDir(goodProfile))
    assert.equal(resolveDataDir({}, process.env, process.argv, TEST_HOME), join(goodProfile, 'data'))

    const ymlProfile = join(dirTmp, 'profile-yml')
    mkdirSync(ymlProfile, { recursive: true })
    writeFileSync(join(ymlProfile, 'dsh.yml'), '[]\n', 'utf8')
    process.env.DSH_PROFILE_DIR = ymlProfile
    assert.equal(resolveDataDir({}, process.env, process.argv, TEST_HOME), join(ymlProfile, 'data'))
    ok('data dir: valid DSH_PROFILE_DIR (package.json or dsh.yml) -> <profile>/data')

    const badProfile = join(dirTmp, 'profile-bad')
    mkdirSync(badProfile, { recursive: true })
    process.env.DSH_PROFILE_DIR = badProfile
    assert.equal(resolveDataDir({}, process.env, process.argv, TEST_HOME), PLUGIN_DATA_DIR)
    process.env.DSH_PROFILE_DIR = join(dirTmp, 'profile-missing')
    assert.equal(resolveDataDir({}, process.env, process.argv, TEST_HOME), PLUGIN_DATA_DIR)
    ok('data dir: DSH_PROFILE_DIR without manifest -> <plugin>/data')

    const envDir = join(dirTmp, 'env-data')
    process.env.BIOCORE_UI_DATA_DIR = envDir
    assert.equal(resolveDataDir({}, process.env, process.argv, TEST_HOME), envDir)
    const cfgDir = join(dirTmp, 'cfg-data')
    assert.equal(resolveDataDir({ dataDir: cfgDir }, process.env, process.argv, TEST_HOME), cfgDir)
    delete process.env.DSH_PROFILE_DIR
    delete process.env.BIOCORE_UI_DATA_DIR
    ok('data dir: config.dataDir > BIOCORE_UI_DATA_DIR > profile dir > plugin dir')

    const sharedDir = join(dirTmp, 'shared')
    const dataDir = resolveDataDir({ dataDir: sharedDir }, process.env, process.argv, TEST_HOME)
    const flagsFile = switchForDir.flagsFilePath(dataDir)
    const sessionFile = sessionStateFile(dataDir, 'sess-shared')
    assert.equal(dirname(flagsFile), dataDir)
    assert.equal(sessionFile, join(dataDir, 'sessions', 'sess-shared.json'))
    assert.ok(flagsFile.startsWith(dataDir))
    assert.ok(sessionFile.startsWith(dataDir))
    ok('data dir: flags.json and sessions/ resolve under the same directory')
  } finally {
    if (savedProfile === undefined) delete process.env.DSH_PROFILE_DIR
    else process.env.DSH_PROFILE_DIR = savedProfile
    if (savedUiData === undefined) delete process.env.BIOCORE_UI_DATA_DIR
    else process.env.BIOCORE_UI_DATA_DIR = savedUiData
    rmSync(dirTmp, { recursive: true, force: true })
  }
}

/* ---------- data dir: --profile from argv (task-201) ---------- */
{
  const homeTmp = mkdtempSync(join(tmpdir(), 'biocore-ui-home-'))
  try {
    const profileRoot = join(homeTmp, '.dsh', 'profiles', 'web-bio')
    const defaultProfile = join(homeTmp, '.dsh', 'profiles', 'web')
    mkdirSync(profileRoot, { recursive: true })
    mkdirSync(defaultProfile, { recursive: true })
    writeFileSync(join(profileRoot, 'package.json'), '{}', 'utf8')
    writeFileSync(join(defaultProfile, 'package.json'), '{}', 'utf8')

    assert.equal(dataDirMod.profileFromArgv(['node', 'bin.js', '--profile', 'web-bio', '--port', '3081']), 'web-bio')
    assert.equal(dataDirMod.profileFromArgv(['node', 'bin.js', '--profile=web-bio']), 'web-bio')
    assert.equal(dataDirMod.profileFromArgv(['node', 'bin.js', '--port', '3081']), '')
    assert.equal(dataDirMod.profileFromArgv(['--profile']), '')
    assert.equal(dataDirMod.profileFromArgv(['--profile=']), '')
    ok('data dir: --profile / --profile= parsing')

    const resolved = resolveDataDir(
      { dataDir: join(homeTmp, 'cfg') },
      { BIOCORE_UI_DATA_DIR: join(homeTmp, 'env'), DSH_PROFILE_DIR: defaultProfile },
      ['node', 'bin.js', '--profile', 'web-bio', '--port', '3081'],
      homeTmp)
    assert.equal(resolved, join(profileRoot, 'data'))
    ok('data dir: --profile wins over config/env/DSH_PROFILE_DIR')

    const configOnly = resolveDataDir(
      { dataDir: join(homeTmp, 'cfg') },
      { DSH_PROFILE_DIR: defaultProfile },
      ['node', 'bin.js', '--port', '3081'],
      homeTmp)
    assert.equal(configOnly, resolve(join(homeTmp, 'cfg')))
    ok('data dir: argv without --profile -> config.dataDir still wins')

    const missingProfile = resolveDataDir(
      {},
      { DSH_PROFILE_DIR: defaultProfile },
      ['node', 'bin.js', '--profile', 'ghost-profile'],
      homeTmp)
    assert.equal(missingProfile, join(defaultProfile, 'data'))
    ok('data dir: --profile pointing at a missing profile dir falls back')
  } finally {
    rmSync(homeTmp, { recursive: true, force: true })
  }
}

/* ---------- /state degradation ---------- */
function fakeRes() {
  return { code: 0, headers: null, body: null,
    writeHead(code, headers) { this.code = code; this.headers = headers },
    end(payload) { this.body = payload } }
}
async function getState(url, stateDir) {
  const res = fakeRes()
  await host.handleState({ method: 'GET', url }, res, stateDir)
  return { code: res.code, body: res.body ? JSON.parse(res.body) : null, headers: res.headers }
}
const tmp = mkdtempSync(join(tmpdir(), 'biocore-ui-'))
try {
  const noSession = await getState('/api/biocore/state', join(tmp, 'state'))
  assert.equal(noSession.code, 200)
  assert.equal(noSession.body.available, false)
  assert.equal(noSession.body.reason, 'no_session')
  ok('GET without session -> available:false reason no_session')

  const badSession = await getState('/api/biocore/state?session=..%2Fevil', join(tmp, 'state'))
  assert.equal(badSession.body.reason, 'bad_session')
  ok('GET with unsafe session -> reason bad_session')

  const stateDir = join(tmp, 'state')
  const disabledRes = fakeRes()
  await host.handleState(
    { method: 'GET', url: '/api/biocore/state?session=selftest-a' },
    disabledRes, stateDir,
    { store: join(stateDir, 'memory'),
      runAction: async () => ({ ok: true, state: { available: false, reason: 'core_disabled' } }) },
  )
  const disabledBody = JSON.parse(disabledRes.body)
  assert.equal(disabledBody.available, false)
  assert.equal(disabledBody.reason, 'core_disabled')
  ok('/state is a pure forward: core-disabled answer passes through')

  const fileA = sessionStateFile(stateDir, 'selftest-a')
  const fileB = sessionStateFile(stateDir, 'selftest-b')
  await bridge.ensureState(fileA)
  await bridge.ensureState(fileB)
  const first = await getState('/api/biocore/state?session=selftest-a', stateDir)
  assert.equal(first.body.available, true)
  assert.equal(first.body.session_id, 'selftest-a')
  assert.equal(first.body.source, 'core')
  await getState('/api/biocore/state?session=selftest-b', stateDir)
  assert.notEqual(fileA, fileB)
  ok('/state forwards the core payload for any session')

  const runtimeState = { available: true, core: { v: 0.2, intero: { tension: 0.3, warmth: 0.5, heaviness: 0.2, clarity: 0.6 } }, relationship: { closeness: 0.5, trust: 0.5, conflict: 0 }, memory: { count: 0 } }
  const runtimeRes = fakeRes()
  await host.handleState(
    { method: 'GET', url: '/api/biocore/state?session=runtime-only' },
    runtimeRes,
    stateDir,
    { getSwitches: () => ({ safety_enabled: true }), runAction: async () => ({ ok: true, state: runtimeState }) },
  )
  const runtimeBody = JSON.parse(runtimeRes.body)
  assert.equal(runtimeBody.available, true)
  assert.equal(runtimeBody.core.v, 0.2)
  assert.equal(runtimeBody.core.intero.tension, 0.3)
  ok('/state returns the core payload unchanged')

  const emptySeries = fakeRes()
  await seriesRoute.handleSeriesRoute(
    { method: 'GET', url: '/api/biocore/series?limit=24' },
    emptySeries,
    { store: join(tmp, 'memory'), dataDir: tmp, runAction: async () => ({ ok: true, series: { v: [] }, points: [] }) },
  )
  const emptyBody = JSON.parse(emptySeries.body)
  assert.equal(emptySeries.code, 200)
  assert.equal(emptyBody.ok, true)
  assert.deepEqual(emptyBody.series.v, [])
  assert.deepEqual(emptyBody.points, [])
  ok('task-221: /series answers 200 with [] on an empty store')

  writeFileSync(fileA, '{broken json', 'utf8')
  const broken = await getState('/api/biocore/state?session=selftest-a', stateDir)
  assert.equal(broken.body.available, true)
  assert.equal(broken.body.source, 'core')
  rmSync(fileA, { force: true })
  ok('legacy session file (even corrupt) no longer blocks /state')
} finally {
  rmSync(tmp, { recursive: true, force: true })
}

/* ---------- python bridge + real one_turn ---------- */
const tmp2 = mkdtempSync(join(tmpdir(), 'biocore-ui-py-'))
try {
  const stateFile = sessionStateFile(tmp2, 'bridge-1')
  const ensured = await bridge.ensureState(stateFile)
  assert.equal(ensured.ok, true, JSON.stringify(ensured))
  assert.ok(existsSync(stateFile))
  const turned = await bridge.runTurn(stateFile, '谢谢你，今天好多�?)
  assert.equal(turned.ok, true, JSON.stringify(turned))
  assert.ok(turned.after, 'turn summary has after state')
  assert.ok(turned.reply_hint && 'length_scale' in turned.reply_hint)
  assert.ok(turned.after.v >= turned.before.v, 'praise does not lower v')
  const saved = JSON.parse(readFileSync(stateFile, 'utf8'))
  assert.equal(saved.turn, 1)
  ok('python bridge ensure/turn calls dsh_adapter one_turn and saves state')
} finally {
  rmSync(tmp2, { recursive: true, force: true })
}

/* ---------- /bio sync command ---------- */
assert.deepEqual(parseSyncInput(' sync 今天好累 '), { ok: true, text: '今天好累' })
assert.equal(parseSyncInput('').ok, false)
assert.equal(parseSyncInput('nope').ok, false)
const fakeTurnResult = {
  ok: true,
  before: { v: 0, stress: 0.2 },
  after: { v: 0.06, stress: 0.24 },
  reply_hint: { length_scale: 1, initiative: 0.46, allow_correction: false },
}
const tmp3 = mkdtempSync(join(tmpdir(), 'biocore-ui-cmd-'))
try {
  const calls = []
  const handler = makeCommandHandler({
    stateDir: tmp3,
    runTurn: async (file, text) => { calls.push({ file, text }); return fakeTurnResult },
  })
  const success = await handler({ rawInput: ' sync 今天好累 �?, agent: { session: { id: 'sess-cmd-1' } } })
  assert.equal(success.kind, 'success')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].file, sessionStateFile(tmp3, 'sess-cmd-1'))
  assert.ok(success.text.includes('v 0.00�?.06'))
  const usage = await handler({ rawInput: '', agent: { session: { id: 's' } } })
  assert.equal(usage.kind, 'error')
  const noSession = await handler({ rawInput: ' sync hi', agent: {} })
  assert.equal(noSession.kind, 'error')
  assert.ok(summarizeTurn(fakeTurnResult).includes('压力 0.20�?.24'))

  const realHandler = makeCommandHandler({ stateDir: tmp3 })
  const real = await realHandler({
    rawInput: ' sync 谢谢你，今天好多�?,
    agent: { session: { id: 'sess-real-' + Date.now() } },
  })
  assert.equal(real.kind, 'success', JSON.stringify(real))
  assert.ok(real.text.includes('session sess-rea'))
  ok('/bio sync handler: success summary, usage error, no_session error, real bridge run')
} finally {
  rmSync(tmp3, { recursive: true, force: true })
}

/* ---------- session/event listener ---------- */
{
  const calls = []
  const listener = makeSessionEventListener({
    stateDir: '/tmp/state',
    runTurn: async (file, text) => { calls.push({ file, text }); return { ok: true } },
  })
  listener({ id: 's-hook-1' }, {
    type: 'user/message',
    data: { source: { kind: 'user' }, content: [{ type: 'text', text: '今天好累' }] },
  })
  await tick()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].file, sessionStateFile('/tmp/state', 's-hook-1'))
  assert.equal(calls[0].text, '今天好累')
  listener({ id: 's-hook-1' }, {
    type: 'user/message',
    data: { source: { kind: 'runtime-context' }, content: [{ type: 'text', text: 'system' }] },
  })
  listener({ id: 's-hook-1' }, {
    type: 'assistant/message', data: { content: [{ type: 'text', text: 'hi' }] },
  })
  listener({ id: '../bad' }, {
    type: 'user/message',
    data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'x' }] },
  })
  await tick()
  assert.equal(calls.length, 1)
  ok('session/event listener only forwards direct human user/message text')
}

/* ---------- four switches: flags.json file persistence ---------- */
const switchMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'switch.js')).href)

function fakeSwitchReq(method, body) {
  const handlers = {}
  return {
    method,
    on(event, fn) { (handlers[event] = handlers[event] || []).push(fn) },
    destroy() {},
    emit() {
      for (const fn of handlers.data || []) fn(Buffer.from(body === undefined ? '' : body))
      for (const fn of handlers.end || []) fn()
    },
  }
}

async function callSwitch(handler, method, payload) {
  const req = fakeSwitchReq(method, payload === undefined ? undefined : JSON.stringify(payload))
  const res = fakeRes()
  const pending = handler(req, res)
  req.emit()
  await pending
  return { code: res.code, body: res.body ? JSON.parse(res.body) : null, headers: res.headers }
}

assert.deepEqual(Array.from(switchMod.SWITCH_KEYS),
  ['core_enabled', 'safety_enabled', 'boundary_enabled', 'crisis_safety_enabled',
    'proactive_send', 'mood_recall', 'affect_encoding', 'memory_feedback',
    'preference_drift'])
assert.equal(switchMod.sanitizeSwitchRequest({ key: 'db_password', value: true, confirm: true }).reason, 'unknown_key')
assert.equal(switchMod.sanitizeSwitchRequest({ key: 'safety_enabled', value: true }).reason, 'confirm_required')
assert.equal(switchMod.sanitizeSwitchRequest({ key: 'safety_enabled', value: true, confirm: 'safety_enabled' }).reason, 'confirm_required')
assert.equal(switchMod.sanitizeSwitchRequest({ key: 'safety_enabled', value: 1, confirm: true }).reason, 'bad_value')
assert.deepEqual(Array.from(Object.values(switchMod.DEFAULT_SWITCHES)),
  [true, false, false, false, false, true, true, true, true])
assert.equal(switchMod.readSwitchValues({ proactive_autonomy: true }).proactive_send, true)
assert.equal(switchMod.readSwitchValues({ proactive_send: false, proactive_autonomy: true }).proactive_send, false)
assert.equal(switchMod.sanitizeSwitchRequest({ key: 'proactive_autonomy', value: true, confirm: true }).reason, 'unknown_key')
ok('switch whitelist rejects unknown key / missing confirm / non-boolean value (defaults false, read alias)')

{
  const libDir = join(root, 'lib')
  const files = ['index.js', 'py-bridge.js', 'core-worker.js',
    ...readdirSync(join(libDir, 'modules')).filter((name) => name.endsWith('.js'))
      .map((name) => join('modules', name))]
  const offenders = []
  for (const file of files) {
    const text = readFileSync(join(libDir, file), 'utf8')
    for (const needle of ['anySwitchOn', 'mergeCoreState']) {
      if (text.includes(needle)) offenders.push(file + ':' + needle)
    }
  }
  assert.deepEqual(offenders, [])
  ok('contract: adapter has no switch gating or state-merge logic')
}

{
  const calls = []
  const runAction = async (action) => {
    calls.push(action)
    return action === 'turn_start' ? { ok: true, prompt_block: 'state' } : { ok: true }
  }
  const event = {
    type: 'user/message',
    data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'hello there' }] },
  }
  const on = coreTurn.makeCoreSessionListener({
    stateDir: join(tmp, 'core-on'),
    getSwitches: () => ({ core_enabled: false, safety_enabled: false }),
    runAction,
    runTurn: async () => ({}),
    store: join(tmp, 'core-on-memory'),
  })
  on({ id: 'core-on-session' }, event)
  await tick()
  assert.deepEqual(calls, ['turn_start'])
  ok('adapter forwards one turn_start per user message (core decides switching)')
}

{
  const dataDir = mkdtempSync(join(tmpdir(), 'biocore-ui-switch-file-'))
  try {
    const flagsPath = join(dataDir, 'flags.json')
    const handler = (req, res) => switchMod.handleSwitchRoute(req, res, { dataDir })
    assert.equal(switchMod.flagsFilePath(dataDir), flagsPath)

    const missing = await callSwitch(handler, 'GET')
    assert.equal(missing.code, 200)
    assert.equal(missing.body.ok, true)
    assert.deepEqual(missing.body.switches, {
      core_enabled: true,
      safety_enabled: false, boundary_enabled: false,
      crisis_safety_enabled: false, proactive_send: false,
      mood_recall: true, affect_encoding: true,
      memory_feedback: true, preference_drift: true,
    })
    assert.equal(existsSync(flagsPath), false)
    ok('GET /api/biocore/switch with no flags.json -> core+learning on, safety off, no file')

    const unknown = await callSwitch(handler, 'POST', { key: 'db_password', value: true, confirm: true })
    assert.equal(unknown.code, 400)
    assert.equal(unknown.body.reason, 'unknown_key')
    assert.equal(existsSync(flagsPath), false)
    const missingConfirm = await callSwitch(handler, 'POST', { key: 'safety_enabled', value: true })
    assert.equal(missingConfirm.code, 400)
    assert.equal(missingConfirm.body.reason, 'confirm_required')
    assert.equal(existsSync(flagsPath), false)
    ok('POST rejects unknown key / missing confirm without writing flags.json')

    const written = await callSwitch(handler, 'POST',
      { key: 'safety_enabled', value: true, confirm: true })
    assert.equal(written.code, 200)
    assert.equal(written.body.ok, true)
    assert.equal(written.body.key, 'safety_enabled')
    assert.equal(written.body.value, true)
    assert.equal(written.body.switches.safety_enabled, true)
    assert.equal(existsSync(flagsPath), true)
    assert.equal(existsSync(flagsPath + '.tmp'), false)
    const stored = JSON.parse(readFileSync(flagsPath, 'utf8'))
    assert.deepEqual(stored, {
      core_enabled: true,
      safety_enabled: true, boundary_enabled: false,
      crisis_safety_enabled: false, proactive_send: false,
      mood_recall: true, affect_encoding: true,
      memory_feedback: true, preference_drift: true,
    })
    ok('POST writes <dataDir>/flags.json atomically (read back, no .tmp residue)')

    const second = await callSwitch(handler, 'POST',
      { key: 'proactive_send', value: true, confirm: true })
    assert.equal(second.code, 200)
    const reread = JSON.parse(readFileSync(flagsPath, 'utf8'))
    assert.equal(reread.safety_enabled, true)
    assert.equal(reread.proactive_send, true)
    assert.equal(existsSync(flagsPath + '.tmp'), false)
    const read = await callSwitch(handler, 'GET')
    assert.equal(read.body.switches.safety_enabled, true)
    assert.equal(read.body.switches.proactive_send, true)
    assert.equal(read.body.switches.boundary_enabled, false)
    ok('GET reads persisted flags.json and keeps other switches untouched')

    writeFileSync(flagsPath, '{ not json', 'utf8')
    const broken = await callSwitch(handler, 'GET')
    assert.equal(broken.code, 200)
    assert.equal(broken.body.ok, true)
    assert.equal(broken.body.switches.safety_enabled, false)
    ok('GET with malformed flags.json degrades to defaults (no 500/409)')

    const blocked = join(dataDir, 'blocked')
    writeFileSync(blocked, 'x', 'utf8')
    const failed = await callSwitch(
      (req, res) => switchMod.handleSwitchRoute(req, res, { dataDir: blocked }),
      'POST', { key: 'boundary_enabled', value: true, confirm: true })
    assert.equal(failed.code, 500)
    assert.equal(failed.body.reason, 'write_failed')
    ok('POST reports write_failed (500) when the data dir is not writable')
  } finally {
    rmSync(dataDir, { recursive: true, force: true })
  }
}

{
  const previous = process.env.DSH_PROFILE_DIR
  try {
    const profile = mkdtempSync(join(tmpdir(), 'biocore-ui-profile-'))
    // task-197: a bare DSH_PROFILE_DIR only counts when it has a profile manifest.
    writeFileSync(join(profile, 'package.json'), '{}', 'utf8')
    process.env.DSH_PROFILE_DIR = profile
    assert.equal(switchMod.resolveDataDir({}, process.env, process.argv, TEST_HOME), join(profile, 'data'))
    assert.equal(switchMod.resolveDataDir({ dataDir: join(profile, 'custom') }),
      join(profile, 'custom'))
    rmSync(profile, { recursive: true, force: true })
  } finally {
    if (previous === undefined) delete process.env.DSH_PROFILE_DIR
    else process.env.DSH_PROFILE_DIR = previous
  }
  ok('dataDir resolution: config override wins, otherwise <DSH_PROFILE_DIR>/data')
}

{
  const dataDir = mkdtempSync(join(tmpdir(), 'biocore-ui-switch-wire-'))
  try {
    const registered = []
    const fakeHostCtx = {
      on() { return () => {} },
      inject(names, callback) {
        if (names.includes('webServer')) {
          callback({ webServer: {
            register(entry) { registered.push(entry); return () => {} },
          } })
        } else if (names.includes('commands')) {
          callback({ commands: { register() { return () => {} } } })
        }
      },
    }
    const dispose = host.apply(fakeHostCtx, { dataDir })
    assert.equal(typeof dispose, 'function')
    const paths = registered.map((entry) => entry.path)
    assert.ok(paths.includes('/api/biocore/state'))
    assert.ok(paths.includes('/api/biocore/switch'))
    const entry = registered.find((item) => item.path === '/api/biocore/switch')
    assert.equal(entry.kind, 'exact')
    const wired = await callSwitch(entry.handler, 'POST',
      { key: 'crisis_safety_enabled', value: true, confirm: true })
    assert.equal(wired.code, 200)
    const stored = JSON.parse(readFileSync(join(dataDir, 'flags.json'), 'utf8'))
    assert.equal(stored.crisis_safety_enabled, true)
    dispose()
    ok('host apply registers /api/biocore/switch and persists via config.dataDir')
  } finally {
    rmSync(dataDir, { recursive: true, force: true })
  }
}

/* ---------- client module ---------- */
const fakeReact = {
  createElement(type, props, ...children) {
    return { type, props: Object.assign({}, props || {}, { children }) }
  },
  useState(initial) {
    return [typeof initial === 'function' ? initial() : initial, () => {}]
  },
  useEffect(run) { const cleanup = run(); return cleanup },
}
const tabs = []
const slots = []
const clientCtx = {
  effect(run) { return run() },
  sidebarRightTabs: { register(definition) { tabs.push(definition); return () => {} } },
  slots: {
    inject(name, callback) { callback() },
    register(definition) { slots.push(definition); return () => {} },
  },
}
globalThis.window = {
  __ModuleLoader__: {
    load(definition) {
      clientCtx.exports = definition.factory((id) => {
        if (id === 'react') return fakeReact
        throw new Error('unexpected require ' + id)
      })
    },
  },
}
await import(pathToFileURL(join(root, 'lib', 'client.js')).href + '?t=' + Date.now())
clientCtx.exports.apply(clientCtx)
assert.equal(tabs.length, 1)
assert.equal(tabs[0].kind, 'biocore')
assert.equal(tabs[0].title(), '生物�?)
const tabSlot = slots.find((item) => item.name === 'sidebar.right.pane.tab')
assert.ok(tabSlot, 'pane tab slot registered')
assert.deepEqual(tabSlot.inject('session-abcdef'), { sessionId: 'session-abcdef' })
const render = clientCtx.exports.__test.renderPanel
const tree = JSON.stringify(render(body, false, 'sess-1234abcd'))
for (const needle of ['生物�?, '绷紧', '�?, '�?, '�?, '语气', '关系', '自主',
  '已连�?, '会话 sess-123']) {
  assert.ok(tree.includes(needle), 'render missing ' + needle)
}
assert.ok(JSON.stringify(render(null, true, 'sess-1')).includes('连不上宿�?))
assert.ok(JSON.stringify(render({ available: false, reason: 'bad_state' }, false, 's')).includes('状态文件坏�?))
assert.ok(JSON.stringify(render({ available: true, v: 0 }, false, '')).includes('未连�?))
ok('client renders 5 numbers + 3 words, session label, 未连�?状态损�?states')


/* ---------- task-213: left sidebar entry + main iframe panel ---------- */
{
  const testApi = clientCtx.exports.__test
  const mainSlot = slots.find((item) => item.name === 'main')
  const listSlot = slots.find((item) => item.name === 'sidebar.panellist')
  assert.ok(mainSlot, 'main keyed slot registered')
  assert.equal(mainSlot.key, testApi.MAIN_PANEL_KEY)
  assert.equal(testApi.MAIN_PANEL_KEY, 'biocore-panel')
  assert.ok(listSlot, 'sidebar.panellist entry registered')
  assert.equal(listSlot.id, testApi.MAIN_PANEL_KEY)
  assert.equal(listSlot.label(), 'BioCore')
  assert.equal(typeof listSlot.order, 'number')
  ok('left sidebar: main keyed slot + sidebar.panellist entry registered')

  assert.equal(testApi.panelIframeUrl('session-abcdef'),
    '/api/biocore-ui/ui/panel.html?session=session-abcdef')
  assert.equal(testApi.panelIframeUrl(''), '/api/biocore-ui/ui/panel.html')
  assert.equal(testApi.panelIframeUrl(null), '/api/biocore-ui/ui/panel.html')
  const withSession = JSON.stringify(testApi.mainPanelIframe('session-x'))
  assert.ok(withSession.includes('/api/biocore-ui/ui/panel.html?session=session-x'))
  const noSession = JSON.stringify(testApi.mainPanelIframe(''))
  assert.ok(noSession.includes('/api/biocore-ui/ui/panel.html'))
  assert.ok(!noSession.includes('?session='))

  assert.equal(testApi.pickSessionId({
    byId: {
      a: { id: 'a', retainedBy: { mainView: 0 }, updatedAt: 1 },
      b: { id: 'b', retainedBy: { mainView: 1 }, updatedAt: 2 },
    },
  }), 'b')
  assert.equal(testApi.pickSessionId({
    byId: { a: { id: 'a', updatedAt: 5 }, b: { id: 'b', updatedAt: 9 } },
  }), 'b')
  assert.equal(testApi.pickSessionId(null), '')
  assert.ok(JSON.stringify(testApi.BiocoreMainPanel({})).includes('panel.html'))
  ok('main iframe URL carries session; empty session omits it without crashing')

  const before = window.open
  let opened = 0
  let selected = ''
  window.open = () => { opened += 1; return {} }
  testApi.setMainPanelSelector((key) => { selected = key; return true })
  const expanded = testApi.openInstrumentPanel('session-9')
  assert.equal(selected, 'biocore-panel')
  assert.equal(opened, 0)
  assert.ok(expanded.includes('session=session-9'))
  testApi.setMainPanelSelector(null)
  testApi.openInstrumentPanel('session-9')
  assert.equal(opened, 1)
  window.open = before
  testApi.setMainPanelSelector(null)
  ok('expand button selects the in-app main panel; popup remains the fallback')
}

/* ---------- switch UI (task-195) ---------- */
{
  const testApi = clientCtx.exports.__test
  assert.ok(testApi.SWITCH_META.every(
    (item) => switchMod.SWITCH_KEYS.indexOf(item.key) >= 0))
  assert.equal(testApi.confirmationText('safety_enabled', true), '确定要打开安全闸吗�?)
  assert.equal(testApi.confirmationText('safety_enabled', false), '确定要关闭安全闸吗？')
  assert.ok(testApi.switchErrorText('settings_unavailable').includes('设置服务不可�?))
  const section = JSON.stringify(testApi.SwitchSection({
    switches: {
      safety_enabled: true, boundary_enabled: false,
      crisis_safety_enabled: false, proactive_send: false,
    },
    busy: '',
    error: testApi.switchErrorText('write_failed'),
    onToggle() {},
  }))
  for (const needle of ['安全�?, '底线', '危机兜底', '主动回复', '开关（写宿主设置）', '写入失败']) {
    assert.ok(section.includes(needle), 'switch section missing ' + needle)
  }
  ok('client renders the four native switches with confirm text and rollback reason')
}
/* ---------- standalone instrument panel (task-202) ---------- */
{
  const panelDir = join(root, 'ui')
  const panelHtml = readFileSync(join(panelDir, 'panel.html'), 'utf8')
  const panelCss = readFileSync(join(panelDir, 'panel.css'), 'utf8')
  const panelJs = readFileSync(join(panelDir, 'panel.js'), 'utf8')
  assert.ok(panelHtml.length > 800, 'panel.html too small')
  assert.ok(panelCss.length > 1200, 'panel.css too small')
  assert.ok(panelJs.length > 2000, 'panel.js too small')
  for (const needle of ['moodChart', 'memoryList', 'switches', 'motivation', 'orb']) {
    assert.ok(panelHtml.includes(needle), 'panel.html missing ' + needle)
  }
  for (const needle of ['心情', '绷紧', '�?, '�?, '�?, '语气', '关系', '自主']) {
    assert.ok(panelHtml.includes(needle), 'panel.html missing ' + needle)
  }
  for (const needle of ['安全�?, '底线', '危机兜底', '主动回复', '已连�?, '未连�?,
    '/api/biocore/state', '/api/biocore/switch', 'confirm: true']) {
    assert.ok(panelJs.includes(needle), 'panel.js missing ' + needle)
  }
  for (const needle of ['prefers-color-scheme', '--accent', '.orb', '.switch-row']) {
    assert.ok(panelCss.includes(needle), 'panel.css missing ' + needle)
  }
  ok('panel html/css/js exist and carry the required fields')
}

{
  const extrasMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'panel-data.js')).href)
  const longText = '很长的记忆正�?.repeat(30)
  const sampleState = {
    now: 1000,
    core: {
      v: 0.3, self_esteem: 0.6, social_pain: 0.1, energy: 0.5, social: 0.55,
      labels: { intent: 'hold' },
      intero: { tension: 0.2, warmth: 0.5, heaviness: 0.3, clarity: 0.6 },
    },
    relationship: { closeness: 0.6, trust: 0.55, conflict: 0.1, bids: 2 },
    last_turn: {
      intent: 'hold', metacog_band: '�?,
      turn: { length_factor: 0.9, initiative: 0.4, allow_correction: false },
    },
    memory: {
      count: 12,
      recent: Array.from({ length: 12 }, (_, index) => ({
        id: 'm' + index, ts: index, score: 0.5, source: 'chat', kind: 'fact', text: longText,
      })),
    },
    history: [{ ts: 100, v: 0.1 }, { ts: 200, v: 0.2 }, { ts: 300, v: 0.3 }],
  }
  const extras = extrasMod.buildPanelExtras(sampleState)
  assert.equal(extras.memory_recent.length, 10)
  for (const item of extras.memory_recent) assert.ok(item.text.length <= 80)
  assert.deepEqual(Object.keys(extras.relationship_metrics).sort(),
    ['bids', 'closeness', 'conflict', 'trust'])
  for (const key of ['self_esteem', 'social_pain', 'energy', 'social']) {
    assert.ok(key in extras.body_metrics, 'body metrics missing ' + key)
  }
  for (const key of ['length_factor', 'initiative', 'metacog_band', 'intent']) {
    assert.ok(key in extras.narrative, 'narrative missing ' + key)
  }
  assert.ok(typeof extras.motivation === 'string' && extras.motivation.length > 0)
  assert.ok(extras.history.length >= 1 && extras.history.length <= extrasMod.HISTORY_LIMIT)
  ok('panel extras: 10 memories <=80 chars, relation/narrative metrics, motivation, history')
}

{
  const panelMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'panel-data.js')).href)
  const makeRes = () => ({
    code: 0, headers: null, body: undefined,
    writeHead(code, headers) { this.code = code; this.headers = headers },
    end(body) { this.body = body === undefined ? null : body },
  })
  const call = async (method, url) => {
    const res = makeRes()
    await panelMod.servePanelPage({ method, url }, res)
    return res
  }
  const html = await call('GET', '/api/biocore-ui/ui/panel.html')
  assert.equal(html.code, 200)
  assert.ok(String(html.headers['content-type']).startsWith('text/html'))
  assert.equal(html.headers['cache-control'], 'no-store')
  const head = await call('HEAD', '/api/biocore-ui/ui/panel.css')
  assert.equal(head.code, 200)
  assert.equal(head.body, null)
  assert.equal((await call('GET', '/api/biocore-ui/ui/../package.json')).code, 404)
  assert.equal((await call('POST', '/api/biocore-ui/ui/panel.html')).code, 405)
  ok('panel static route: GET/HEAD 200 + no-store, traversal/unknown 404, POST 405')
}

{
  const registeredPanel = []
  const fakeCtxPanel = {
    on() { return () => {} },
    inject(names, callback) {
      if (names.includes('webServer')) {
        callback({ webServer: { register(entry) { registeredPanel.push(entry); return () => {} } } })
      } else if (names.includes('commands')) {
        callback({ commands: { register() { return () => {} } } })
      }
    },
  }
  const dir = mkdtempSync(join(tmpdir(), 'biocore-ui-panel-route-'))
  try {
    const dispose = host.apply(fakeCtxPanel, { dataDir: dir })
    const entry = registeredPanel.find((item) => item.path === '/api/biocore-ui/ui')
    assert.ok(entry, 'panel prefix route registered')
    assert.equal(entry.kind, 'prefix')
    dispose()
    ok('host apply registers prefix /api/biocore-ui/ui')
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

{
  const testApi = clientCtx.exports.__test
  const previous = window.open
  const opened = []
  window.open = (url) => { opened.push(url); return {} }
  try {
    const url = testApi.openInstrumentPanel('session-y')
    assert.equal(url, '/api/biocore-ui/ui/panel.html?session=session-y')
    assert.deepEqual(opened, [url])
    assert.ok(JSON.stringify(testApi.ExpandButton({ sessionId: 'session-z' })).includes('展开独立面板'))
  } finally {
    window.open = previous
  }
  ok('client tab exposes 展开 button and opens the standalone panel URL')
}

{
  const vm = await import('node:vm')
  const sandbox = {}
  vm.runInNewContext(readFileSync(join(root, 'ui', 'panel-layout.js'), 'utf8'), sandbox)
  const layout = sandbox.BioCorePanelLayout
  assert.equal(layout.STORAGE_KEY, 'biocore-panel-layout-v2')
  assert.deepEqual(Array.from(layout.TAB_ORDER), ['dashboard', 'memory', 'relation', 'settings', 'tools'])
  assert.deepEqual(Array.from(layout.cardsForTab(layout.DEFAULT_ORDER, 'dashboard')),
    ['intero', 'log', 'state', 'today', 'loops', 'dims', 'cache', 'system', 'chart'])
  assert.deepEqual(Array.from(layout.cardsForTab(layout.DEFAULT_ORDER, 'relation')),
    ['metrics', 'narrative'])
  assert.deepEqual(Array.from(layout.cardsForTab(layout.DEFAULT_ORDER, 'tools')),
    ['trace', 'runtime', 'diagnostics'])
  const hidden = layout.hideCard(layout.defaultLayout(), 'chart')
  assert.equal(layout.visibleIds(hidden).includes('chart'), false)
  assert.ok(layout.showCard(hidden, 'chart').hidden.indexOf('chart') < 0)
  const store = new Map()
  const storage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  }
  layout.writeLayout(storage, layout.moveCard(layout.defaultLayout(), 'switches', -1))
  assert.equal(layout.readLayout(storage).order[0], 'intero')
  layout.clearLayout(storage)
  assert.equal(store.has(layout.STORAGE_KEY), false)
  assert.equal(layout.dragIntent({ editing: false }).hold, false)
  assert.equal(layout.dragIntent({ editing: true, pointerType: 'touch' }).hold, true)
  const solo = layout.defaultLayout()
  const hidToday = layout.hideCard(solo, 'today')
  assert.deepEqual(Array.from(layout.visibleIds(hidToday)).filter(function (id) {
    return ['state', 'today', 'loops'].indexOf(id) >= 0
  }), ['state', 'loops'])
  assert.ok(layout.showCard(hidToday, 'today').hidden.indexOf('today') < 0)
  ok('layout: groups/order/hidden/storage/restore + edit-only drag + solo column hide')
}

{
  const { EventEmitter } = await import('node:events')
  const dataDir = mkdtempSync(join(tmpdir(), 'biocore-ui-settings-'))
  const makeRes = () => ({
    code: 0, headers: null, body: null,
    writeHead(code, headers) { this.code = code; this.headers = headers },
    end(payload) { this.body = payload },
  })
  const call = async (method, payload) => {
    const req = new EventEmitter()
    req.method = method
    req.url = '/api/biocore/settings'
    const res = makeRes()
    const pending = host.handleSettings(req, res, dataDir)
    if (payload !== undefined) req.emit('data', Buffer.from(JSON.stringify(payload)))
    req.emit('end')
    await pending
    return { code: res.code, body: res.body ? JSON.parse(res.body) : null }
  }
  try {
    assert.equal((await call('GET')).body.embedding.mode, 'auto')
    const plainKey = 'test-key-placeholder'
    const saved = await call('POST', { confirm: true, embedding: {
      mode: 'remote', base_url: 'https://api.example.com', model: 'deepseek-flash',
      api_key: plainKey, local_path: 'C:/models/bge-m3',
    } })
    assert.equal(saved.code, 200)
    assert.ok(saved.body.embedding.api_key.includes('•�?))
    assert.equal(JSON.parse(readFileSync(join(dataDir, 'config.json'), 'utf8')).embedding.api_key, plainKey)
    assert.equal((await call('POST', { embedding: { mode: 'auto' } })).body.reason, 'confirm_required')
    assert.equal((await call('POST', { confirm: true, embedding: { mode: 'weird' } })).body.reason, 'bad_mode')
    ok('settings GET/POST: config.json lands, GET masks api_key + errors')
  } finally {
    rmSync(dataDir, { recursive: true, force: true })
  }
}

/* ---------- restored assertions: persona / UI / nav / edit / routes ---------- */
{
  const { EventEmitter } = await import('node:events')
  const dataDir = mkdtempSync(join(tmpdir(), 'biocore-ui-persona-restore-'))
  const makeRes = () => ({
    code: 0, headers: null, body: null,
    writeHead(code, headers) { this.code = code; this.headers = headers },
    end(payload) { this.body = payload },
  })
  const call = async (method, payload) => {
    const req = new EventEmitter()
    req.method = method
    req.url = '/api/biocore/persona'
    const res = makeRes()
    const pending = host.handlePersona(req, res, dataDir)
    if (payload !== undefined) req.emit('data', Buffer.from(JSON.stringify(payload)))
    req.emit('end')
    await pending
    return { code: res.code, body: res.body ? JSON.parse(res.body) : null }
  }
  try {
    rmSync(join(process.env.BIOCORE_SHARED_PERSONA_DIR, 'persona_profile.json'), { force: true })
    rmSync(join(process.env.BIOCORE_SHARED_PERSONA_DIR, 'persona.md'), { force: true })
    const empty = await call('GET')
    assert.equal(empty.code, 200)
    assert.equal(empty.body.ok, true)
    assert.equal(empty.body.reason, 'no_persona')
    ok('persona GET before compile -> no_persona shape')

    const text = '亲近、俏皮、温暖、先接情绪、好�?
    const compiled = await call('POST', { text, confirm: true })
    assert.equal(compiled.code, 200, JSON.stringify(compiled.body))
    assert.equal(compiled.body.ok, true)
    assert.ok(compiled.body.profile && compiled.body.profile.E !== undefined)
    assert.equal(compiled.body.preview.length, 9)
    const profileFile = join(dataDir, 'persona_profile.json')
    const mdFile = join(dataDir, 'persona.md')
    assert.ok(existsSync(profileFile) && existsSync(mdFile))
    const saved = JSON.parse(readFileSync(profileFile, 'utf8'))
    assert.equal(saved.persona, text)
    assert.equal(compiled.body.preview.find((row) => row.key === 'baseline_v').value,
      Number(Number(saved.profile.baseline_v).toFixed(2)))
    ok('persona POST compiles via bridge, writes profile+md, preview matches file')

    assert.equal((await call('POST', { text: '', confirm: true })).body.reason, 'empty_text')
    assert.equal((await call('POST', { text: 'x' })).body.reason, 'confirm_required')
    assert.equal((await call('POST', {
      text: 'x'.repeat(host.PERSONA_TEXT_LIMIT + 1), confirm: true,
    })).body.reason, 'text_too_long')
    ok('persona POST errors: empty/confirm/too long')

    // read order prefers shared: clear it so this test exercises the profile copy
    rmSync(join(process.env.BIOCORE_SHARED_PERSONA_DIR, 'persona_profile.json'), { force: true })
    rmSync(join(process.env.BIOCORE_SHARED_PERSONA_DIR, 'persona.md'), { force: true })
    writeFileSync(profileFile, '{broken', 'utf8')
    assert.equal((await call('GET')).body.reason, 'bad_profile')
    writeFileSync(profileFile, JSON.stringify(saved), 'utf8')
    ok('persona GET bad JSON -> bad_profile')

    const stateFile = join(dataDir, 'sessions', 'persona-restore.json')
    const ensured = await bridge.ensureState(stateFile, profileFile)
    assert.equal(ensured.ok, true, JSON.stringify(ensured))
    const stateDoc = JSON.parse(readFileSync(stateFile, 'utf8'))
    assert.ok(stateDoc.persona_profile && stateDoc.persona_version)
    assert.ok(Math.abs(stateDoc.core.v - saved.profile.baseline_v) < 0.01)
    ok('adapter read: bridge applies persona baselines to session state')

    const html = readFileSync(join(root, 'ui', 'panel.html'), 'utf8')
    const panelJs = readFileSync(join(root, 'ui', 'panel.js'), 'utf8')
    for (const needle of ['personaText', 'personaSave', 'personaStatus']) {
      assert.ok(html.includes(needle), 'panel.html missing ' + needle)
    }
    assert.ok(panelJs.includes('/api/biocore/persona'))
    ok('panel persona UI: textarea/save/preview wiring present')

    const registered = []
    const fakeCtx = {
      on() { return () => {} },
      inject(names, cb) {
        if (names.includes('webServer')) cb({ webServer: { register(entry) { registered.push(entry); return () => {} } } })
        else if (names.includes('commands')) cb({ commands: { register() { return () => {} } } })
      },
    }
    const dir2 = mkdtempSync(join(tmpdir(), 'biocore-ui-route-restore-'))
    try {
      const dispose = host.apply(fakeCtx, { dataDir: dir2 })
      const paths = registered.map((entry) => entry.path)
      for (const route of ['/api/biocore/state', '/api/biocore/switch',
        '/api/biocore/persona', '/api/biocore/settings', '/api/biocore/stats',
        '/api/biocore-ui/ui']) {
        assert.ok(paths.includes(route), 'missing route ' + route)
      }
      dispose()
    } finally { rmSync(dir2, { recursive: true, force: true }) }
    ok('host apply registers state/switch/persona/settings/panel routes')
  } finally {
    rmSync(dataDir, { recursive: true, force: true })
  }
}

{
  const api = clientCtx.exports.__test
  assert.equal(api.formatDelta(0.1, 0.14), '+0.04')
  assert.equal(api.formatDelta(0.1, 0.08), '-0.02')
  assert.equal(api.formatDelta(undefined, 1), '�?)
  ok('ui helpers: per-turn delta suffixes')

  const history = Array.from({ length: 70 }, (_, i) => ({ v: Math.sin(i / 6) * 0.5 }))
  const spark = api.buildSparkline({ available: true, history })
  assert.equal(api.SPARK_HEIGHT, 24)
  assert.equal(spark.props.height, 24)
  const points = String(spark.props.children[0].props.points).trim().split(' ')
  assert.equal(points.length, 60)
  ok('ui helpers: tab sparkline is 24px / last 60 points')

  for (const [data, error, label] of [
    [{ available: false, reason: 'no_state_yet' }, false, '这个会话还没聊过'],
    [{ available: false, reason: 'bad_state' }, false, '状态文件坏�?],
    [null, true, '连不上宿�?],
  ]) {
    const status = api.statusOf(data, error, 's')
    assert.equal(status.label, label)
    assert.ok(status.suggestion.length > 0)
  }
  ok('ui helpers: three empty states with advice')

  const html = readFileSync(join(root, 'ui', 'panel.html'), 'utf8')
  const css = readFileSync(join(root, 'ui', 'panel.css'), 'utf8')
  for (const id of ['intero', 'log', 'state', 'today', 'loops', 'dims', 'cache', 'system', 'chart',
    'memory', 'memory-summary', 'metrics', 'trace', 'runtime', 'persona', 'model', 'switches', 'advanced']) {
    assert.ok(html.includes('data-card-id="' + id + '"'), 'missing card ' + id)
  }
  assert.ok(!/class="channels-wrap"[^>]*data-card-id/.test(html), 'channels wrapper must not be a layout card')
  for (const marker of ['card-head', 'subblock', 'triple', 'add-cards']) {
    assert.ok(html.includes(marker), 'panel.html missing ' + marker)
  }
  for (const marker of ['--card', '.subblock', '.tabbar', '.card-hide']) {
    assert.ok(css.includes(marker), 'panel.css missing ' + marker)
  }
  ok('ui structure: 15 tab cards (4 channel cards solo) + card-head/subblock/triple markup and css')

  const panelJs = readFileSync(join(root, 'ui', 'panel.js'), 'utf8')
  for (const marker of ['layoutFab', 'layoutDone', 'data-layout-hide', 'data-add-card',
    'pointerdown', 'LONG_PRESS_MS', 'localStorage', '/api/biocore/settings',
    '/api/biocore/persona']) {
    assert.ok(panelJs.includes(marker), 'panel.js missing ' + marker)
  }
  ok('edit layout wiring: fab/done, red minus, add card, long-press, storage')
}

{
  const vm = await import('node:vm')
  const sandbox = {}
  vm.runInNewContext(readFileSync(join(root, 'ui', 'panel-layout.js'), 'utf8'), sandbox)
  const layout = sandbox.BioCorePanelLayout
  assert.deepEqual(Array.from(layout.TAB_ORDER), ['dashboard', 'memory', 'relation', 'settings', 'tools'])
  assert.deepEqual(Array.from(layout.cardsForTab(layout.DEFAULT_ORDER, 'settings')),
    ['persona', 'model', 'switches', 'expression', 'advanced'])
  ok('bottom nav groups: dashboard/memory/relation/settings/tools mapping')

  const hidden = layout.hideCard(layout.defaultLayout(), 'memory')
  assert.equal(layout.visibleIds(hidden).includes('memory'), false)
  assert.ok(layout.visibleIds(layout.showCard(hidden, 'memory')).includes('memory'))
  assert.equal(layout.dragIntent({ editing: false }).hold, false)
  assert.equal(layout.dragIntent({ editing: true }).hold, true)
  ok('layout: show/hide restore + edit-only drag intent')
}

{
  assert.equal(tabs.length, 1)
  assert.equal(tabs[0].kind, 'biocore')
  assert.equal(tabs[0].title(), '生物�?)
  ok('client right tab registered (kind biocore)')

  assert.deepEqual(Array.from(Object.values(switchMod.DEFAULT_SWITCHES)),
    [true, false, false, false, false, true, true, true, true])
  assert.equal(switchMod.readSwitchValues({ proactive_autonomy: true }).proactive_send, true)
  ok('switch defaults all false + proactive_autonomy read alias')

  const htmlSize = readFileSync(join(root, 'ui', 'panel.html'), 'utf8').length
  const cssSize = readFileSync(join(root, 'ui', 'panel.css'), 'utf8').length
  const jsSize = readFileSync(join(root, 'ui', 'panel.js'), 'utf8').length
  assert.ok(htmlSize > 1500 && cssSize > 3000 && jsSize > 15000)
  ok('panel static files are all present and non-empty')
}

{
  const expressionMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'expression-config.js')).href)
  const dir = mkdtempSync(join(tmpdir(), 'biocore-ui-expression-'))
  try {
    const defaults = expressionMod.readExpressionSettings(dir)
    assert.deepEqual(defaults, { length_hint: false, length_target: 10, length_max: 30 })
    ok('expression config: missing config.json -> false/10/30 defaults')

    const { EventEmitter } = await import('node:events')
    const makeRes = () => ({
      code: 0, headers: null, body: null,
      writeHead(code, headers) { this.code = code; this.headers = headers },
      end(payload) { this.body = payload },
    })
    const callSettings = async (method, payload) => {
      const req = new EventEmitter()
      req.method = method
      req.url = '/api/biocore/settings'
      const res = makeRes()
      const pending = host.handleSettings(req, res, dir)
      if (payload !== undefined) req.emit('data', Buffer.from(JSON.stringify(payload)))
      req.emit('end')
      await pending
      return { code: res.code, body: res.body ? JSON.parse(res.body) : null }
    }
    const saved = await callSettings('POST', {
      confirm: true,
      expression: { length_hint: true, length_target: 8, length_max: 24 },
    })
    assert.equal(saved.code, 200, JSON.stringify(saved.body))
    assert.deepEqual(saved.body.expression, { length_hint: true, length_target: 8, length_max: 24 })
    const stored = JSON.parse(readFileSync(join(dir, 'config.json'), 'utf8'))
    assert.deepEqual(stored.expression, { length_hint: true, length_target: 8, length_max: 24 })
    assert.deepEqual(expressionMod.readExpressionSettings(dir), stored.expression)
    const reload = await callSettings('GET')
    assert.deepEqual(reload.body.expression, stored.expression)
    ok('expression config: POST writes config.json expression, GET reads it back')

    const coreTurn = await import(pathToFileURL(join(root, 'lib', 'modules', 'core-turn.js')).href)
    const off = coreTurn.turnStartPayload({
      sessionId: 's', userText: 'hi', switches: { safety_enabled: true },
      expression: { length_hint: false, length_target: 10, length_max: 30 },
    })
    assert.equal(off.length_hint, false)
    assert.equal(off.length_target, 10)
    assert.equal(off.length_max, 30)
    const defaultPayload = coreTurn.turnStartPayload({ sessionId: 's', userText: 'hi' })
    assert.equal(defaultPayload.length_hint, false)
    ok('turn_start payload: length_hint defaults false with 10/30')

    const on = coreTurn.turnStartPayload({
      sessionId: 's', userText: 'hi', switches: { safety_enabled: true },
      expression: expressionMod.readExpressionSettings(dir),
    })
    assert.equal(on.length_hint, true)
    assert.equal(on.length_target, 8)
    assert.equal(on.length_max, 24)
    const configured = expressionMod.readExpressionSettings(dir)
    assert.equal(configured.length_hint, true)
    ok('turn_start payload: enabled reads config -> true/8/24')
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

{
  const storeMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'persona-store.js')).href)
  const previous = process.env.BIOCORE_SHARED_PERSONA_DIR
  const sharedDir = mkdtempSync(join(tmpdir(), 'biocore-ui-shared-persona-'))
  const profileDir = mkdtempSync(join(tmpdir(), 'biocore-ui-profile-persona-'))
  process.env.BIOCORE_SHARED_PERSONA_DIR = sharedDir
  try {
    const { EventEmitter } = await import('node:events')
    const makeRes = () => ({
      code: 0, headers: null, body: null,
      writeHead(code, headers) { this.code = code; this.headers = headers },
      end(payload) { this.body = payload },
    })
    const callPersona = async (method, payload) => {
      const req = new EventEmitter()
      req.method = method
      req.url = '/api/biocore/persona'
      const res = makeRes()
      const pending = host.handlePersona(req, res, profileDir)
      if (payload !== undefined) req.emit('data', Buffer.from(JSON.stringify(payload)))
      req.emit('end')
      await pending
      return { code: res.code, body: res.body ? JSON.parse(res.body) : null }
    }
    const empty = await callPersona('GET')
    assert.equal(empty.code, 200)
    assert.equal(empty.body.ok, true)
    assert.equal(empty.body.reason, 'no_persona')
    assert.equal(empty.body.source, 'profile')
    ok('persona GET before compile -> no_persona shape')

    const text = '亲近、俏皮、温暖、先接情绪、好�?
    const compiled = await callPersona('POST', { text, confirm: true })
    assert.equal(compiled.code, 200, JSON.stringify(compiled.body))
    assert.equal(compiled.body.ok, true)
    assert.equal(compiled.body.source, 'shared')
    const sharedFiles = storeMod.sharedPersonaPaths()
    assert.ok(existsSync(sharedFiles.profile) && existsSync(sharedFiles.text))
    assert.ok(existsSync(join(profileDir, 'persona_profile.json')) && existsSync(join(profileDir, 'persona.md')))
    ok('persona shared write: compile writes shared + profile copy')

    writeFileSync(sharedFiles.profile, JSON.stringify({ profile: { E: 0.11 }, persona: 'shared' }))
    const readShared = await callPersona('GET')
    assert.equal(readShared.body.profile.E, 0.11)
    assert.equal(readShared.body.source, 'shared')
    ok('persona read order: shared wins over profile copy')

    rmSync(sharedFiles.profile, { force: true })
    rmSync(sharedFiles.text, { force: true })
    writeFileSync(join(profileDir, 'persona_profile.json'), JSON.stringify({ profile: { E: 0.22 }, persona: 'profile' }))
    writeFileSync(join(profileDir, 'persona.md'), '# profile persona\n')
    const migrated = storeMod.migratePersonaToShared(profileDir)
    assert.equal(migrated.migrated, true)
    assert.ok(existsSync(sharedFiles.profile) && existsSync(sharedFiles.text))
    ok('persona migration: profile copy seeds missing shared dir')

    const sourceInfo = await callPersona('GET')
    assert.equal(sourceInfo.body.source, 'shared')
    assert.ok(String(sourceInfo.body.profile_path).indexOf(sharedDir) === 0)
    ok('persona GET exposes actual source path')
  } finally {
    rmSync(sharedDir, { recursive: true, force: true })
    rmSync(profileDir, { recursive: true, force: true })
    if (previous === undefined) delete process.env.BIOCORE_SHARED_PERSONA_DIR
    else process.env.BIOCORE_SHARED_PERSONA_DIR = previous
  }
}

{
  const coreTurn = await import(pathToFileURL(join(root, 'lib', 'modules', 'core-turn.js')).href)
  const start = coreTurn.turnStartPayload({ sessionId: 's', userText: 'hi' })
  const end = coreTurn.turnEndPayload({ sessionId: 's', assistantText: 'hi' })
  assert.equal(start.capture, true)
  assert.equal(start.memoryEnabled, true)
  assert.equal(end.capture, true)
  assert.equal(end.memoryEnabled, true)
  ok('payload: capture merged on both turn_start and turn_end')
}

{
  const coreTurn = await import(pathToFileURL(join(root, 'lib', 'modules', 'core-turn.js')).href)
  const dir = mkdtempSync(join(tmpdir(), 'biocore-ui-persona-prompt-'))
  try {
    assert.equal(coreTurn.turnStartPayload({}).persona_prompt, true)
    writeFileSync(join(dir, 'persona_preset.json'), JSON.stringify({ applied: true }), 'utf8')
    assert.equal(coreTurn.personaApplied(dir), true)
    assert.equal(coreTurn.turnStartPayload({
      personaPrompt: !coreTurn.personaApplied(dir),
    }).persona_prompt, false)
    ok('payload: persona_prompt dedupe when preset applied')
  } finally { rmSync(dir, { recursive: true, force: true }) }
}

{
  const panelJs = readFileSync(join(root, 'ui', 'panel.js'), 'utf8')
  assert.ok(panelJs.includes('var POLL_MS = 5000'))
  assert.ok(panelJs.includes('document.hidden'))
  assert.ok(panelJs.includes('visibilitychange'))
  ok('panel polling: 5000ms + hidden pause + refresh on visible')
}

{
  const home = mkdtempSync(join(tmpdir(), 'biocore-ui-dir-fallback-'))
  try {
    const web = join(home, '.dsh', 'profiles', 'web')
    mkdirSync(web, { recursive: true })
    writeFileSync(join(web, 'package.json'), '{}', 'utf8')
    assert.equal(resolveDataDir({}, {}, ['node', 'bin.js'], home), join(web, 'data'))
    ok('data dir: no argv resolves profiles/web/data')
    rmSync(web, { recursive: true, force: true })
    const solo = join(home, '.dsh', 'profiles', 'solo')
    mkdirSync(solo, { recursive: true })
    writeFileSync(join(solo, 'package.json'), '{}', 'utf8')
    assert.equal(resolveDataDir({}, {}, ['node', 'bin.js'], home), join(solo, 'data'))
    ok('data dir: exactly one profile resolves its data')
    mkdirSync(join(home, '.dsh', 'profiles', 'two'), { recursive: true })
    writeFileSync(join(home, '.dsh', 'profiles', 'two', 'package.json'), '{}', 'utf8')
    assert.equal(resolveDataDir({}, {}, ['node', 'bin.js'], home), PLUGIN_DATA_DIR)
    ok('data dir: multiple profiles fall back to plugin data')
  } finally { rmSync(home, { recursive: true, force: true }) }
}

{
  const home = mkdtempSync(join(tmpdir(), 'biocore-ui-migrate-extra-'))
  const profile = join(home, '.dsh', 'profiles', 'web')
  const target = join(profile, 'data')
  const legacy = mkdtempSync(join(tmpdir(), 'biocore-ui-legacy-extra-'))
  try {
    mkdirSync(profile, { recursive: true })
    writeFileSync(join(profile, 'package.json'), '{}', 'utf8')
    mkdirSync(join(target, 'sessions'), { recursive: true })
    writeFileSync(join(target, 'flags.json'), '{"keep":true}', 'utf8')
    mkdirSync(join(legacy, 'sessions'), { recursive: true })
    writeFileSync(join(legacy, 'flags.json'), '{"old":true}', 'utf8')
    writeFileSync(join(legacy, 'persona.md'), 'OLD', 'utf8')
    writeFileSync(join(legacy, 'sessions', 's.json'), '{}', 'utf8')
    const result = dataDirMod.migrateLegacyPluginData(target, legacy)
    assert.equal(result.migrated, true)
    assert.equal(JSON.parse(readFileSync(join(target, 'flags.json'), 'utf8')).keep, true)
    assert.equal(readFileSync(join(target, 'persona.md'), 'utf8'), 'OLD')
    assert.ok(existsSync(join(legacy, 'persona.md')))
    ok('data dir migration: missing files copied, existing kept, source retained')
  } finally {
    rmSync(home, { recursive: true, force: true })
    rmSync(legacy, { recursive: true, force: true })
  }
}

{
  const { EventEmitter } = await import('node:events')
  const dataDir = mkdtempSync(join(tmpdir(), 'biocore-ui-rerank-extra-'))
  const makeRes = () => ({ code: 0, headers: null, body: null,
    writeHead(code, headers) { this.code = code; this.headers = headers },
    end(payload) { this.body = payload } })
  const callSettings = async (method, payload) => {
    const req = new EventEmitter(); req.method = method
    const res = makeRes(); const pending = host.handleSettings(req, res, dataDir)
    if (payload !== undefined) req.emit('data', Buffer.from(JSON.stringify(payload)))
    req.emit('end'); await pending
    return { code: res.code, body: res.body ? JSON.parse(res.body) : null }
  }
  const callTest = async (payload, options) => {
    const req = new EventEmitter(); req.method = 'POST'
    const res = makeRes(); const pending = host.handleSettingsTest(req, res, Object.assign({ dataDir }, options || {}))
    req.emit('data', Buffer.from(JSON.stringify(payload))); req.emit('end'); await pending
    return { code: res.code, body: res.body ? JSON.parse(res.body) : null }
  }
  try {
    const key = 'sk-rerank-extra-123456'
    const saved = await callSettings('POST', { confirm: true, rerank: {
      enabled: true, base_url: 'https://api.jina.ai/v1',
      model: 'jina-reranker-v2-base-multilingual', api_key: key, top_n: 15 } })
    assert.equal(saved.code, 200)
    assert.ok(saved.body.rerank.api_key.includes('•�?))
    assert.equal(saved.body.rerank.api_key.includes(key), false)
    const stored = JSON.parse(readFileSync(join(dataDir, 'config.json'), 'utf8'))
    assert.equal(stored.rerank.api_key, key)
    ok('settings: rerank segment writes + masks api_key')
    const calls = []
    const fetchOk = async (url, options) => { calls.push({ url, options }); return { ok: true, status: 200 } }
    const rerankTest = await callTest({ kind: 'rerank' }, { fetchImpl: fetchOk })
    assert.equal(rerankTest.body.ok, true)
    assert.ok(calls[0].url.endsWith('/rerank'))
    assert.equal(JSON.stringify(rerankTest.body).includes(key), false)
    ok('settings/test: rerank minimal request ok + key not returned')
    const emptyDir = mkdtempSync(join(tmpdir(), 'biocore-ui-test-empty-'))
    try {
      const emptyReq = new EventEmitter(); emptyReq.method = 'POST'
      const emptyRes = makeRes()
      const pending = host.handleSettingsTest(emptyReq, emptyRes, { dataDir: emptyDir, fetchImpl: fetchOk })
      emptyReq.emit('data', Buffer.from(JSON.stringify({ kind: 'rerank' }))); emptyReq.emit('end')
      await pending
      assert.equal(JSON.parse(emptyRes.body).reason, 'not_configured')
      ok('settings/test: unconfigured rerank -> not_configured')
    } finally { rmSync(emptyDir, { recursive: true, force: true }) }
  } finally { rmSync(dataDir, { recursive: true, force: true }) }
}

{
  const sanitize = await import(pathToFileURL(join(root, 'lib', 'modules', 'sanitize-reply.js')).href)
  assert.equal(sanitize.sanitizeReply('你好<bc>{"e":"sad","i":0.55}</bc>呀'), '你好呀')
  assert.equal(sanitize.sanitizeReply('A<biocore>{"e":"mad","i":0.2}</biocore>B'), 'AB')
  assert.equal(sanitize.sanitizeReply('先说结论<bc>{"e":"sad","i":0.55}'), '先说结论')
  assert.equal(sanitize.sanitizeReply('真的吗？<bc>{"e":"sad","i":0.55}后面的都算标签内。正�?), '真的吗？正常')
  ok('sanitize: closed/unclosed bc + biocore tags removed')
}

{
  const sanitize = await import(pathToFileURL(join(root, 'lib', 'modules', 'sanitize-reply.js')).href)
  assert.equal(sanitize.sanitizeReply('{"e":"sad","i":0.55}'), '')
  assert.equal(sanitize.sanitizeReply('前言{"e":"sad","i":0.55,"n":2}后记'), '前言后记')
  assert.equal(sanitize.sanitizeReply('{"name":"x","value":1}'), '{"name":"x","value":1}')
  ok('sanitize: bare e/i[/n] JSON stripped, normal JSON untouched')
}

{
  const sanitize = await import(pathToFileURL(join(root, 'lib', 'modules', 'sanitize-reply.js')).href)
  assert.equal(sanitize.sanitizeReply('<bc>{"e":"sad","i":0.5}</bc><bc>{"e":"mad","i":0.6}</bc>�?), '�?)
  assert.equal(sanitize.sanitizeReply('   '), '')
  const original = '你好<bc>{"e":"sad","i":0.5}</bc>'
  assert.equal(sanitize.sanitizeReply(original, { enabled: false }), original)
  ok('sanitize: consecutive tags, empty result, switch off')
}

{
  const coreTurn = await import(pathToFileURL(join(root, 'lib', 'modules', 'core-turn.js')).href)
  const original = 'A<bc>{"e":"sad","i":0.5}</bc>B'
  assert.equal(coreTurn.chooseCleanReply(original, 'CORE', { sanitize: () => 'JS' }), 'CORE')
  ok('clean_text: core value wins when present')
}

{
  const coreTurn = await import(pathToFileURL(join(root, 'lib', 'modules', 'core-turn.js')).href)
  const original = 'A<bc>{"e":"sad","i":0.5}</bc>B'
  assert.equal(coreTurn.chooseCleanReply(original, null, { sanitize: () => 'JS' }), 'JS')
  ok('clean_text: missing core value falls back to JS sanitizer')
}

{
  const coreTurn = await import(pathToFileURL(join(root, 'lib', 'modules', 'core-turn.js')).href)
  const original = 'A<bc>{"e":"sad","i":0.5}</bc>B'
  assert.equal(coreTurn.chooseCleanReply(original, 'CORE', { sanitize: () => 'JS-CLEAN' }), 'CORE')
  assert.equal(coreTurn.chooseCleanReply(original, 'CORE', { sanitizeEnabled: false }), original)
  ok('clean_text: core wins over differing JS result; switch off keeps original')
}

const coreTurnSub = await import(pathToFileURL(join(root, 'lib', 'modules', 'core-turn.js')).href)
const exprSub = await import(pathToFileURL(join(root, 'lib', 'modules', 'expression-inject.js')).href)
const queueSub = await import(pathToFileURL(join(root, 'lib', 'modules', 'turn-queue.js')).href)
const dirSub = mkdtempSync(join(tmpdir(), 'biocore-ui-subagent-'))
try {
  let turnStartCalls = 0
  const listener = coreTurnSub.makeCoreSessionListener({
    stateDir: dirSub,
    getSwitches: () => ({ safety_enabled: true }),
    getExpression: () => ({ length_hint: false, length_target: 10, length_max: 30 }),
    getSanitizeReply: () => true,
    runAction: async () => { turnStartCalls += 1; return { ok: true, prompt_block: 'INJECT' } },
    runTurn: async () => 'adapter',
    store: dirSub,
    log: () => {},
  })
  const sendUser = (sessionId, header) => listener(
    { id: sessionId, header },
    { type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'hi' }] } },
  )
  sendUser('sub-zero', { delegationDepth: 0 })
  await queueSub.pendingSessionTurn('sub-zero')
  assert.equal(turnStartCalls, 1)
  assert.equal(exprSub.promptTextFor('sub-zero', 'turn'), 'INJECT')
  ok('subagent: depth=0 keeps persona/state/thinking injection')
  sendUser('sub-one', { delegationDepth: 1 })
  await queueSub.pendingSessionTurn('sub-one')
  assert.equal(turnStartCalls, 2)
  assert.equal(exprSub.promptTextFor('sub-one', 'turn'), '')
  ok('subagent: depth=1 skips injection (prompt_block blanked) but still calls turn_start')
  sendUser('sub-missing', {})
  await queueSub.pendingSessionTurn('sub-missing')
  assert.equal(exprSub.promptTextFor('sub-missing', 'turn'), 'INJECT')
  ok('subagent: missing delegationDepth behaves like main session')
} finally { rmSync(dirSub, { recursive: true, force: true }) }

const envMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'python-env.js')).href)
const cfgDirEnv = mkdtempSync(join(tmpdir(), 'biocore-ui-env-'))
try {
  writeFileSync(join(cfgDirEnv, 'config.json'), JSON.stringify({
    embedding: { mode: 'remote', base_url: 'https://api.example.com', model: 'embed-x', api_key: 'sk-env', local_path: 'C:/models/x' },
    rerank: { enabled: true, base_url: 'https://api.jina.ai/v1', model: 'jina-reranker-v2-base-multilingual', api_key: 'sk-rr', top_n: 7 },
  }), 'utf8')
  const env = envMod.embeddingEnv(cfgDirEnv)
  assert.equal(env.BIOCORE_EMBEDDING_MODE, 'remote')
  assert.equal(env.BIOCORE_EMBEDDING_BASE_URL, 'https://api.example.com')
  assert.equal(env.BIOCORE_EMBEDDING_MODEL, 'embed-x')
  assert.equal(env.BIOCORE_EMBEDDING_KEY, 'sk-env')
  assert.equal(env.BIOCORE_EMBEDDING_LOCAL_PATH, undefined)
  const rr = envMod.rerankConfig(cfgDirEnv)
  assert.equal(rr.rerank_enabled, true)
  assert.equal(rr.rerank_top_n, 7)
  assert.equal(rr.rerank_timeout, 8)
  ok('python env: embedding mapping (no local_path) + rerank_config')
} finally { rmSync(cfgDirEnv, { recursive: true, force: true }) }

const cfgDirNoPath = mkdtempSync(join(tmpdir(), 'biocore-ui-nopath-'))
try {
  const req = new (await import('node:events')).EventEmitter()
  const res = fakeRes()
  req.method = 'POST'
  const pending = host.handleSettings(req, res, cfgDirNoPath)
  req.emit('data', Buffer.from(JSON.stringify({ confirm: true, embedding: { mode: 'remote', base_url: 'https://x', model: 'm', api_key: 'k', local_path: 'C:/old' } })))
  req.emit('end')
  await pending
  const saved = JSON.parse(res.body)
  assert.equal(saved.ok, true)
  assert.equal('local_path' in saved.embedding, false)
  const stored = JSON.parse(readFileSync(join(cfgDirNoPath, 'config.json'), 'utf8'))
  assert.equal('local_path' in stored.embedding, false)
  const getReq = new (await import('node:events')).EventEmitter()
  getReq.method = 'GET'
  const getRes = fakeRes()
  await host.handleSettings(getReq, getRes, cfgDirNoPath)
  assert.equal('local_path' in JSON.parse(getRes.body).embedding, false)
  ok('settings: embedding.local_path removed from GET/POST projection')
} finally { rmSync(cfgDirNoPath, { recursive: true, force: true }) }

const statsReply = { ok: true, embed: { hit: 3, miss: 1, remote: 2, local: 1, fail: 0 } }
const statsDir = mkdtempSync(join(tmpdir(), 'biocore-ui-stats-'))
try {
  writeFileSync(join(statsDir, 'cache_stats.json'),
    JSON.stringify({ turns: 2, prompt_tokens: 100, cache_read: 50, hit_rate: 0.5 }))
  const statsRes = fakeRes()
  await host.handleStats({ method: 'GET' }, statsRes, {
    dataDir: statsDir,
    runAction: async () => statsReply,
  })
  const statsBody = JSON.parse(statsRes.body)
  assert.equal(statsBody.ok, true)
  assert.equal(statsBody.embed.hit, 3)
  assert.equal(statsBody.cache.hit_rate, 0.5)
  assert.equal(statsBody.cache.cache_read, 50)
  const failingStats = fakeRes()
  await host.handleStats({ method: 'GET' }, failingStats, {
    runAction: async () => { throw new Error('core down') },
  })
  const failedBody = JSON.parse(failingStats.body)
  assert.equal(failedBody.ok, false)
  assert.equal(failedBody.reason, 'core_unavailable')
  ok('stats: pure forward of core reply + display-only cache file')
} finally { rmSync(statsDir, { recursive: true, force: true }) }

const cacheMod = await import(pathToFileURL(join(root, 'lib', 'modules', 'cache-stats.js')).href)
const coreTurnCache = await import(pathToFileURL(join(root, 'lib', 'modules', 'core-turn.js')).href)
const memInjectCache = await import(pathToFileURL(join(root, 'lib', 'modules', 'memory-inject.js')).href)
assert.equal(coreTurnCache.TURN_CONTEXT_ORDER, 9500)
assert.equal(memInjectCache.CONTEXT_ORDER, 9510)
ok('context order: dynamic turn/memory contexts moved to 9500/9510')

const cacheDir = mkdtempSync(join(tmpdir(), 'biocore-ui-cache-'))
try {
  writeFileSync(join(cacheDir, 'cache_stats.json'),
    JSON.stringify({ turns: 2, prompt_tokens: 560, cache_read: 400, cache_write: 50,
      hit_rate: 0.7143, updated_at: 123 }))
  const readBack = cacheMod.readCacheStats(cacheDir)
  assert.equal(readBack.turns, 2)
  assert.equal(readBack.cache_read, 400)
  assert.equal(readBack.prompt_tokens, 560)
  assert.equal(readBack.hit_rate, 0.7143)
  ok('cache stats: display-only read of the file values')
  writeFileSync(join(cacheDir, 'cache_stats.json'), '{broken', 'utf8')
  assert.equal(cacheMod.readCacheStats(cacheDir).turns, 0)
  ok('cache stats: corrupt file degrades to zeros')
  const source = readFileSync(join(root, 'lib', 'modules', 'cache-stats.js'), 'utf8')
  assert.equal(source.includes('recordCacheUsage'), false)
  assert.equal(/cacheRead\s*\/\s*prompt/.test(source), false)
  ok('cache stats: adapter no longer computes usage stats')
} finally { rmSync(cacheDir, { recursive: true, force: true }) }

console.log('\nall ' + passed + ' checks passed')
