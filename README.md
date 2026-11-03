# DSH 插件骨架：右栏 tab「生物核」+ GET /api/biocore/state

## 验证记录（2026-11-03，web-test profile）

测试实例（不影响 3080 的 web 实例）：

```
dsh plugin --profile web-test add file:C:/Users/Administrator/Desktop/deepseek/dsh-biocore-ui
dsh --profile web-test --port 3082 --no-open
```

实际结果：

- 3082 监听成功，启动日志：`dsh web: http://127.0.0.1:3082/?token=...`
- `GET /api/biocore/state` -> 200：
  `{"available":true,"v":0.01,"intero":{"tension":0.2967,"warmth":0.5959,
  "heaviness":0.293,"clarity":0.709},"tone":{"label":""},
  "relation":{"stage":"熟悉"},"autonomy":{"state":"静"},
  "memory_count":0,"updated_at":1793709345042}`
- 删掉状态文件后 -> 200：`{"available":false,"reason":"no_state_yet"}`（恢复后重新可用）
- `POST /api/biocore/state` -> 405：`{"available":false,"reason":"method_not_allowed"}`
- 客户端模块已登记进启动清单：`{"id":"dsh-biocore-ui",
  "url":"plugins/??dsh-biocore-ui/client.js&rev=...","immediately":true}`；
  bundle 内含 `sidebarRightTabs`、`renderPanel`、`生物核`、`未连接`。
- `node test/selftest.mjs` -> `all 9 checks passed`。

打开页面：`http://127.0.0.1:3082/?token=<启动日志里的 token>`，进入会话后
打开右栏 -> `+` -> 「生物核」，即可看到 5 数值 + 3 词；状态文件缺失时显示
「暂无状态」，请求失败显示「未连接」。

## 说明


最小可用的 DSH 插件骨架：右栏 tab「生物核」+ 只读 `GET /api/biocore/state`。
host 侧读取 `dsh_adapter` 产出的状态 JSON 并投影成 UI 需要的子集；client 侧
每 5 秒拉一次，失败显示「未连接」，不弹错误，不引入外部依赖。

## 文件

- `lib/index.js`  host 侧：注册 `/api/biocore/state`（GET/HEAD，只读，16KB 上限）
- `lib/modules/state.js`  状态 JSON -> 投影（v / intero 四通道 / tone /
  relation.stage / autonomy.state / memory_count / updated_at）
- `lib/client.js`  右栏 tab（sidebarRightTabs + slots），首屏 5 数值 + 3 词
- `cordis.patch.yml`  插件插入层与默认配置
- `test/selftest.mjs`  Node 自测（投影、缺文件、路由注册、client 装载与渲染）

## 安装

```powershell
cd C:\Users\Administrator\Desktop\deepseek
dsh plugin --profile web add file:C:/Users/Administrator/Desktop/deepseek/dsh-biocore-ui
# 然后在 profile 的 package.json dsh.profile.bundles 里追加 "dsh-biocore-ui"
dsh --profile web --port 3080
```

测试实例（不碰 3080 在跑的实例）：

```powershell
dsh plugin --profile web-test add file:C:/Users/Administrator/Desktop/deepseek/dsh-biocore-ui
dsh --profile web-test --port 3082 --no-open
curl http://127.0.0.1:3082/api/biocore/state
```

打开右栏（聊天右侧）→ `+` → 「生物核」，或从右侧栏 tab 引导里打开。

## 配置

在 `cordis.patch.yml` 的插件配置里：

```yaml
- insert:
    - id: dsh-biocore-ui
      name: dsh-biocore-ui
      config:
        stateFile: C:/Users/Administrator/Desktop/deepseek/dsh_adapter/data/demo_state.json
```

- `stateFile`：状态 JSON 路径；留空时默认
  `<home>/Desktop/deepseek/dsh_adapter/data/demo_state.json`。
- 环境变量 `BIOCORE_STATE_FILE` 优先级最高，方便临时切换。
- 端口、插件启停等仍由 profile（cordis.patch.yml）管理。

状态 JSON 由 `dsh_adapter` 的 `save_state` 产出；示例：

```powershell
cd C:\Users\Administrator\Desktop\deepseek\dsh_adapter
python cli.py --reset --state data/demo_state.json
```

## 接口

`GET /api/biocore/state` -> 200 JSON：

```json
{
  "available": true,
  "v": 0.02,
  "intero": {"tension": 0.3, "warmth": 0.6, "heaviness": 0.3, "clarity": 0.7},
  "tone": {"label": ""},
  "relation": {"stage": "熟悉"},
  "autonomy": {"state": "等"},
  "memory_count": 0,
  "updated_at": 1762300123000
}
```

文件缺失：`{"available": false, "reason": "no_state_yet"}`；坏 JSON：
`reason: "bad_state"`。仅 GET/HEAD，其他方法 405；无写入、无模型调用、无网络。

## 已知限制

- 单文件、单会话：没有 per-session 状态，多会话会看到同一份演示状态。
- tone / relation.stage / autonomy.state 是 JS 侧对 `biocore_model` 纯函数的
  镜像投影（host 不跑 Python），与 adapter 的 Python 实现可能存在小数级差异。
- client 只做展示：无开关写入、无记忆列表、无诊断 tab、无曲线。
- 未做鉴权与 remoteAccess；假定 DSH 宿主自己的会话边界已够用。
- 每 5 秒轮询；隐藏时不会暂停（骨架版本，后续接侧栏可见性事件）。
- `updated_at` 优先取状态文件 mtime；文件系统精度决定其准确性。

## 自测

```powershell
cd C:\Users\Administrator\Desktop\deepseek\dsh-biocore-ui
node test/selftest.mjs
```