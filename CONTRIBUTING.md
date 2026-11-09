# 发版与验证

面向发布这个包的人。用户文档见 [README](README.md)，实现说明见 [docs/README-DETAIL.md](docs/README-DETAIL.md)。

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
