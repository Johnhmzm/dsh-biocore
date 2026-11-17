# 升级到 0.5.20：行为变化与回退

> 面向从 0.5.19 及更早版本升级的实例。**旧数据不需要迁移**；下面这些是行为变化，逐条给出开关与回退方式。

## 一、不需要做的事

- 会话目录：新旧命名（`<id>` 与 `session-<id>`）都能读，代码按“谁有数据用谁”选择，不迁移、不重命名。
- 旧会话缺新文件（`appraisal_vars.json`、`affect_intensity.json`、`relationship_history.jsonl` 等）：首次写入时创建；读不到就走退回路径，日志里会说“退回”，不会报错。
- `memory.db` 未改表结构；新增字段只写进 `events.payload` 的 JSON，旧库照样可读。

## 二、会看到的变化（逐条可关）

| 变化 | 现象 | 关掉 |
|---|---|---|
| 人设只在开场注入一次 | 首轮 2000+ 字，之后每轮 250–700 字 | `BIOCORE_PERSONA_BOOT_ONLY=0` |
| 召回不再自动进注入 | 注入里不再有【相关记忆】；回忆改由 memory 工具按需调用 | `BIOCORE_RECALL_IN_INJECT=1` |
| 长度改由提示词判断 | 注入里不再有“通常 N 字以内” | `BIOCORE_LENGTH_NATURAL=0` |
| 反向注入开启 | 她说完后自身状态会变（`turn_end` 回写） | `BIOCORE_REVERSE_INJECT=0` |
| LUST 默认开启 | 亲密系统参与七系统读数 | `BIOCORE_LUST=0` |
| 跨会话摘要 | 新会话第一拍带一段“来自既往会话” | `BIOCORE_SUMMARY_SHARED_READ=0` |
| 未变化时不出状态行 | 无字段变化的轮次注入更短 | `BIOCORE_NO_CHANGE_SILENT=0` |

## 三、硬门槛

- DSH `>=0.2.0 <0.3.0`，Node `>=22`；低于此版本不加载。
- 需要 DSH 的 `web` 档（插件依赖 `webServer` 服务）；纯 headless 档不会启动插件。

## 四、回退

删掉 profile 下 `node_modules/dsh-biocore` 再装 0.5.19 即可；新版本写的文件（`appraisal_vars.json` 等）旧版本不认识也不会读，不影响运行。
