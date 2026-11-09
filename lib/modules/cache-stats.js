/** Display-only reader for <dataDir>/cache_stats.json.
 *
 * task-223: the adapter no longer computes usage-derived stats; the UI only
 * reads whatever the file already contains. Missing/corrupt -> zeros.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

export const CACHE_STATS_FILE = 'cache_stats.json'
export const CACHE_STATS_DEFAULTS = Object.freeze({
  turns: 0,
  prompt_tokens: 0,
  output_tokens: 0,
  cache_read: 0,
  cache_write: 0,
  hit_rate: 0,
  updated_at: 0,
})

function num(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0
}

export function cacheStatsPath(dataDir) {
  return join(String(dataDir || '').trim(), CACHE_STATS_FILE)
}

/** Read the file as-is (numbers default to 0); never throws. */
export function readCacheStats(dataDir) {
  try {
    const doc = JSON.parse(readFileSync(cacheStatsPath(dataDir), 'utf8'))
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
      return { ...CACHE_STATS_DEFAULTS }
    }
    return {
      turns: Math.round(num(doc.turns)),
      prompt_tokens: Math.round(num(doc.prompt_tokens)),
      output_tokens: Math.round(num(doc.output_tokens)),
      cache_read: Math.round(num(doc.cache_read)),
      cache_write: Math.round(num(doc.cache_write)),
      hit_rate: num(doc.hit_rate),
      updated_at: Math.round(num(doc.updated_at)),
    }
  } catch {
    return { ...CACHE_STATS_DEFAULTS }
  }
}
