# 维护者说明

面向改这个仓库的人，不是使用者。

## 发版前

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check_release_hygiene.ps1
```

检查秘密、截图（白名单外一律拦下）、数据目录、超大文件；退出码 0 才允许发布。

## 推送后

用远端 SHA 与本地比对，不要相信 `git push` 的输出——被拒绝时它同样会打印带 `->` 的行：

```powershell
$l = (git rev-parse HEAD).Substring(0,7)
$r = (((git ls-remote --heads origin main) -split "\s+")[0])
$r.StartsWith($l)   # 必须是 True
```

## 验证顺序

```
pytest tests            全量，先绿
node host_e2e.mjs       走宿主链路：turn_start / memoryInject / turn_end
GET /api/biocore/stats  确认插件已被 profile 加载
真实对话                 确认状态行与记忆进入提示词
```

## 本地调试

```
python/biocore_runtime   核心：状态、记忆、注入
python/mindbase         生物底座：联想学习、噪声、生存筛选
lib/modules             宿主侧：payload 构造、注入接线
```

## 内部文档索引

| 文档 | 讲什么 |
| --- | --- |
| [docs/README-DETAIL.md](docs/README-DETAIL.md) | 接口、hook、文件结构、测试怎么跑 |
| [docs/DSH-PROACTIVE-FEASIBILITY.md](docs/DSH-PROACTIVE-FEASIBILITY.md) | 主动发起的可行性评估与取舍 |
| [HOST-WIRING](https://github.com/Johnhmzm/astrbot_plugin_biocore/blob/main/docs/HOST-WIRING.md) | 宿主链路图与常见断点 |
| [METRICS](https://github.com/Johnhmzm/astrbot_plugin_biocore/blob/main/docs/METRICS.md) | 三个分母、位置指标、二次精排结论 |
| [EMERGENCE](https://github.com/Johnhmzm/astrbot_plugin_biocore/blob/main/docs/EMERGENCE.md) | 涌现的可观测定义与实测条件 |

以上都是给维护者的，**不要放进 README**。README 只回答四个问题：这是什么、怎么工作、能做到什么、怎么装。


- [宿主链路与四处断点](https://github.com/Johnhmzm/astrbot_plugin_biocore/blob/main/docs/HOST-WIRING.md)
- [评估口径](https://github.com/Johnhmzm/astrbot_plugin_biocore/blob/main/docs/METRICS.md)
- [涌现的可观测定义](https://github.com/Johnhmzm/astrbot_plugin_biocore/blob/main/docs/EMERGENCE.md)
