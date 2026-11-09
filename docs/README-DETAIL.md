# DSH 插件：真实会话状态（per-session）+ 右栏 tab「生物核」+ 独立仪器面板

host 侧按会话读写状态（`<dataDir>/sessions/<session_id>.json`），通过子进程
调用 `dsh_adapter` 的 `load_state / one_turn / save_state`；client 侧右栏 tab
每 5 秒带 `session` 轮询，切换会话即切数据。任务 194 之后状态会真实变化：
DSH `session/event` hook 每轮调用一次 `one_turn`，另有 `/bio sync <一句话>` 兜底。
右栏 tab 是快览（含 24px sparkline），独立页是仪表盘（orb + 24h 曲线 + 记忆 +
关系/叙事指标 + 人设编译 + 四个开关）。task-213 起有两个入口：左侧栏「BioCore」
（根作用域 main keyed slot 内嵌 iframe）与右栏 tab。

## 文件

- `lib/index.js` host：`GET /api/biocore/state?session=<id>`（懒创建）、
  `session/event` hook、`/bio sync` 命令、`/api/biocore/switch`（task-195）、
  `GET/POST /api/biocore/persona`（task-208）。
- `lib/modules/state.js` 会话文件路径/校验、状态投影（v / intero 四通道 /
  tone / relation.stage / autonomy.state / memory_count / updated_at）、16KB 上限。
- `lib/modules/data-dir.js`（task-197）唯一的 dataDir 解析：sessions 与 flags.json
  必须落在同一目录。
- `lib/py-bridge.js` + `lib/py/bridge.py` 子进程桥：`ensure` / `turn` / `default` /
  `persona_compile`（文本走 stdin），只调用 dsh_adapter 与 mindbase 编译模块，
  不在 Node 里 import Python，不改 adapter 文件。
- `lib/modules/switch.js` task-195 四个开关（host settings 为权威）。
- `lib/client.js` 右栏 tab：心情主指标（大号 + 趋势箭头 + Δ）、四通道次指标、
  3 状态词、24px sparkline、四开关、底部「展开独立面板」；session 轮询。
  同一文件还注册左侧栏 `sidebar.panellist` 条目（图标 +「BioCore」）与根作用域
  `main` keyed slot（key `biocore-panel`，iframe 指向独立页，带当前 session）。
- `lib/modules/panel-data.js` /state 的附加字段（memory_recent / relationship_metrics /
  body_metrics / narrative / motivation / history）与独立页静态路由。
- `ui/panel.html` + `ui/panel.css` + `ui/panel.js` 独立仪器面板（原生 JS、
  CSS 变量 + prefers-color-scheme 双主题、无外部依赖）。
- `test/selftest.mjs`（45 checks）、`test/hook-wiring.mjs`（2 checks）、
  `test/bio-sync.mjs`（/bio sync 手动实测）。

## 状态怎么变

1) **DSH host hook `session/event`（可用）**：每条直接人类 `user/message` 都会
   触发一次 `one_turn` 并写回该会话状态。证据：
   - `@deepseek-ai/dsh-session/lib/types/index.d.ts` 声明
     `'session/event'(session, event)`；
   - 参考插件 `dsh-biocore-memory/lib/modules/auto-memory.js` 已用同一 hook，
     并从 `event.data.content` 取文本、用 `source.kind === "user"` 过滤注入；
   - 本仓库 `test/hook-wiring.mjs`：`apply()` 注册 hook 后，喂一条合成
     `user/message`，真实 python bridge 写出 `sessions/hook-sess-1.json`，
     `turn=1`（不是 mock）。
   运行时监听器只转发直接人类消息；assistant 消息、runtime-context 注入、
   非法 session id 都会被忽略（selftest 断言）。

2) **兜底命令 `/bio sync <一句话>`**：给没有 hook 的宿主/手动调试用，
   调同一个 bridge 更新当前会话，返回一行状态变化摘要（不产生模型消息）。

## 子智能体（delegationDepth >= 1）

core-turn 在组装 -190 注入前读取 session.header.delegationDepth（缺失按 0）：
- depth = 0 / 缺失：保持现行为，注入人设、状态块与思考规则。
- depth >= 1：仍调用 turn_start 维持状态/记忆/trace，但把本会话的 prompt_block
  置空，跳过人设/状态/思考规则注入；turn_end 的 gate/trace 照常。

persona preset 的常驻注入是宿主级（agent preset），插件无法按会话单独关掉；
本插件只保证 turn 注入对子智能体跳过。若 preset 仍向子智能体注入，需要在
宿主侧为 subagent 使用不含 persona 的 preset。

## 接口

`GET /api/biocore/state?session=<id>`

- 有会话：200 + 投影子集（含 `session_id`、`lazily_created` 当次懒创建）。
- 无 `session`：`{"available":false,"reason":"no_session"}`。
- session id 非法：`reason:"bad_session"`；文件坏：`reason:"bad_state"`；
  懒创建失败：`reason:"no_state_yet"`。
- 只读、无副作用、`cache-control: no-store`、载荷 ≤16KB。

示例：

```json
{"available":true,"session_id":"live-test-a","v":0.04,
 "intero":{"tension":0.2982,"warmth":0.5979,"heaviness":0.2965,"clarity":0.7047},
 "tone":{"label":""},"relation":{"stage":"熟悉"},"autonomy":{"state":"静"},
 "memory_count":0,"updated_at":1793711000533}
```

`GET/POST /api/biocore/switch`（task-195）：四个开关，host settings 为权威，
写入需要 `{key,value,confirm}`；详细键见 `lib/modules/switch.js`。

## 诊断：这一轮为什么（task-210）

`GET /api/biocore/trace?session=<id>&limit=10`

- 返回 `{ok:true, rows:[...], session_id, limit}`；`rows` 按时间**新→旧**排序。
- 数据源（只读）：`<store>/traces/<session_id>.jsonl`，由 Python
  `biocore_runtime/pipeline.py` 在 `turn_end` 时写入，最多保留最近 50 条；
  store 解析：`payload.store` > `BIOCORE_MEMORY_STORE` > `<plugin>/data/memory`。
- 文件不存在、不可读、单行坏 JSON：返回 `{ok:true,rows:[]}`（坏行跳过）；
  缺 `session`：`{ok:false,reason:"no_session",rows:[]}`；非 GET/HEAD：405。
- `limit` 默认 10，范围 1..50；session 名按 Python 同规则 sanitize，禁止路径穿越。
- 本端点不调用模型、不写文件、不影响对话行为。

单条 trace 字段清单（`rows[]`）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `ts` | number | 写入时间（epoch 秒） |
| `session_id` | string | 会话 id |
| `state_delta` | array | 只列**真正变化**的数值：`{field,before,after,source}`；`source` = `user` / `bio_tick` |
| `intent` | object | `{kind,weight,reason}`（意图类别、权重、原因） |
| `constraints` | object | 本轮表达约束（`length_scale`/`initiative`/`tone`/`allow_correction`/`sources` 等） |
| `length` | object | `{scale,source}`（长度缩放 + 来源，如 `state` / `self_layer`） |
| `gate` | object | `{action,rule,label}`；`action=pass` 即未命中 |
| `memory` | object | `{recalled_ids:[], captured_id}`（召回 id 列表 / 本轮写入的记忆 id） |
| `modules` | object | `{ran:[], skipped:[]}`（本轮运行/跳过的模块） |

独立页「这一轮为什么」：

- `ui/panel.html` 里可折叠的 `<details id="traceCard">`，默认收起；
- 拉 `/api/biocore/trace?session=<id>&limit=3`，每条显示 5 行：
  **状态变化**（`field before→after`，最多 4 个，其余 `+N`）、**意图**、
  **长度来源**、**闸命中**（`pass` 显示「未命中」）、**记忆**（召回 N / 写入 id）；
- 缺失字段显示 `—`，`state_delta` 为空显示「无变化」；
- 空状态统一写「还没有记录」，请求失败写「读取失败」。

## 两个入口（task-213）

- 左侧栏入口：客户端 `lib/client.js` 的 `apply()` 里注册
  `sidebar.panellist`（id=`biocore-panel`，order 20，label `BioCore`，图标 SVG）
  与根作用域 `main` keyed slot（同 key）。点击左侧行后，layout 自动切到该 main
  panel，渲染满高 iframe：
  - 有当前会话：`/api/biocore-ui/ui/panel.html?session=<session-id>`
  - 无会话：省略 query（独立页显示「没有会话参数」，不会报错）
  - 当前会话来自 slot 标准 `useSessions` 选择器（优先 `retainedBy.mainView>0`，
    其次 updatedAt 最新的会话，再没有就省略）。
  > 说明：DSH 的 slots 注册 API 在客户端插件（`ctx.slots`），与
  > `dsh-biocore-memory` 同一套；host `apply()` 只负责 `/api/biocore-ui/ui/*`
  > 静态路由（本次未改）。
- 右栏 tab：保留原样。底部「展开独立面板」改为 `ctx.layout.selectPanel('biocore-panel')`，
  切到同一个左侧入口（同一 iframe/同源 cookie/无弹窗拦截）；若宿主没有 layout
  服务（或选择失败）则退回 `window.open` 新标签，行为与旧版一致。
- 静态路由未改。

## 人设：提示词 -> 数值画像（task-208）

- 参考 AstrBot 侧格式：`persona_profile.json` = `{"persona": 原文, "profile": {...}}`，
  profile 为 18 个扁平字段：O/C/E/A/N、stress_reactivity、recovery_rate、
  social/energy/curiosity/safety baseline、da/oa/ht baseline、autonomy_gain、
  baseline_v/baseline_a、learning_rate。注入顺序照 skill：
  traits -> affect baseline -> base/autonomy/learning（白名单，不写记忆/事实/安全）。
- 编译用 `mindbase.persona_compile`（与 AstrBot 同字段/同顺序的确定性编译：
  Big Five 后 `refresh_numbers()` 推导生理/驱力/自主/学习率）；不新增 LLM 调用。
- 文件（与 flags/sessions 同 dataDir）：
  - `<dataDir>/persona.md` 原文
  - `<dataDir>/persona_profile.json` 编译结果（含 version / persona_hash / compiled_at）
- 端点：
  - `GET /api/biocore/persona` → `{ok,text,profile,updated_at,preview}`；
    缺文件 `ok:true, profile:null, reason:"no_persona"`；坏 JSON → `{ok:false,reason:"bad_profile"}`。
  - `POST /api/biocore/persona {text, confirm:true}` → 编译并写两个文件，返回
    profile + 9 项中文预览；错误：`empty_text` / `confirm_required` /
    `text_too_long`（上限 20000 字）/ `bad_payload` / `compile_failed`。
- adapter 读取：adapter 本身没有 profile 参数，由桥 `lib/py/bridge.py` 读取
  persona_profile.json，按白名单把 baseline_v/a、social/energy baseline、
  recovery_rate（映射半衰期）应用到会话 state（新会话或 persona_version 变化时），
  并在 state JSON 里持久化 `persona_profile` / `persona_version`；
  `turn` / `ensure` 都接受 profile 路径参数。
- UI：独立页「人设」区块 = 多行文本框（预填当前人设）+「保存并编译」
  （二次确认）+ 9 项中文数值预览；状态：未保存 / 编译中… / 编译失败：原因 / 已编译。

## 人设常驻：写入 DSH 预设（task-214）

- 调研结论（DSH 宿主代码 / profile 配置）：
  - 预设声明在 bundle 的 `presets/*.patch.yml`（`@deepseek-ai/dsh-agent-preset` 行）；profile 的
    `cordis.patch.yml` 按行 id 最后覆盖（`cordis.yml` 本身只是空数组）。
  - 预设字段：`config {id,name,description,order,plugins[]}`；人设是预设 plugins 里的
    `@deepseek-ai/dsh-persona` 行，字段 `{prefix,suffix,complete,includeRuntimeContext}`，
    prefix 渲染为 `deployment:persona-prefix` 并**遮蔽**部署级 `system-prompt.personaPrefix`。
  - 默认预设：registry 行 `agent-preset-registry.config {default, selectedDefault}`，
    生效默认 = `selectedDefault ?? default`（web-bio 当前是 `ptc`）。
  - 优先级：bundle patches → profile `cordis.patch.yml`（同 id 后写覆盖）→ 会话选择的预设；
    编辑预设只影响**之后创建**的会话，已存在 Agent 保留旧 revision。
- 写入方式：宿主 `ctx.configEditor.edit(entry, change)`（校验 + 文件锁 + 原子写 profile
  patch + Loader 热重载；profile 启动的进程才有该服务，headless 会返回 `host_unavailable`）。
- 端点 `/api/biocore/persona/apply`：
  - `GET` → `{ok, applied, preset_id, patch_path, chars, matches_preset, injection, has_backup,...}`；
    未应用 `reason:"not_applied"`，预设在外部被改过 `reason:"drift"`。
  - `POST {confirm:true}` → 读取 `<dataDir>/persona.md`（优先 persona_profile.json 原文），
    写入默认预设的 dsh-persona `prefix`；同时把 `dsh-biocore-memory` 行的
    `personaFile` 置空以关闭它每轮的人设注入（原值进备份，避免重复）。
  - `POST {confirm:true, rollback:true}` → 用备份恢复原预设 plugins 与原 personaFile。
  - 占位符转换：写入前把 AstrBot 人设占位符转成字面文本——`{{user}}`→「主管」、`{{char}}`→「<角色名>」
  （从 persona 标题「# 角色扮演：安吉拉（Angela）」解析，取不到用「她」）、其它 `{{xxx}}` 去掉大括号保留 xxx；
  转换后仍含 `{{`/`}}` 才拒绝（`template_unsafe`）。状态同时记录 `raw_chars` 与 `sanitized_chars`。
- 备份/状态（同 dataDir）：`persona_preset_backup.json`（首次应用前的完整预设 config +
  memory personaFile）、`persona_preset.json`（applied/preset/hash/chars/injection）。
- 面板「人设」卡新增「设为常驻模式」「恢复原人设」按钮（二次确认），并显示
  常驻状态/预设 id/字数/漂移提示。
- 说明：本插件当前没有每轮"人设文本注入"（只有 persona→数值画像，继续保留）；真正每轮
  注入人设文本的是 `dsh-biocore-memory.personaFile`，应用时自动关闭，回滚自动恢复。

## 独立页（仪器面板）

- 入口：右栏 tab 底部「展开独立面板」，或直接打开
  `/api/biocore-ui/ui/panel.html?session=<session-id>`（同源 cookie）。
- 静态路由：`GET/HEAD /api/biocore-ui/ui/*`（prefix，`cache-control: no-store`，
  仅 `panel.html` / `panel.css` / `panel.js` / `trace-view.js`，拒绝穿越与 POST）。
- 布局：96px CSS orb +「她现在的样子」+ 当前动机；**心情为 hero**（大号数字 +
  ↑/↓/→ 趋势箭头 + Δ）；绷紧/暖/沉/清 为一行次指标（小号 + Δ）；
  24h 心情曲线（canvas，单线 + 事件点，来自 /state 的 history，`/series` 不存在
  时自动回退）；最近 10 条记忆（每条 ≤80 字）；关系与叙事 5 个数字；
  底部四个开关（POST /api/biocore/switch 且 `confirm: true`）。
- 空状态三种文案（都带下一步建议）：
  - `no_state_yet` →「这个会话还没聊过」/「发一条消息后就有状态」
  - 请求失败 →「连不上宿主」/「确认 3081 上的 DSH 与插件已加载」
  - `bad_state` →「状态文件坏了」/「删掉该会话的 state 文件后重试」
- tab 侧 sparkline：高 24px、最多 60 个点（最近 60 个 v 样本）；详细曲线只在
  独立页。
- 颜色：neutral-bluish token（浅色/深色两套，跟随 `prefers-color-scheme`，
  也可用 `html[data-theme]` 强制）。

## 安装

```powershell
cd C:\Users\Administrator\Desktop\deepseek
dsh plugin --profile web add file:<path-to-dsh-biocore>
# 再把 "dsh-biocore-ui" 追加进 profile 的 package.json dsh.profile.bundles
dsh --profile web --port 3080
```

本地开发（改代码即生效）：把 profile 的 `node_modules/dsh-biocore-ui` 换成
指向本仓库的 junction/symlink（pnpm 的 `file:` 会拷贝快照，不会跟随源码）。

测试实例（不碰 3080）：

```powershell
dsh --profile web-test --port 3082 --no-open
```

## 数据目录（task-197 / task-201）

```yaml
- insert:
    - id: dsh-biocore-ui
      name: dsh-biocore-ui
      config:
        dataDir: ''   # 空 = 自动解析，见下
```

`resolveDataDir(config)` 优先级（`lib/modules/data-dir.js`）：

1. `process.argv` 里的 `--profile <name>` / `--profile=<name>`
   → `<homedir>/.dsh/profiles/<name>/data`（该 profile 目录存在时）
2. `config.dataDir`
3. 环境变量 `BIOCORE_UI_DATA_DIR`
4. `<DSH_PROFILE_DIR>/data` —— **仅当** `DSH_PROFILE_DIR` 目录里确实有
   `package.json` 或 `dsh.yml`（确认它是 profile 根）
5. `<plugin>/data`（仓库/部署副本自己的兜底，不再用 cwd）

> task-201 起因：DSH 给子进程的 `DSH_PROFILE_DIR` 会指向默认 profile（web），
> 即使实例是 `--profile web-bio`，之前因此把会话写进了 `profiles\web\data`。
> 现在先看启动参数里的 `--profile`，argv 存在时以它为准。

同一目录下的两个文件：

- `<dataDir>/sessions/<session_id>.json`  会话状态
- `<dataDir>/flags.json`                  四个开关

其它环境变量：`BIOCORE_ADAPTER_DIR`（adapter 目录，默认
`<pkg>/python/dsh_adapter`）、`BIOCORE_PYTHON`（默认 `python`）。
端口/启停仍由 profile 管理。

部署（`scripts\deploy_dsh_ui.ps1` 会同步 lib/package/cordis 并跑 selftest）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy_dsh_ui.ps1 -Profile web-bio -Port 3081
dsh --profile web-bio --port 3081 --no-open
```

注意：从另一个 DSH shell 里启动测试实例时，`DSH_PROFILE_DIR` 可能继承成别的
profile；argv 里的 `--profile` 现在优先，因此直接
`dsh --profile <name> --port <port>` 启动即可正确落到该 profile 的 `data`。

## 验证记录

### task-208：人设写入窗口 + 编译（web-bio / 3081）

- deploy → 重启 3081 → 带 cookie 调端点。
- `GET /api/biocore/persona`（初始）→ `{"ok":true,"text":"","profile":null,"updated_at":0,"reason":"no_persona"}`。
- `POST` 「亲近、俏皮、温暖、先接情绪、好奇；说话直一点，不装。」+`confirm:true`
  → 200；生成 `web-bio\data\persona_profile.json`（748 B，18 字段 + version）
  与 `persona.md`（80 B）；预览 9 项（外向 0.65 / 宜人 0.65 / 情绪基线 0.04 /
  唤醒基线 0.52 / 压力反应 0.5 / 恢复速度 0.75 / 社交基线 0.66 / 自主增益 0.98 /
  学习率 0.059），与文件一致。
- adapter 读取：新会话 `persona-live-probe` 懒创建后 `v=0.04`（=baseline_v）、
  social=0.66；state JSON 含 `persona_version`/`persona_profile`，
  `valence_decay_minutes=104.9`。
- 错误：空文本 400 `empty_text`、缺 confirm 400 `confirm_required`、
  >20000 字 400 `text_too_long`、坏 JSON GET → `{"ok":false,"reason":"bad_profile"}`。
- 截图：[人设面板截图](_persona_panel.png)（1170×1370，含文本框、已载入、保存并编译按钮、9 项预览）。
- selftest 45 checks（新增 7 条：GET 形状/POST 编译+文件一致/错误/坏 JSON/
  adapter 读取/UI 分支/路由注册）。

### task-213：左侧栏入口（未重启，Lead 验收）

- `lib/client.js` 注册 `main`（key `biocore-panel`）+ `sidebar.panellist`
  （id 同 key，label「BioCore」）；右栏 tab 保留；「展开独立面板」优先
  `ctx.layout.selectPanel`，无 layout 时退回 window.open。
- selftest 48 checks（新增 3 条：注册被调用、iframe URL 带 session、无会话不崩）。
- 按约束未重启 3081；静态路由未改，验收由 Lead 刷新客户端后确认两个入口。

### task-202：独立仪器面板（web-bio / 3081）

- deploy（脚本现在会同步 `lib/` + `ui/`）→ 重启 3081 → 带 cookie 打开
  `/api/biocore-ui/ui/panel.html?session=session-e68776cb-...`。
- 静态资源 200：panel.html 3727 B、panel.css、panel.js，均为 `no-store`。
- 截图：[面板截图](_panel_shot2.png)（1170×1060）可见：orb、
  心情 hero 0.04 ↑ +0.02、绷紧/暖/沉/清 次指标行（Δ ±0.00）、24h 曲线、
  关系/叙事数字、四个开关。
- 真实会话 `session-e68776cb-...` 有 3 轮 history（300/600/900），Δ 与曲线
  均来自 /state 的 `history` 字段（无 /series 时不阻塞）。
- selftest 38 checks（含新增 4 条：主次/Δ/sparkline/三种空状态）。

### task-201：dataDir 按 --profile 解析（web-bio / 3081）

- deploy 到 web-bio → 重启 3081 后端（启动参数 `--profile web-bio`，环境里
  `DSH_PROFILE_DIR` 仍是默认 web，用于验证 argv 优先）。
- 真实网页操作：headless Edge 打开 3081，新建会话并发送
  「今天有点累，但谢谢你」，DSH 正常回复（1 轮）。
- 会话状态写入：`profiles\web-bio\data\sessions\session-3cb5d52b-...json`
  （2981 B，mtime 21:32:30）；另一条 `session-29e0d216-...json` 同样在
  `web-bio\data`。
- 旧错误目录 `profiles\web\data\sessions` 未再新增文件（仍是修复前的
  `ready-check.json` / `session-72c50b00-...json`）。
- 面板实测（右栏「生物核」）：`已连接 · 9 秒前`，心情 0.02、绷紧 0.30、
  暖 0.60、沉 0.30、清 0.70、语气 正常、关系 熟悉、自主 静。
- `GET /api/biocore/state?session=session-3cb5d52b-...` → 上述数值；
  `flags.json` 仍在该 dataDir（118 B）。
- selftest 29 checks（新增 `--profile` 解析 / argv 缺省回退 / profile 不存在回退）。

### task-197：统一数据目录（web-bio / 3081）

- 部署：`scripts\deploy_dsh_ui.ps1 -Profile web-bio -Port 3081`
  → 同步 lib/package/cordis + selftest 25 checks 全过。
- 重启 3081（`dsh --profile web-bio --port 3081 --no-open`，
  显式 `DSH_PROFILE_DIR` 指向 web-bio）。
- dataDir 实际解析：`C:\Users\Administrator\.dsh\profiles\web-bio\data`
- `GET /api/biocore/state?session=deploy-verify`（懒创建）→ available:true
  → `web-bio\data\sessions\deploy-verify.json`（1410 B）
- `POST /api/biocore/switch {"key":"safety_enabled","value":true,"confirm":true}`
  → 200，`switches.safety_enabled=true`
  → `web-bio\data\flags.json`（118 B）
- 两个文件同处 `web-bio\data`：部署/备份只需这一个目录。

### task-194：per-session / hook（早期 web-test / 3082）

- 无 session → `{"available":false,"reason":"no_session"}`；坏 JSON → `bad_state`；
  `?session=..%2Fevil` → `bad_session`。
- `/bio sync 今天好累，但谢谢你` 实测：
  `v 0.02→0.04 · 压力 0.20→0.24 · 长度 1.00 · 主动性 0.46 · 允许改口 否`；
  会话 A 更新、会话 B 不变。
- `node test/selftest.mjs` → 25 checks；`node test/hook-wiring.mjs` → 2 checks。

## 自测

```powershell
cd C:\Users\Administrator\Desktop\deepseek\dsh-biocore-ui
node test/selftest.mjs        # 48 checks：投影/会话/数据目录/降级/命令/hook/开关/
                              #            client/panel/主次指标/Δ/sparkline/空状态/人设编译/
                              #            双侧入口（main keyed slot + sidebar.panellist）
node test/hook-wiring.mjs     # 2 checks：apply 真正注册 hook + 触发 one_turn
node test/bio-sync.mjs <session-id> "<一句话>"   # 手动跑一次 /bio sync
```

## 已知限制

- hook 是 DSH 0.2.x 的 `session/event`；宿主版本变化需复核（类型文件在
  `dsh-session/lib/types/index.d.ts`）。
- 单 host、单 python 进程桥：每次更新 spawn 一次 python（约 100-300ms），
  没做常驻服务；并发按 session 串行排队。
- tone / relation.stage / autonomy.state 仍是 JS 镜像投影（host 不跑 Python），
  与 adapter 的 Python 结果可能有小数级差异。
- 没有 per-session 的开关/设置（task-195 的开关是 host 级 settings）。
- 独立页曲线使用 /state 附带的 history（没有 /series 端点）；history 上限 120 点。
- 独立页需要与宿主同源的 cookie；从别的域名直接打开会拿不到 /api 数据。
- 左侧栏 main panel 的 session 来自 `useSessions` 标准选择器；宿主未提供该
  hook（或没有会话）时 iframe 省略 session 参数，独立页显示无会话提示。
- client tab 只展示快览（无记忆列表/诊断/详细曲线）；每 5 秒轮询、隐藏不暂停。
- 会话切换依赖宿主 slot 传入的 `sessionId`；没有会话的界面显示「未连接」。
- `updated_at` 取状态文件 mtime。
- 人设编译用 mindbase 的确定性编译器（同字段/同注入顺序），AstrBot 侧 LLM 编译的
  个别数值可能略有差异；人设按 dataDir 存一份（非 per-session），变更后对新会话
  或 persona_version 变化的会话生效。
- hook 里 one_turn 失败只打一行 `__bioTurnErr` 到宿主 stderr，不打断回合；
  若看到该前缀，说明 python bridge 或 adapter 报错（会话状态会保持上一次）。
