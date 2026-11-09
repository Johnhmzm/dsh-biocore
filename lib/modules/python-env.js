/** config.json -> Python env / rerank payload (read-only, fail-silent). */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

function readConfig(dataDir) {
  try {
    const doc = JSON.parse(readFileSync(join(String(dataDir || '').trim(), 'config.json'), 'utf8'))
    return doc && typeof doc === 'object' && !Array.isArray(doc) ? doc : {}
  } catch { return {} }
}

const MODES = new Set(['auto', 'remote', 'local', 'off'])

/** Env for embedding; local_path is intentionally not exported. */
export function embeddingEnv(dataDir) {
  const doc = readConfig(dataDir)
  const embedding = doc.embedding && typeof doc.embedding === 'object' ? doc.embedding : {}
  const mode = String(embedding.mode || 'auto').trim()
  const env = {
    BIOCORE_EMBEDDING_MODE: MODES.has(mode) ? mode : 'auto',
    BIOCORE_EMBEDDING_BASE_URL: String(embedding.base_url || '').trim(),
    BIOCORE_EMBEDDING_MODEL: String(embedding.model || '').trim(),
    BIOCORE_EMBEDDING_KEY: typeof embedding.api_key === 'string' ? embedding.api_key : '',
    // Cross-process disk cache so spawn-per-call still hits (task-221).
    BIOCORE_EMBEDDING_CACHE_FILE: dataDir ? join(String(dataDir).trim(), 'cache') : '',
    // Counters survive the spawn-per-call bridge; /stats reads this file.
    BIOCORE_EMBED_STATS_FILE: dataDir ? join(String(dataDir).trim(), 'embed_stats.json') : '',
  }
  for (const key of Object.keys(env)) if (!env[key]) delete env[key]
  return env
}

/** rerank_config for memory_inject / recall payloads. */
export function rerankConfig(dataDir) {
  const doc = readConfig(dataDir)
  const rerank = doc.rerank && typeof doc.rerank === 'object' ? doc.rerank : {}
  const top = Number(rerank.top_n)
  return {
    rerank_enabled: rerank.enabled === true,
    rerank_base_url: String(rerank.base_url || '').trim(),
    rerank_model: String(rerank.model || '').trim(),
    rerank_api_key: typeof rerank.api_key === 'string' ? rerank.api_key : '',
    rerank_top_n: Number.isFinite(top) ? Math.max(1, Math.min(100, Math.round(top))) : 20,
    rerank_timeout: 8,
  }
}
