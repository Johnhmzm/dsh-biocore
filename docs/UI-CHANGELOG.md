# 面板 UI 变更记录

一行一条：改了什么 / 为什么 / commit / tag。回退：`git revert <commit>`，或 `git checkout <tag> -- ui/`。

| 日期 | 改了什么 | 为什么 | commit | tag |
|---|---|---|---|---|
| 2026-11-17 | 人设卡标注注入时机，并显示人设字数 | 0.6.7 只在开场注入一次（首轮 2000+ 字），之后每轮 250–700 字状态行；原卡片没有时间维度，读起来像每轮都带人设 | de2a33e | ui-persona-20261117 |
| 2026-11-17 | 召回卡改名「召回预算（工具）」，提示写明自动召回默认关闭、只有工具调用时才有【相关记忆】；trace 记忆列改为「工具召回」 | 0.6.7 召回改由 memory 工具按需调用，注入里不再每轮带记忆块；原卡片写着「每轮最多投放」会误导 | be0f010 | ui-recall-20261117 |
| 2026-11-17 | 表达卡去掉「目标字数 / 上限字数」与旧口径开关，改为「由提示词判断 / 不下发数字上限」 | 0.6.7 长度由提示词判断，面板不再下发 length_target / length_max；旧数字字段已无实际作用 | 0be882e | ui-length-20261117 |
| 2026-11-17 | 状态卡新增 SELF-PUSH 行，显示她上一句回写身体的 v / stress；lib 只读补 reverse_push（不改 python） | 0.6.7 新增反向注入（她说完回写身体），核心 /state 只投影数字，标记字段到不了面板 | c34635b | ui-reverse-20261117 |
| 2026-11-17 | 版本占位符不再写成 JS 标识符（window.BIOCORE_UI_VERSION） | 宿主把 panel.html 里所有 __BIOCORE_VERSION__ 做字符串替换，原来的 window.__BIOCORE_VERSION__ 会变成 window.0.5.5，页面每次加载都抛 SyntaxError | d1ccdda | ui-version-20261117 |
