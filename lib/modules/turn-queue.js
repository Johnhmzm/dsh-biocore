/** Per-session serial queue for one_turn refreshes (shared by hooks).
 *
 * session/event fires the async one_turn update; agent/pre-step awaits the
 * same session's pending update so the prompt context sees the new state.
 * Pure in-memory bookkeeping, no IO.
 */
const PENDING = new Map()

/** Run work after the session's previous turn update; returns the promise. */
export function queueSessionTurn(sessionId, work) {
  const id = String(sessionId == null ? '' : sessionId)
  const run = typeof work === 'function' ? work : () => Promise.resolve(null)
  const previous = PENDING.get(id) || Promise.resolve(null)
  const next = previous.then(run).catch(() => null)
  PENDING.set(id, next)
  const settle = () => {
    if (PENDING.get(id) === next) PENDING.delete(id)
  }
  next.then(settle, settle)
  return next
}

/** Resolve when the current pending turn for the session is done. */
export function pendingSessionTurn(sessionId) {
  const id = String(sessionId == null ? '' : sessionId)
  return PENDING.get(id) || Promise.resolve(null)
}

export function resetTurnQueue() { PENDING.clear() }
