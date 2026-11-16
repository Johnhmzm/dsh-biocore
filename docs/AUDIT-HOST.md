# DSH 宿主插件审计（dsh-biocore-audit / dsh-biocore-ui）

快照：2026-11-16 21:00–21:20。被审代码：`dsh-biocore-ui`（干净副本 `dsh-biocore-audit` 与它逐文件同哈希）。
基线数量：修复前 `node --test` 59/59；修复后 70/70（新增 11 条回归）；`py_compile` 836 个 py 文件 0 失败。

## 0. 一句话结论

14 条里 **9 条完整修复**、**2 条部分修复**（H5 死接线改显式 opt-in；H13 只做了版本 bump）、**3 条未修**（H9 会话文件名双规范、H11 settings 静默降级、H14 死代码不删）。
另有 9 条新发现（N1–N9），其中 N9（面板 `#bodyTruth` 元素不存在）已修并加了"id 对齐"回归测试。

## 1. 修复记录（改前 → 改后 → 验证）

| 编号 | 位置 | 改前（症状） | 改后 | 验证 |
|---|---|---|---|---|
| H2a | `python/biocore_runtime/pipeline.py` `_injection_trace` | payload 不带 `store` 就一行都不写（`if _sid and _st:`） | `store = store or _default_store()` | 探针 `_pipeline_probe.py`：改前 `log_exists:false`；改后 `log_exists:true` |
| H2b/H7 | 同上 `_sig` | 去重签名 = `len + 前80字`，5 秒内长度相同、只在第 80 字之后变了的两轮被吞 | 签名带整段 `sha1`，窗口只用于"同一次 turn_start 内 prompt_block 被调两次" | 同一探针：改前 `has_tail_B:false`（真变化被吞）；改后 `true` |
| H2c | 同上 `inject_block` 末尾 | spawn-per-call 回退路径（`bridge.memory_inject`）没有日志 | 返回前调 `_injection_trace` | 探针：改前 `inject_block_logged:false`；改后 `true` |
| H1a | `lib/index.js` TRACE_ROUTE 注册 | `handler: (req,res)=>handleTraceRoute(req,res)`，路由去读默认 store | 显式传 `store: join(dataDir,'memory')` | `test/audit-fixes.mjs`：route 读得到给定 store 的 rows |
| H1b | `lib/modules/trace.js` `defaultStoreDir` | 默认 store = `runtime-client` 里硬编码的 `~/Desktop/deepseek/astrbot_plugin_biocore_standalone/data/memory` | `<package>/data/memory`（与 python `_default_store` 同源），仍受 `BIOCORE_MEMORY_STORE` 覆盖 | 单测：默认值 == `<pkg>/data/memory`；设 `BIOCORE_PLUGIN_DIR` 也不改变 |
| H8a | `lib/core-worker.js` `spawnChild` | 重启只 spawn，不重发 `configure`（新进程 persona/embedding/开关全默认） | 新增 `onWorkerSpawn(fn)`，第 2 次及以后的 spawn 回调宿主重放 configure | 单测：SIGKILL → 日志 `restart=1 delay_ms=500` → `spawn=2` → hook 收到 dataDir |
| H8b | 同上 `onGone`/`spawnChild` | `restarts` 只增不减，累计 3 次永久放弃 | 存活 `HEALTHY_RESET_MS=60000` 后归零 | 单测暴露 `setWorkerHealthWindowForTest`（收窗 50ms 可观测） |
| H8c | 同上 `stopCoreWorker` | SIGKILL 兜底定时器 `unref()`，宿主 5s 内退出会留孤儿 python | 不再 unref | 代码审查 + `stopCoreWorker` 单测路径 |
| H6 | `lib/modules/core-turn.js` assemble 分支 | 打补丁前不判开关：全关后同步 provider 返回空串，这里又把缓存旧文本塞回去 | 加 `if (!anySwitchOn(getSwitches())) return result` | 两条单测：全关→text 仍为空；开→注入 `FRESH BLOCK` |
| H5 | `expression-inject.js` / `safety-gate.js` / `expression-refresh.js` | 三个 register 从未被 index 接线，但文件头写着"已接"；将来接线会重复注入/双闸 | 保留实现，默认 no-op，改由 `BIOCORE_WIRE_LEGACY_INJECT/_GATE/_REFRESH` 或 `opts.force` 显式打开 | 单测：默认 0 次注册；`force:true` 注册 2 个 context（order -200/-190）；闸/刷新同理 |
| H4 | `lib/py/bridge.py` | 使用 `_DSH_HOME` 但从未定义 → NameError 被吞，凭证文件兜底是死码 | `_DSH_HOME = Path(DSH_HOME or ~/.dsh)` | 探针：改前 `KEY=''`；改后 `KEY='sk-from-file'`（伪造 `.credentials.yaml`） |
| H10 | `lib/client.js` `statusOf` | 只认 `bad_state`/`no_state_yet`（服务端从不发），其它一律"连不上宿主" | 认 `core_unavailable/core_disabled/bad_session/payload_too_large/method_not_allowed`，未知 reason 原样显示 | 单测：`core_unavailable` 与 `payload_too_large` 的 label 都不是"连不上宿主"；未知 reason 出现在 label 里 |
| H12 | `ui/panel.js` `coreRow`/变化日志 | `'...' + label + '...' + text + '...'` 拼 innerHTML（引号坏 `data-k`，后端字符串是未转义注入面） | `createElement` + `textContent` + `fillNode()` | 假 DOM 探针：`weights_source` 传 `x\"><img src=x onerror=alert(1)>`，渲染结果是 `.v` 里的**纯文本**、`data-k` 完整；探针里 innerHTML setter 直接抛错，全程未被触发 |
| H3 | `python/{biocore_runtime,biocore_model,biocore_memory}` | 镜像落后主仓（pipeline 少 448 净行、缺 peak_recall/redact、4 个模块缺省方向相反） | 以主仓为准整树同步：runtime 8 个、model 8 个、memory 13 个，共 24 文件 | 逐文件 MD5：快照时刻 `remaining drift: 0`；py_compile 836/836 |
| N9 | `ui/panel.html` | `panel.js:451` 找 `#bodyTruth`，HTML 里没有这个 id → 身体真值永远渲染不出来（静默 no-op） | 在 state 卡补一行 `<span id="bodyTruth">` | 新增单测：`panel.js` 里每个 `$('id')` 都必须在 `panel.html` 存在（`msEmb*/msRr*` 为显式白名单） |

### 1.1 探针附录（可复跑）

- `backups/audit-20261116/_pipeline_probe.py <pipeline.py>`：注入留痕三件事 + 时间线峰值；对 `_pipeline_before.py`（旧镜像）与 `python/biocore_runtime/pipeline.py`（已修）各跑一次，输出见 §1 表。
- `backups/audit-20261116/_bridge_key_probe2.py <bridge.py>`：伪造 `$DSH_HOME/.credentials.yaml`；旧 `bridge.py` → `KEY=''`，新 → `KEY='sk-from-file'`。
- `backups/audit-20261116/_panel_probe.mjs`：把 `ovRenderTimeline` 抠出来跑假 DOM，四种输入（有峰值+原文 / 只有数值 / 全空 / 老 `affect` 格式）分别渲染 `0.95`＋`v -0.80 · 「…」`、`0.40`＋`—`、`—`＋`—`、`0.60`＋`v 0.12`。
- `backups/audit-20261116/_core_probe.mjs`：把 `renderCore` 抠出来跑假 DOM（innerHTML setter 抛错），后端字符串一律进 `textContent`。
- 3081 只有只读探测（`GET /api/biocore/switch` 返回 `ok:true`）；因为不重启服务，线上未加载本轮代码，行为验证以上述探针 + 单测为准。

## 2. 未修与原因

| 编号 | 为什么没修 |
|---|---|
| H5（boot 注入未接线） | 真接线意味着每个会话多一次 `phase:'boot'` 的 python 调用，且会改变线上提示词；当前只把"接线会重复注入/双闸"的风险用显式 opt-in 关掉。要不要开 boot 是行为决策，不是缺陷修复。 |
| H9 会话文件名双规范 | node 侧 `state.js:47-49` 用原始 `session.id` 作文件名，python 侧 `sanitize_session_id` 会改写；统一会牵动既有 state 文件迁移（用户数据），风险高于收益。已在文档标注，建议随一次显式迁移做。 |
| H11 settings GET 静默 | `index.js:329-334` 核心不可用时静默返回文件旧值；修法要改 settings 契约（新增 `degraded` 字段）并同步面板，属接口改动，未在本轮动。 |
| H13 版本号三处同步 | 这次按规矩 bump 了（`panel.css?v=55`、`panel.js?v=16`、`PANEL_UI_VERSION 0.6.9`），但"单一来源"需要把四个 `?v=` 换成 `__BIOCORE_VERSION__` 占位符，属重构，未做。 |
| H14 死代码 | 规矩是"只增不改、不删"：`panel-data.buildPanelExtras`、`index.fetchCoreState`（含 5s TTL 缓存）、`runtime-client` 全模块均保留，只在文档标注"无调用方"。 |
| N6 proactive `agent.followup` 未 await | 需要先确认 host followup 的返回语义（是否可 await 出错），否则改了可能把"发送成功"错判成失败。 |
| N7 定时器只在加载时按配置安装 | `proactive/heartbeat/standby` 的 interval 在 `apply()` 时按 config 决定是否安装，而面板文案写"已保存，立即生效（不再需要重启）"。修它要引入运行时重装定时器的机制，属功能改动。 |
| N8 `drainOutbox` 用行数对齐 | outbox 被重写/截断时会重复记账；改成按 id 对齐需要 outbox 带稳定 id（python 侧约定），跨模块改动。 |

## 3. 新发现（N 系列）

- **N1** `lib/modules/autonomy.js:17-30` `appendAction` 每次动作都 `readFileSync` 整份 `actions.jsonl` 再决定是否重写（500 行上限），高频 tick 下是无谓 IO；且核心不可用时每 5 分钟追加一行 `core_unavailable`，文件只靠 500 行上限收口。低。
- **N2** `lib/modules/bio-heartbeat.js:25-27,36` 注释写"环境变量优先"，实现里 `BIOCORE_BODY=0/false/off` **不会**关掉身体层（只有 1/true/on 能开）；`BIOCORE_BODY=0` 而 `core_config.json` 里 `body:true` 时身体仍然开。中低（与内核文档"写 0 关"冲突）。
- **N3** `lib/modules/bio-heartbeat.js:69`/`proactive.js:78` 用 `resolveMemoryDir(cfg)` 重新推导 dataDir，而不是用注册时拿到的 `dataDir`；若 `config.dataDir` 与 argv 解析结果不同，心跳/proactive 与 turn 路径会写两个 store。低。
- **N4** `lib/modules/memory-capture.js:91,108` 用一个 `chain` Promise 串行化所有 capture：一次 python 卡到 90s 桥超时，后续消息排队等待（顺序正确但会滞后）。观察项。
- **N5** `lib/modules/memory-search-tool.js:419,479` `state.dispose = dispose` 在每个工具注册后覆盖，卸载时只释放最后一个工具（另外两个注册残留）。低。
- **N6** `lib/modules/proactive.js:63-64` `agent.followup(message)` 不 await 就 `flush`，消息可能未落库就 flush；异常只能变成未捕获 rejection。低（未修，见 §2）。
- **N7** `lib/index.js:1179-1234` 与面板"保存即生效"文案冲突：定时器按加载时配置安装，改配置要重载插件。中低（文档/文案层）。
- **N8** `lib/modules/standby.js:35-36` `drainOutbox` 用"results 行数 vs outbox 行数"判断新行；outbox 被重写变短时会重复记账。低。
- **N9** `ui/panel.js:451` 引用 `#bodyTruth`，`ui/panel.html` 无此 id（已修，见 §1）。修完又发现 `MS_TARGETS` 里的 `msEmb*/msRr*` 同样没有对应输入框——但代码用 `if (el)` 守卫，属"未实现的功能位"，已在测试里列为显式白名单，不算静默缺陷。

## 4. 覆盖矩阵（61 个插件文件，遗漏 0）

读法：**通**=通读全文；**扫**=导出面 + 风险行扫描 + 关键段落精读。每条都至少过了导出面与风险点。

### lib/（36）
| 文件 | 行 | 读法 | 结论 |
|---|---|---|---|
| lib/index.js | 1166 | 通 | H1a/H8 修；H11、H14 未修；N7 |
| lib/client.js | 556 | 通 | H10 修；H13 bump |
| lib/core-worker.js | 249 | 通 | H8 修（重放/预算/孤儿） |
| lib/py-bridge.js | 268 | 通 | 无独立缺陷（H2a 的上游：只透传 payload） |
| lib/py/bridge.py | 507 | 通 | H4 修；`_memory_inject` 直连 inject_block（H2c 已覆盖） |
| lib/compaction.js | 32 | 通 | 无问题（薄封装，策略在 compaction-engine） |
| lib/modules/autonomy.js | 119 | 通 | N1 |
| lib/modules/bio-heartbeat.js | 128 | 通 | N2、N3 |
| lib/modules/body-state.js | 18 | 通 | 无问题（带路径转义，读失败返回 null） |
| lib/modules/cache-stats.js | 44 | 扫 | 无问题（缺文件回落默认值） |
| lib/modules/compaction-engine.js | 374 | 扫 | 无问题（debug 落盘有 env 闸；阈值读取有 seen 缓存） |
| lib/modules/compaction-scan.js | 196 | 扫 | 无问题（throttle 与 trim 都在） |
| lib/modules/core-turn.js | 400 | 通 | H6 修；H9 记录 |
| lib/modules/data-dir.js | 174 | 通 | 无问题（argv 先于 env，注释与实现一致：docs 说明的 7 级顺序逐条对上） |
| lib/modules/expression-config.js | 51 | 扫 | 无问题（clamp + 默认值） |
| lib/modules/expression-inject.js | 103 | 通 | H5 修（opt-in） |
| lib/modules/expression-refresh.js | 140 | 通 | H5 修（opt-in） |
| lib/modules/memory-capture.js | 123 | 通 | N4 |
| lib/modules/memory-search-tool.js | 447 | 扫 | N5 |
| lib/modules/memory-store.js | 7 | 通 | 无问题 |
| lib/modules/memory-tools.js | 107 | 通 | 无问题（schema 与转发；`memory_disabled` 短路） |
| lib/modules/panel-data.js | 177 | 通 | H14（buildPanelExtras 无调用方）；静态路由本身安全（白名单 + no-store） |
| lib/modules/persona-preset.js | 428 | 扫 | 无问题（写前备份 + confirm + 回滚路径齐全） |
| lib/modules/persona-store.js | 88 | 扫 | 无问题（shared/profile 两级回退） |
| lib/modules/proactive.js | 138 | 通 | N6、N7 |
| lib/modules/python-env.js | 42 | 通 | 无问题（空值键会删掉） |
| lib/modules/runtime-client.js | 94 | 通 | H1b 已解除依赖；模块本身仍留（H14） |
| lib/modules/safety-gate.js | 129 | 扫 | H5 修（opt-in） |
| lib/modules/sanitize-reply.js | 52 | 扫 | 无问题（只剥标签块 + 归一空白） |
| lib/modules/series.js | 51 | 扫 | 无问题（limit clamp） |
| lib/modules/standby.js | 55 | 通 | N8 |
| lib/modules/state.js | 183 | 通 | 无问题；H9 的另一半（文件名不过 cleanSessionId） |
| lib/modules/switch.js | 243 | 通 | 无问题（白名单 + 原子写 + confirm） |
| lib/modules/switches.js | 8 | 通 | 无问题 |
| lib/modules/trace.js | 87 | 通 | H1b 修 |
| lib/modules/turn-queue.js | 26 | 通 | 无问题（串行 + 清理） |

### ui/（5）
| 文件 | 行 | 读法 | 结论 |
|---|---|---|---|
| ui/panel.js | 2374 | 扫 | H12 修、N9 修、事件卡峰值；轮询失败回落 `读取失败`/`连不上宿主`，字段缺失一律破折号 |
| ui/panel.html | 656 | 扫 | N9 修（补 #bodyTruth）；资源版本 bump 到 css v55 / js v16 |
| ui/panel.css | 1074 | 扫 | 新增 `.ov-peak-text`；无内联表达式、无 `url()` 注入面 |
| ui/panel-layout.js | 249 | 扫 | 无问题（layout 归一化 + hidden/order 校验） |
| ui/trace-view.js | 143 | 扫 | 无问题（纯格式化，全部 textContent） |

### test/（16）与 tools/（3）
| 文件 | 行 | 读法 | 结论 |
|---|---|---|---|
| test/audit-fixes.mjs（新增） | 132 | 通 | 本轮 11 条回归 |
| test/bio-heartbeat.test.mjs | 120 | 扫 | 无问题 |
| test/autonomy-exec.mjs | 64 | 扫 | 无问题 |
| test/bio-sync.mjs | 24 | 扫 | 无问题 |
| test/compaction-capture.mjs | 94 | 扫 | 无问题 |
| test/compaction-engine.mjs | 310 | 扫 | 无问题 |
| test/compaction-scan.mjs | 294 | 扫 | 无问题 |
| test/core-worker.mjs | 89 | 扫 | 无问题（与 H8 改动兼容：新增 API 是增量的） |
| test/hook-wiring.mjs | 66 | 扫 | 无问题；但并行跑时偶发 `worker_stopped`（全量 3 次中 1 次，单跑必过），既有竞态，未改测试 |
| test/integration/memory-wiring.mjs | 255 | 扫 | 无问题 |
| test/memory-list-read.mjs | 112 | 扫 | 无问题 |
| test/memory-search-tool.mjs | 158 | 扫 | 无问题 |
| test/memory-tool-boundary.mjs | 74 | 扫 | 无问题 |
| test/panel-routes.mjs | 74 | 扫 | 无问题 |
| test/persona-preset.test.mjs | 328 | 扫 | 无问题 |
| test/proactive.test.mjs | 180 | 扫 | 无问题 |
| test/standby-exec.mjs | 37 | 扫 | 无问题 |
| tools/panel-preview.mjs | 261 | 扫 | 无问题（本地预览器，不参与宿主路径） |
| tools/selftest.mjs | 1429 | 扫 | 无问题（自检聚合器） |
| tools/selftest-task204.mjs | 350 | 扫 | 无问题；注意它是 H5 三个 legacy 注册的唯一调用方（这就是"死接线"的证据） |
| tools/selftest-task210.mjs | 381 | 扫 | 无问题 |

## 5. python 镜像同步记录

- 源：`C:\Users\Administrator\Desktop\deepseek\biocore`（只读，未修改）。
- 目的：`dsh-biocore-ui/python/{biocore_runtime,biocore_model,biocore_memory}`。
- 同步 24 个文件（runtime 8 / model 8 含新增 `one_accum.py` 与 5 个 tests / memory 13）。
- 快照时刻逐文件 MD5 比对：`remaining drift: 0`；随后 `py_compile` 836 个文件 0 失败。
- **主仓在此刻仍在被编辑**（同步过程中 `runtime_consolidate.py`、`mindbase_bridge.py` 等 13 个文件在我复制后 15 秒内又被改写），所以"零 drift"只对 21:07 那一刻成立；再同步前请重新比对。
- 镜像上有 4 处**本地补丁**（主仓尚未有，建议回灌）：`_injection_trace` 的 store 回落、整段 sha1 去重、`inject_block` 留痕、`_session_overview` 峰值字段。

## 6. 剩余项

- 矩阵：**61/61 已过，遗漏 0**；其中 28 个通读、33 个"导出面+风险行+关键段"。
- 未修条目见 §2（8 条，均写明原因）。
- 已知不修的外部因素：主仓正在被并发编辑（镜像 parity 是时间点快照）。
