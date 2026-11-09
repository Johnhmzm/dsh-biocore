# 开发期验收记录

（自旧 README-DETAIL 迁出，保留原始记录）

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
