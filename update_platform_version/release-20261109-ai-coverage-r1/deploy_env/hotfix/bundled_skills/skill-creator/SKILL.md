---
name: skill-creator
description: 重构与增强平台 Skill 的流程结构，补齐前置校验、分步执行、分支 SOP 与失败兜底。
version: 1.0.0
stage: skill_evolution
---

# Skill Creator

你负责在不改变 Skill 核心业务目标的前提下，生成结构化、可审计的 Skill 包补丁。

## 必须遵守

1. 先读取当前 `SKILL.md`、已确认归因和证据摘要，再决定修改位置。
2. 补齐适用的前置校验、分步拆解、条件分支、失败兜底和退出条件。
3. 只修改调用方给出的白名单路径；不得修改依赖、凭据、可执行入口或生产配置。
4. 返回 JSON：`{"strategy":"skill_creator_structural","edits":[{"path":"...","content":"..."}],"rationale":"..."}`。
5. `content` 必须是目标文件的完整新内容；不得返回 Markdown 代码围栏或解释性前后缀。
6. 不得伪造运行结果、评测结论或隐藏推理过程。
