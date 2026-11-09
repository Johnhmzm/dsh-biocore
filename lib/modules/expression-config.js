/** Expression length-constraint settings stored in <dataDir>/config.json.
 *
 * config.json = { embedding: {...}, expression: {length_hint,length_target,length_max} }
 * Defaults are off / 10 / 30; the core turn_start payload receives the same
 * three fields.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

export const EXPRESSION_DEFAULTS = Object.freeze({
  length_hint: false,
  length_target: 10,
  length_max: 30,
})

export function expressionConfigFile(dataDir) {
  const dir = String(dataDir || '').trim()
  return join(dir, 'config.json')
}

function clampInt(value, low, high, fallback) {
  const number = Number(value)
  if (!Number.isFinite(number)) return fallback
  return Math.max(low, Math.min(high, Math.round(number)))
}

/** Normalize one expression block; missing fields fall back to defaults. */
export function normalizeExpression(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const target = clampInt(source.length_target, 1, 200, EXPRESSION_DEFAULTS.length_target)
  const max = clampInt(source.length_max, 2, 400, EXPRESSION_DEFAULTS.length_max)
  return {
    length_hint: source.length_hint === true,
    length_target: target,
    length_max: Math.max(target, max),
  }
}

/** Read config.json (missing / bad JSON -> defaults). */
export function readConfig(dataDir) {
  let doc = null
  try {
    doc = JSON.parse(readFileSync(expressionConfigFile(dataDir), 'utf8'))
  } catch {
    return { embedding: null, expression: EXPRESSION_DEFAULTS }
  }
  const source = doc && typeof doc === 'object' && !Array.isArray(doc) ? doc : {}
  return {
    embedding: source.embedding && typeof source.embedding === 'object' ? source.embedding : null,
    expression: normalizeExpression(source.expression),
  }
}

/** Expression block only (used by core-turn turn_start). */
export function readExpressionSettings(dataDir) {
  return readConfig(dataDir).expression
}
