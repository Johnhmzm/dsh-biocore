# DSH 主动消息可行性（task-205）

结论：**可行**。DSH 没有“插件直接发消息”API，但宿主提供了一条被内置 schedule 使用的受认可路径：
把带稳定 id 的 user-role 消息放进目标 Session 并唤醒其 Agent，模型随后生成回复发给用户。
本仓库 `dsh-biocore-ui` 已按最小版本落地：默认全关，需显式打开两个 config 开关并重载插件。

## 1. 宿主能力探测（证据）

| 问题 | 结论 | 证据（宿主包，profile 内 node_modules） |
|---|---|---|
| Session 能否直接 send | 不能。Session 是事件溯源日志，只有 append 类事件 API | @deepseek-ai/dsh-session/lib/index.js:1324（class Session）、:1517（append）；SessionStore.get/list/create/fork 见 lib/types/index.d.ts:414/491/496/513 |
| 主动投递先例 | **有**：内置 dsh-schedule 到期时 resolveAgent -> createUserMessage -> agent.followup -> sessions.flush | @deepseek-ai/dsh-schedule/lib/index.js:1576 / :1586 / :1596 / :1597；dsh-command-goal / dsh-headless / api-session-controller / subagent 也有 followup 先例 |
| 合成消息会被拒吗 | 不会。user/message 只要求稳定 id + 非空 source.kind | @deepseek-ai/dsh-session/lib/index.js:1200（id）、:1205（source.kind 字符串） |
| 自定义 source kind | 合法（merge-extensible，未知 kind fall-through） | @deepseek-ai/dsh-llm/lib/types/message.d.ts:94-99 |
| 定时器安全吗 | 安全。宿主 scheduler 用 setTimeout(...).unref()；本插件用 setInterval + unref + disposer | dsh-schedule/lib/index.js:1641-1645 |
| 最近活跃会话 | 本插件在 `registerProactive` 里挂 `session/event`，只看 user/message 且 source.kind==='user'，维护最近人类会话 | lib/modules/proactive.js（recordActivity/getActiveSession） |
| 状态 / 待办来源 | **核心 pipeline 决策**：biocore_runtime/pipeline.py 的 proactive_tick 读取 dsh_adapter session state（relationship/trust）与 biocore_runtime 记忆库（承诺线索）；Node 只把结果发出去 | biocore_runtime/pipeline.py（proactive_tick）；lib/py-bridge.js（proactiveTick）；lib/py/bridge.py（proactive_tick 命令） |

## 2. 实现（决策在 Python，Node 只执行发送）

- **核心 pipeline**：`biocore_runtime/pipeline.py` 新增 action `proactive_tick`：
  输入 `{session_id, last_human_ts, switches, store, now?}` →
  输出 `{ok, should_send, text, reason, gap_minutes}`。
  间隔/每小时上限/冷却/信任/待办全部在 Python 判定；should_send=true 时同时把冷却写进
  `<store>/proactive.json`（Node 不再做任何条件判断或状态写入）。
- `lib/modules/proactive.js`（只剩执行）：
  - 活动注册表：`session/event` 的 user/message（source.kind==='user'）→ recordActivity(sessionId, now)；
  - 每 `proactiveIntervalMinutes`（默认 30，5–240）一次 `setInterval`，`unref()`，disposer 清理 timer 与 hook；
  - `runOnce`：取最近活跃会话 + last_human_ts → 调 `proactiveTick` → `should_send=false` 直接返回；
  - `deliver`：`ctx.sessionController.resolveAgent(sessionId)` → `agent.followup({id(uuid), role:'user', source:{kind:'biocore-proactive'}, content:[{type:'text'}]})` → `ctx.sessions.flush`。
- `lib/py-bridge.js` + `lib/py/bridge.py`：只保留 `proactive_tick` 通道（JSON payload 进 pipeline.dispatch）。
- `lib/index.js`：`// 5) proactive` 段（在 `// 4) memory` 之后），只在 `config.proactiveEnabled === true` 时 `ctx.inject(['sessionController','sessions'], ...)` 注册；默认不装 timer。
- `cordis.patch.yml`：新增 `proactiveEnabled: false`、`proactiveSend: false` 及 interval/minGap/perHour/minTrust/maxChars/requireOpenLoop 默认值。
- 测试：插件仓库 `biocore_model/tests/test_runtime_proactive.py`（18 个 pipeline 决策测试，经 bridge 端到端验证）；本仓库 `test/proactive.test.mjs`（10 个 Node 接线测试：payload 转发/should_send 执行/不决策/关闭回退）。

## 3. 发送条件（全部满足）

1. `config.proactiveEnabled === true` 且 `config.proactiveSend === true`（默认都 false）；
2. 有最近活跃会话（重启后需先有一条真实人类消息）；
3. 距该会话最后人类消息 ≥ `proactiveMinGapMinutes`（默认 180）；
4. 最近 1 小时发送 < `proactivePerHour`（默认 2），且距上次发送 ≥ 同一 gap；
5. 桥读取的 `trust` ≥ `proactiveMinTrust`（默认 0.6）；
6. 存在待办承诺（`proactiveRequireOpenLoop=true`，默认；优先 due_hint=soon）→「你之前提过「X」，后来怎么样了？」（≤ `proactiveMaxChars`，默认 60）。

## 4. 启用方式（默认关闭）

修改 `cordis.patch.yml` 或 profile 里的插件配置：

```yaml
config:
  proactiveEnabled: true
  proactiveSend: true
  proactiveIntervalMinutes: 30
  proactiveMinGapMinutes: 180
  proactivePerHour: 2
  proactiveMinTrust: 0.6
  proactiveMaxChars: 60
  proactiveRequireOpenLoop: true
```

`proactiveEnabled=false` 时不安装 timer、不发送、不写 `proactive.json`（完全回退旧行为）。

## 5. 已知限制 / 风险

- 投递的是宿主唯一认可的 **user-role 合成消息**（与 dsh-schedule 提醒同机制）；Web 会话记录里可能看到一条“用户消息”，这是宿主行为，不是本插件 UI。
- 依赖 python 子进程桥（dsh_adapter + biocore_runtime）：桥失败/服务缺失时静默跳过，不影响正常聊天。
- 重启后活动注册表为空，需要一条真实人类消息才会恢复“最近活跃会话”（避免向未知会话乱发）。
- 合成消息带稳定 uuid，满足 2026-10-30 “lacks an identified message” 事故后宿主对合成消息 id 的校验。
