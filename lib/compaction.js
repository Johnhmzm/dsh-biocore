/** 宿主入口：把 BioCore 的压缩产物接到 ctx.compaction 上。
 *
 * 继承官方 BasicCompactionEngine，只覆盖 summarize()——触发、区间、锁、溢出恢复
 * 全部是官方的。挂法与官方实现完全一样（profile 的 cordis.patch.yml）：
 *   1) - id: compaction-basic ... disabled: true
 *   2) - insert: [{ name: 'dsh-biocore-ui/compaction', config: { ...官方同名配置项 } }]
 *
 * 官方配置项全认（thresholdRatio/headroomTokens/retainRatio|retainTokens/
 * summarizationProvider/summarizationModel/maxTokens/compactionRetries/
 * maxOverflowRetries/modelPolicies/auto）；本文件额外只关心 store（降级路径的库位置）。
 */
import BasicCompactionEngine from '@deepseek-ai/dsh-compaction-basic'
import { BlockAssembler, LlmError, contentHasImage } from '@deepseek-ai/dsh-llm'
import { createCompactionEngineClass } from './modules/compaction-engine.js'
import { resolveDataDir } from './modules/data-dir.js'
import { join } from 'node:path'

export const ENGINE_NAME = 'biocore-index'

/** 降级路径要读的库：<dataDir>/memory（与插件其他部分同一个 store）。 */
export function memoryStoreOf(config) {
  const options = config && typeof config === 'object' ? config : {}
  const explicit = String(options.store || '').trim()
  if (explicit) return explicit
  try {
    return join(resolveDataDir(options), 'memory')
  } catch {
    return ''
  }
}

export default createCompactionEngineClass({
  BasicCompactionEngine,
  BlockAssembler,
  LlmError,
  contentHasImage,
  store: memoryStoreOf(process.env.BIOCORE_UI_STORE || ''),
})
