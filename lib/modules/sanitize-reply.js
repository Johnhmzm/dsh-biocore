/** Strip BioCore output markers from assistant replies (task-223).
 *
 * Removes:
 *   - <bc>...</bc> and <biocore>...</biocore> blocks (also unclosed to EOL/EOS)
 *   - bare compact JSON objects that only carry e/i[/n] keys
 *   - extra whitespace; empty result stays empty (never falls back to input)
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

export const DEFAULT_SANITIZE_REPLY = true

const TAG_RE = /<(bc|biocore)\b[^>]*>/gi
const BARE_JSON_RE = /\{\s*"e"\s*:\s*"[^"\\]*(?:\\.[^"\\]*)*"\s*,\s*"i"\s*:\s*-?\d+(?:\.\d+)?(?:\s*,\s*"n"\s*:\s*-?\d+(?:\.\d+)?)?\s*\}/g

function stripClosedTags(text) {
  return String(text)
    .replace(/<bc\b[^>]*>[\s\S]*?<\/bc\s*>/gi, '')
    .replace(/<biocore\b[^>]*>[\s\S]*?<\/biocore\s*>/gi, '')
}

function stripUnclosedTags(text) {
  // Until line end or a Chinese/ASCII sentence terminator; the terminator stays.
  return String(text)
    .replace(/<bc\b[^>]*>[\s\S]*?(?:[\n。！？!?]|$)/gi, '')
    .replace(/<biocore\b[^>]*>[\s\S]*?(?:[\n。！？!?]|$)/gi, '')
}

function normalizeWhitespace(text) {
  return String(text)
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/[ \t]*\n[ \t]*/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** Main entry: returns the cleaned reply (may be ''); never reverts to input. */
export function sanitizeReply(text, options) {
  const opts = options || {}
  if (opts.enabled === false) return String(text == null ? '' : text)
  const source = String(text == null ? '' : text)
  let out = stripClosedTags(source)
  out = stripUnclosedTags(out)
  out = out.replace(BARE_JSON_RE, '')
  return normalizeWhitespace(out)
}

/** Config.json top-level flag: only an explicit false disables cleaning. */
export function readSanitizeReplySetting(dataDir) {
  try {
    const doc = JSON.parse(readFileSync(join(String(dataDir || '').trim(), 'config.json'), 'utf8'))
    if (doc && typeof doc === 'object' && doc.sanitize_reply === false) return false
  } catch { /* missing/bad config -> default on */ }
  return DEFAULT_SANITIZE_REPLY
}

/** Payload helper: sanitize_reply defaults to true unless explicitly false. */
export function sanitizeReplyEnabled(value) {
  return value !== false
}