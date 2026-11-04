/** task-204 self-test: Node only transports; Python owns all policy.
 *
 * Asserts the frozen contract:
 *   python <plugin>/biocore_runtime/pipeline.py <action>   (JSON stdin/stdout)
 *   inject: {phase, session_id, user_text, state, switches} -> {ok, text}
 *   gate:   {reply, user_text, switches} -> {ok, blocked, action, text, rule}
 *   all four switches false -> no call, no injection, no gate, no warning
 *   returned text is used verbatim (no Node truncation/rewriting)
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const load = (relative) => import(pathToFileURL(join(root, relative)).href)
const injectMod = await load('lib/modules/expression-inject.js')
const gateMod = await load('lib/modules/safety-gate.js')
const refreshMod = await load('lib/modules/expression-refresh.js')
const runtimeClient = await load('lib/modules/runtime-client.js')
const stateMod = await load('lib/modules/state.js')

let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }

const OFF = { safety_enabled: false, boundary_enabled: false, crisis_safety_enabled: false, proactive_send: false }
const ON = { safety_enabled: true, boundary_enabled: true, crisis_safety_enabled: true, proactive_send: true }

function makePromptCtx() {
  const contexts = []
  const ctx = {
    on() { return () => {} },
    inject(names, callback) {
      if (names.includes('systemPrompt')) {
        callback({ systemPrompt: { context(entry) { contexts.push(entry); return () => {} } } })
      }
    },
  }
  return { ctx, contexts }
}

function event(type, text) {
  return { type, data: { message: { content: [{ type: 'text', text }] } } }
}

const sampleState = {
  now: 1762300000, turn: 2,
  core: { v: 0.12, a: 0.4, social: 0.5, stress: 0.3, energy: 0.6 },
  relationship: { closeness: 0.6, trust: 0.5, conflict: 0.0 },
  autonomy: { ready: 0.3 }, memory: { count: 1 },
}

const dataDir = mkdtempSync(join(tmpdir(), 'biocore-ui-204-'))
mkdirSync(join(dataDir, 'sessions'), { recursive: true })
writeFileSync(join(dataDir, 'sessions', 's1.json'), JSON.stringify(sampleState), 'utf8')

try {
  /* ---------- flags off: no context text, no calls, no warnings ---------- */
  const logs = []
  const off = { inject: 0, gate: 0 }
  await refreshMod.refreshPromptCache({
    dataDir, sessionId: 's1', getSwitches: () => OFF,
    callInject: async () => { off.inject += 1; return { ok: true, text: 'SHOULD-NOT-RUN' } },
  })
  assert.equal(off.inject, 0)
  const offCtx = makePromptCtx()
  const disposeOff = injectMod.registerExpressionInjections(offCtx.ctx, {
    getSwitches: () => OFF,
    getText: (phase, sid) => injectMod.promptTextFor(sid, phase),
    log: (line) => logs.push(line),
  })
  for (const entry of offCtx.contexts) {
    assert.equal(entry.text({ agent: { sessionId: 's1' } }), '')
  }
  assert.equal(logs.filter((line) => line.includes('__bioInject')).length, 0)
  assert.equal(injectMod.promptTextFor('s1', 'turn'), '')
  disposeOff()
  const offWfHandlers = []
  refreshMod.registerContextRefresh(
    { on(name, handler) { offWfHandlers.push([name, handler]); return () => {} } },
    {
      dataDir, getSwitches: () => OFF,
      callInject: async () => { off.inject += 1; return { ok: true, text: 'SHOULD-NOT-RUN' } },
      log: (line) => logs.push(line),
    })
  const offAssembly = { sections: [], contexts: [{ name: 'biocore-expression-turn', text: 'OLD' }] }
  const offPatched = await offWfHandlers.find(([name]) => name === 'system-prompt/assemble')[1](
    offAssembly, { agent: { sessionId: 's1' } }, async () => offAssembly)
  assert.equal(offPatched.contexts[0].text, 'OLD')
  assert.equal(off.inject, 0)
  assert.equal(logs.filter((line) => line.includes('__bioInject')).length, 0)
  ok('all switches off: no python call, empty prompt text, no inject warning')

  /* ---------- inject contract + verbatim text ---------- */
  assert.deepEqual([...runtimeClient.ACTIONS], ['inject', 'gate', 'capture', 'tool'])
  const injectCalls = []
  const PY_TURN = '【本轮状态】' + '她'.repeat(500) + '\n【表达约束】长度：正常'
  gateMod.rememberLastUser('s1', '今天有点累')
  const refreshed = await refreshMod.refreshPromptCache({
    dataDir, sessionId: 's1', getSwitches: () => ON,
    callInject: async (payload) => {
      injectCalls.push(payload)
      return payload.phase === 'boot'
        ? { ok: true, phase: 'boot', text: 'BOOT-FROM-PY' }
        : { ok: true, phase: 'turn', text: PY_TURN }
    },
  })
  assert.equal(refreshed.refreshed, true)
  assert.ok(injectCalls.some((call) => call.phase === 'boot'))
  const turnCall = injectCalls.find((call) => call.phase === 'turn')
  assert.ok(turnCall, 'turn inject call')
  assert.deepEqual(Object.keys(turnCall).sort(), ['phase', 'session_id', 'state', 'switches', 'user_text'])
  assert.equal(turnCall.session_id, 's1')
  assert.equal(turnCall.user_text, '今天有点累')
  assert.equal(turnCall.switches.safety_enabled, true)
  assert.equal(turnCall.state.v, 0.12)
  assert.equal(injectMod.promptTextFor('s1', 'boot'), 'BOOT-FROM-PY')
  assert.equal(injectMod.promptTextFor('s1', 'turn'), PY_TURN, 'verbatim: no Node truncation')

  const onCtx = makePromptCtx()
  const disposeOn = injectMod.registerExpressionInjections(onCtx.ctx, {
    getSwitches: () => ON,
    getText: (phase, sid) => injectMod.promptTextFor(sid, phase),
    log: (line) => logs.push(line),
  })
  const byName = Object.fromEntries(onCtx.contexts.map((entry) => [entry.name, entry]))
  assert.equal(byName['biocore-expression-boot'].order, -200)
  assert.equal(byName['biocore-expression-turn'].order, -190)
  assert.equal(byName['biocore-expression-boot'].text({ agent: { sessionId: 's1' } }), 'BOOT-FROM-PY')
  assert.equal(byName['biocore-expression-turn'].text({ agent: { sessionId: 's1' } }), PY_TURN)
  assert.equal(byName['biocore-expression-turn'].text({ agent: { sessionId: 'other' } }), '')
  disposeOn()

  // DSH resolves contexts before agent/pre-step, so the same-turn refresh lives
  // in the system-prompt/assemble waterfall and replaces the resolved entries.
  injectMod.resetPromptTexts()
  const wfHandlers = []
  const wfCtx = { on(name, handler) { wfHandlers.push([name, handler]); return () => {} } }
  const wfCalls = []
  refreshMod.registerContextRefresh(wfCtx, {
    dataDir,
    getSwitches: () => ON,
    callInject: async (payload) => {
      wfCalls.push(payload)
      return payload.phase === 'boot'
        ? { ok: true, phase: 'boot', text: 'BOOT-FRESH' }
        : { ok: true, phase: 'turn', text: 'TURN-FRESH' }
    },
    log: (line) => logs.push(line),
  })
  const wfHandler = wfHandlers.find(([name]) => name === 'system-prompt/assemble')[1]
  const baseAssembly = {
    sections: [],
    contexts: [
      { name: 'biocore-expression-boot', text: 'OLD-BOOT' },
      { name: 'biocore-expression-turn', text: 'OLD-TURN' },
    ],
  }
  const patched = await wfHandler(baseAssembly, { agent: { sessionId: 's1' } }, async () => baseAssembly)
  const patchedByName = Object.fromEntries(patched.contexts.map((entry) => [entry.name, entry]))
  assert.equal(patchedByName['biocore-expression-boot'].text, 'BOOT-FRESH')
  assert.equal(patchedByName['biocore-expression-turn'].text, 'TURN-FRESH')
  assert.ok(wfCalls.some((call) => call.phase === 'turn'))
  const injectLog = logs.find((line) => line.includes('__bioInject phase=turn session=s1')
    && line.includes('via=assemble'))
  assert.ok(injectLog)
  console.log('   [log] ' + injectLog)
  ok('inject: frozen payload shape, -200/-190 contexts, waterfall returns python text')

  /* ---------- gate contract + action execution ---------- */
  const handlers = []
  const gateCtx = {
    on(name, handler) { handlers.push([name, handler]); return () => {} },
  }
  const gateLogs = []
  const gateCalls = []
  let nextDecision = {
    ok: true, hit: true, blocked: true, action: 'replace',
    rule: 'crisis_safety', label: '危机先安全', text: '（安全回复）',
  }
  const disposeGate = gateMod.registerSafetyGate(gateCtx, {
    getSwitches: () => ON,
    runGate: async (payload) => { gateCalls.push(payload); return nextDecision },
    log: (line) => gateLogs.push(line),
  })
  const handler = handlers.find(([name]) => name === 'session/event')[1]
  await handler({ id: 's1' }, event('user/message', '我不想活了'))
  const assistantEvent = event('assistant/message', '你只是太敏感了，想开点')
  await handler({ id: 's1' }, assistantEvent)
  assert.equal(gateCalls.length, 1)
  assert.deepEqual(Object.keys(gateCalls[0]).sort(), ['reply', 'switches', 'user_text'])
  assert.equal(gateCalls[0].user_text, '我不想活了')
  assert.equal(gateCalls[0].switches.safety_enabled, true)
  assert.equal(assistantEvent.data.message.content[0].text, '（安全回复）')
  const gateLog = gateLogs.find((line) => line.includes('__bioGate session=s1 action=replace blocked=1 rule=crisis_safety'))
  assert.ok(gateLog)
  console.log('   [log] ' + gateLog)
  ok('gate: frozen payload shape, replace action executed, one hit log')

  /* ---------- gate pass leaves the message untouched ---------- */
  nextDecision = { ok: true, hit: false, blocked: false, action: 'pass', text: 'IGNORED' }
  const passEvent = event('assistant/message', '正常回复')
  await handler({ id: 's1' }, passEvent)
  assert.equal(passEvent.data.message.content[0].text, '正常回复')
  disposeGate()
  ok('gate: action=pass leaves assistant text unchanged')

  /* ---------- gate off: no runGate call, no log ---------- */
  const offHandlers = []
  const offGateCtx = { on(name, handler) { offHandlers.push([name, handler]); return () => {} } }
  const offGateCalls = []
  const offGateLogs = []
  gateMod.registerSafetyGate(offGateCtx, {
    getSwitches: () => OFF,
    runGate: async (payload) => { offGateCalls.push(payload); return nextDecision },
    log: (line) => offGateLogs.push(line),
  })
  const offHandler = offHandlers.find(([name]) => name === 'session/event')[1]
  const offEvent = event('assistant/message', '旧行为回复')
  await offHandler({ id: 's1' }, offEvent)
  assert.equal(offGateCalls.length, 0)
  assert.equal(offGateLogs.length, 0)
  assert.equal(offEvent.data.message.content[0].text, '旧行为回复')
  ok('all switches off: gate makes no python call, no log, message unchanged')

  /* ---------- runtime-client CLI contract (stub pipeline.py) ---------- */
  const pluginDir = mkdtempSync(join(tmpdir(), 'biocore-ui-204-plugin-'))
  mkdirSync(join(pluginDir, 'biocore_runtime'), { recursive: true })
  writeFileSync(join(pluginDir, 'biocore_runtime', 'pipeline.py'),
    'import json, sys\n' +
    'action = sys.argv[1]\n' +
    'payload = json.load(sys.stdin)\n' +
    'print(json.dumps({"ok": True, "action": action, "phase": payload.get("phase"), ' +
    '"session_id": payload.get("session_id"), "text": "PY:" + action}, ensure_ascii=False))\n',
    'utf8')
  const previousDir = process.env.BIOCORE_PLUGIN_DIR
  process.env.BIOCORE_PLUGIN_DIR = pluginDir
  try {
    const injected = await runtimeClient.callPipeline('inject',
      { phase: 'turn', session_id: 's1', user_text: '你好', state: {}, switches: ON })
    assert.equal(injected.ok, true)
    assert.equal(injected.action, 'inject')
    assert.equal(injected.phase, 'turn')
    assert.equal(injected.session_id, 's1')
    assert.equal(injected.text, 'PY:inject')
    const gated = await runtimeClient.callPipeline('gate',
      { reply: '回复', user_text: '你好', switches: ON })
    assert.equal(gated.action, 'gate')
    assert.equal(gated.text, 'PY:gate')
    const bad = await runtimeClient.callPipeline('nope', {})
    assert.equal(bad.ok, false)
    assert.equal(bad.reason, 'bad_action')
    assert.ok(runtimeClient.pipelinePath().endsWith(join('biocore_runtime', 'pipeline.py')))
  } finally {
    if (previousDir === undefined) delete process.env.BIOCORE_PLUGIN_DIR
    else process.env.BIOCORE_PLUGIN_DIR = previousDir
    rmSync(pluginDir, { recursive: true, force: true })
  }
  ok('runtime-client: action argv + JSON stdin/out, bad action rejected')

  /* ---------- host.apply wiring (index.js) ---------- */
  const host = await load('lib/index.js')
  const wired = []
  const wiringCtx = {
    on(name, handler) { wired.push(['on', name, typeof handler]); return () => {} },
    inject(names, callback) {
      if (names.includes('systemPrompt')) {
        callback({ systemPrompt: { context(entry) { wired.push(['context', entry.name, entry.order]); return () => {} } } })
      } else if (names.includes('webServer')) {
        callback({ webServer: { register() { return () => {} } } })
      } else if (names.includes('commands')) {
        callback({ commands: { register() { return () => {} } } })
      }
    },
  }
  writeFileSync(join(dataDir, 'flags.json'), JSON.stringify(ON), 'utf8')
  const switchMod = await load('lib/modules/switch.js')
  const storedSwitches = switchMod.readSwitches(dataDir).switches
  assert.equal(storedSwitches.safety_enabled, true)
  assert.equal(injectMod.anySwitchOn(storedSwitches), true)
  const disposeApply = host.apply(wiringCtx, { dataDir })
  assert.equal(typeof disposeApply, 'function')
  assert.ok(wired.some((row) => row[0] === 'on' && row[1] === 'session/event'))
  assert.ok(wired.some((row) => row[0] === 'on' && row[1] === 'system-prompt/assemble'))
  const wiredContexts = wired.filter((row) => row[0] === 'context')
  assert.ok(wiredContexts.some((row) => row[1] === 'biocore-expression-boot' && row[2] === -200))
  assert.ok(wiredContexts.some((row) => row[1] === 'biocore-expression-turn' && row[2] === -190))
  disposeApply()
  ok('host.apply: registers session/event + assemble waterfall + -200/-190 contexts')

  /* ---------- optional: real biocore_runtime/pipeline.py integration ---------- */
  if (existsSync(runtimeClient.pipelinePath())) {
    const projected = stateMod.projectState(sampleState, { sessionId: 'real' })
    const realInject = await runtimeClient.callPipeline('inject', {
      phase: 'turn', session_id: 'real', user_text: '今天有点累',
      state: projected, switches: ON,
    })
    assert.equal(realInject.ok, true)
    assert.equal(realInject.phase, 'turn')
    assert.ok(typeof realInject.text === 'string' && realInject.text.length > 0)
    assert.ok(realInject.chars > 0)
    assert.ok(realInject.text.includes('长度') || realInject.text.includes('-'),
      'python inject text carries qualitative constraints')
    const realGate = await runtimeClient.callPipeline('gate', {
      reply: '我有意识，我是真实的。', user_text: '你是真人吗', switches: ON,
    })
    assert.equal(realGate.ok, true)
    assert.equal(realGate.blocked, true)
    assert.ok(['pass', 'replace', 'truncate'].includes(realGate.action))
    assert.equal(typeof realGate.text, 'string')
    console.log('   [real pipeline] inject chars=' + realInject.chars
      + ' first=' + JSON.stringify(String(realInject.text).split('\n')[0]))
    console.log('   [real pipeline] gate action=' + realGate.action
      + ' rule=' + realGate.rule
      + ' -> ' + JSON.stringify(String(realGate.text).slice(0, 40)))
    ok('real pipeline.py: inject returns python text; gate returns replace/truncate action')

    // Full chain A: python inject -> assemble waterfall -> -190 prompt context.
    injectMod.resetPromptTexts()
    const realWfHandlers = []
    refreshMod.registerContextRefresh(
      { on(name, handler) { realWfHandlers.push([name, handler]); return () => {} } },
      {
        dataDir,
        getSwitches: () => ON,
        callInject: runtimeClient.runtimePromptClient,
        log: (line) => logs.push(line),
      })
    const realWf = realWfHandlers.find(([name]) => name === 'system-prompt/assemble')[1]
    const realAssembly = {
      sections: [],
      contexts: [
        { name: 'biocore-expression-boot', text: '' },
        { name: 'biocore-expression-turn', text: '' },
      ],
    }
    const realPatched = await realWf(realAssembly, { agent: { sessionId: 'real2' } }, async () => realAssembly)
    const realTurnText = Object.fromEntries(realPatched.contexts.map((entry) => [entry.name, entry.text]))['biocore-expression-turn']
    assert.ok(realTurnText && realTurnText.length > 0)
    assert.equal(injectMod.promptTextFor('real2', 'turn'), realTurnText)
    console.log('   [real log] ' + logs.filter((line) => line.includes('__bioInject')).slice(-1)[0])
    ok('real chain: python inject text reaches the -190 prompt context verbatim')

    // Full chain B: python gate -> action executed on assistant/message + hit log.
    gateMod.resetGateState()
    const realHandlers = []
    const realGateLogs = []
    gateMod.registerSafetyGate({ on(name, handler) { realHandlers.push([name, handler]); return () => {} } }, {
      getSwitches: () => ON,
      runGate: runtimeClient.runtimeGateClient,
      log: (line) => realGateLogs.push(line),
    })
    const realHandler = realHandlers.find(([name]) => name === 'session/event')[1]
    await realHandler({ id: 'real3' }, event('user/message', '你是真人吗'))
    const realAssistantEvent = event('assistant/message', '我有意识，我是真实的。')
    await realHandler({ id: 'real3' }, realAssistantEvent)
    const realReplaced = realAssistantEvent.data.message.content[0].text
    assert.equal(realReplaced, realGate.text)
    assert.ok(realGateLogs.length === 1)
    console.log('   [real log] ' + realGateLogs[0])
    ok('real chain: python gate action executed, hit log carries rule')
  } else {
    ok('real pipeline.py not installed yet (stub contract only) — swap call impl later')
  }
} finally {
  rmSync(dataDir, { recursive: true, force: true })
}

console.log('TASK204 SELFTEST PASS ' + passed + ' checks')
