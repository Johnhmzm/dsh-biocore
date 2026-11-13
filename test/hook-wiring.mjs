// 这些测试等的是 <store>/sessions/<sid>/memory.json：把存储模式钉成 json（缺省已是 sqlite）
process.env.BIOCORE_STORE = 'json'
/** Wiring evidence: apply() registers the session/event hook, routes and /bio command.
 *
 * Uses a fake cordis ctx and a temp stateDir, then feeds one synthetic direct
 * human user/message through the registered listener. Because the listener runs
 * the real python bridge, a session state file must appear: this proves the
 * hook path actually reaches dsh_adapter one_turn.
 */
import assert from 'node:assert/strict'
import { mkdtempSync, existsSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { apply, STATE_ROUTE_PATH, COMMAND_NAME } from '../lib/index.js'
import { SWITCH_ROUTE_PATH } from '../lib/modules/switch.js'

const stateDir = mkdtempSync(join(tmpdir(), 'biocore-hook-'))
const hooks = []
const routes = []
const commands = []
const fakeCtx = {
  on(name, listener) { hooks.push({ name, listener }); return () => {} },
  effect(run) { return run() },
  inject(names, callback) {
    const services = {}
    for (const name of names) {
      if (name === 'webServer') services.webServer = { register(entry) { routes.push(entry); return () => {} } }
      else if (name === 'commands') services.commands = { register(definition) { commands.push(definition); return () => {} } }
      else if (name === 'settings') services.settings = {}
    }
    callback(services)
  },
}
let passed = 0
function ok(name) { passed += 1; console.log('ok - ' + name) }

try {
  const dispose = apply(fakeCtx, { dataDir: stateDir })
  assert.equal(typeof dispose, 'function')
  const hook = hooks.find((item) => item.name === 'session/event')
  assert.ok(hook, 'session/event listener registered')
  const paths = routes.map((route) => route.path)
  assert.ok(paths.includes(STATE_ROUTE_PATH), 'state route registered')
  assert.ok(paths.includes(SWITCH_ROUTE_PATH), 'switch route registered (task-195)')
  assert.equal(commands.length, 1)
  assert.equal(commands[0].name, COMMAND_NAME)
  assert.equal(typeof commands[0].handler, 'function')
  ok('apply registers session/event, /api/biocore/state, /api/biocore/switch, /bio')

  hook.listener(
    { id: 'hook-sess-1' },
    { type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: '谢谢你，今天好多了' }] } },
  )
  // 会话状态现在由 <stateDir>/core_state.json 承载，per-session 文件不再写。
  const stateFile = join(stateDir, 'core_state.json')
  const deadline = Date.now() + 20000
  while (Date.now() < deadline && !existsSync(stateFile)) {
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  assert.ok(existsSync(stateFile), 'hook 触发了 python bridge 并写了 core_state.json')
  const saved = JSON.parse(readFileSync(stateFile, 'utf8'))
  const sessions = saved.sessions || saved
  const hasSession = Object.prototype.hasOwnProperty.call(sessions, 'hook-sess-1')
    || JSON.stringify(saved).includes('hook-sess-1')
  assert.ok(hasSession, 'core_state.json 里应含 hook-sess-1')
  ok('session/event hook -> adapter one_turn -> core_state.json 含该会话')

  dispose()
} finally {
  rmSync(stateDir, { recursive: true, force: true })
}
console.log('\nall ' + passed + ' checks passed')
