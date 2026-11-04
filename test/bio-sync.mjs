/** Manual demo: run the real /bio sync handler against the local adapter.
 *
 * Usage:
 *   node test/bio-sync.mjs <session-id> "<一句话>" [state-dir]
 *
 * Prints the command result JSON (same text the tab would show after /bio sync).
 */
import { fileURLToPath, pathToFileURL } from 'node:url'
import { join } from 'node:path'
import { makeCommandHandler, resolveDataDir } from '../lib/index.js'

const sessionId = process.argv[2] || 'demo-session'
const text = process.argv[3] || '今天好累'
const stateDir = process.argv[4] || resolveDataDir({})

const handler = makeCommandHandler({ stateDir })
const result = await handler({
  rawInput: ' sync ' + text,
  agent: { session: { id: sessionId } },
})
console.log(JSON.stringify({
  session_id: sessionId,
  command: '/bio sync ' + text,
  result,
  state_file: join(stateDir, 'sessions', sessionId + '.json'),
}, null, 2))