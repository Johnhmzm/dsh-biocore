/** Memory store root only: policy lives in biocore_runtime/pipeline.py. */
import { join } from 'node:path'
import { resolveDataDir } from './data-dir.js'

export const MEMORY_SUBDIR = 'memory'

export function resolveMemoryDir(config) {
  return join(resolveDataDir(config), MEMORY_SUBDIR)
}
