---
name: agent-browser-skill
description: 基于 agent-browser CLI 的浏览器自动化工具。提供快照获取、元素交互、截图等功能。推荐用于需要页面快照分析、通过 ref 引用交互元素的场景。
allowed-tools: Bash(agent-browser:*), Bash(npx agent-browser:*)
---

# Agent Browser 浏览器自动化

基于 Vercel Labs 官方开发的 `agent-browser` CLI 浏览器自动化工具，专为 AI Agent（智能体）设计。

## ⚠️ 启动指引：动态获取最新指南

为了确保你获取的命令和工作流与当前系统安装的 `agent-browser` 版本（当前为 v0.27.0）完全匹配且不落后，**此文件仅作为引导存根（Stub），不作为主要的使用指南**。

在执行任何浏览器自动化操作之前，**你必须优先通过命令行动态加载最新的详细工作流和命令指南**：

```bash
# 1. 运行此命令获取核心工作流、常用模式和疑难解答（最重要！）
agent-browser skills get core

# 2. 运行此命令获取包含完整命令参数、修饰符和脚本模板的完整参考指南
agent-browser skills get core --full
```

---

## 快速导航
如果你需要处理非标准 Web 页面（例如桌面应用或特定协同工具），请通过以下命令获取专用技能指南：
* 自动化 Electron 桌面应用 (如 VS Code, Slack, Notion)：`agent-browser skills get electron`
* 自动化 Slack 工作区操作：`agent-browser skills get slack`
* 探索性 QA 测试 / 找 Bug 场景：`agent-browser skills get dogfood`

## 截图路径约定

必须优先使用系统注入的 `SCREENSHOT_DIR` 环境变量保存截图，不要手写 `/tmp/screenshots` 或 `./step1.png` 这类路径：

```bash
: "${SCREENSHOT_DIR:?SCREENSHOT_DIR 未设置}"
agent-browser screenshot "${SCREENSHOT_DIR}/case_11_step1.png"
```
