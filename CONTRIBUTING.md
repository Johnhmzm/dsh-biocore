# 发版与验证

面向发布这个包的人。用户文档见 [README](README.md)，实现说明见 [docs/README-DETAIL.md](docs/README-DETAIL.md)。

## 打包白名单（会直接影响发出去的东西）

```
根目录 .npmignore 在 package.json 的 files 白名单下完全无效（实测：三种写法都拦不住）
有效做法：在每个被包含的子目录里放 .npmignore
  lib/py/.npmignore
  python/<每个包>/.npmignore   内容：__pycache__/ *.pyc tests/
```

发版前必查：`npm pack --dry-run` 里不得出现 `__pycache__`、`*.pyc`、`tests/`、`CHANGELOG.md`。
0.5.17 之前每版都带了 275 个 .pyc、238 个测试文件，包体积 3.1MB；修完 1.1MB。

## 发版前

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check_release_hygiene.ps1
```

检查密钥、截图（白名单外一律拦下）、数据目录、超大文件；退出码 0 才允许发布。

## 验证顺序

```
pytest tests            核心全量（701 项），先绿
node test/hook-wiring.mjs  宿主接线
npm run selftest        package 自检
GET /api/biocore/stats  确认插件已被 profile 加载
```

## 推送后

用远端 SHA 与本地比对，不要相信 `git push` 的输出——被拒绝时它同样会打印带 `->` 的行：

```powershell
$l = (git rev-parse HEAD).Substring(0,7)
$r = (((git ls-remote --heads origin main) -split "\s+")[0])
$r.StartsWith($l)   # 必须是 True
```

## 安装后确认

```
启动日志出现 __bioWorker started   插件已加载
/api/biocore/stats 返回 200       路由已注册
真实对话的状态行出现数值与读法      核心已生效
```

## 发版前置：必须先在 3081 冒烟

**没有在 3081 上跑过的一律不发。** 这一步能抓住三类问题：插件没被加载、包没装上、装上了但代码没生效。

```powershell
# 1) 起一个独立实例（不要动 3080，那是正在用的界面）
cd C:\Users\Administrator\Desktop\deepseek
$a = '/c start "" /b dsh --profile web --port 3081 --no-open'
Start-Process cmd.exe -ArgumentList $a -WindowStyle Hidden -RedirectStandardOutput .\3081.log -RedirectStandardError .\3081.err
Start-Sleep -Seconds 20

# 2) 从日志里取 token（形如 http://127.0.0.1:3081/?token=...）
Get-Content .\3081.log -Tail 3

# 3) 确认插件加载（两个都要 200）
$t = '<token>'
Invoke-WebRequest "http://127.0.0.1:3081/?token=$t" -UseBasicParsing -SessionVariable s | Out-Null
Invoke-WebRequest "http://127.0.0.1:3081/api/biocore/stats" -WebSession $s | Select-Object StatusCode
Invoke-WebRequest "http://127.0.0.1:3081/api/biocore/state" -WebSession $s | Select-Object StatusCode

# 4) 跑一轮真实对话，确认状态行出现数值与读法
# 5) 关掉实例
```

判定标准：

| 现象 | 结论 |
| --- | --- |
| 启动日志无 `__bioWorker started` | 插件未加载，检查 bundles |
| `/api/biocore/*` 404 | 同上 |
| 状态行只有文字没有数值 | 装的是旧包，检查 profile 里的版本号 |
| 状态行有数值但没「读法」 | 包是旧的，或进程未重启 |

任何一条不过，先修再发——**不要在 3080 上试，那是用户正在用的界面**。
