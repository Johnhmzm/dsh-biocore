# 发版与验证

面向维护者。用户文档见 [README](README.md)，能力说明见 [docs/](docs/)。

## 发布前检查

```powershell
# 1 核心测试必须全绿
pytest tests -q

# 2 密钥 / 截图 / 数据目录 / 超大文件
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check_release_hygiene.ps1

# 3 包里不能有缓存与测试文件
npm pack --dry-run
```

第 3 步要确认：条目里没有 `__pycache__`、`*.pyc`、`tests/`、`CHANGELOG`、`CONTRIBUTING`。
打包白名单由 `package.json` 的 `files` 决定；目录内的额外排除写在各自目录的 `.npmignore` 里。

## 冒烟

在测试实例上确认三件事，缺一不可：

```
插件加载    GET /api/biocore/stats 返回 200
状态行      首行含数值、读法、以及「相对你平常」
整轮闭合    一轮对话后 turn_end 返回评分与 state_kl
```

只看接口 200 不够——要真的发一条消息，确认回复生成与数据落盘。

## 发布

```powershell
npm publish --access public
npm dist-tag add dsh-biocore@<version> latest
npm view dsh-biocore version    # 确认 latest 指向新版本
```

标签更新可能滞后，必要时手动 add 一次。

## 安装后

```
1  解包后确认 package.json 的 version 与发布版本一致
2  lib/ 与 python/ 与仓库逐字节一致（可对比 SHA256）
3  重启实例后重跑一次冒烟
```

## 仓库与发布包的区别

发布包只包含运行所需：`lib`、`python`、`ui`、`cordis.patch.yml`、`README.md`、`docs/`。
开发记录、问题排查笔记、基准脚本的说明不放进来或放在仓库根目录以外。
