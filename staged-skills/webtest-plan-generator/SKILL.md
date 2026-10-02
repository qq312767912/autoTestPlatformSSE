---
name: webtest-plan-generator
description: 根据目标网站页面结构、需求文档和历史站点特征，生成可追溯的 Web 测试方案，覆盖正向、反向和边界场景，并输出统一协议所需的结构化摘要。
version: 1.0.0
stage: test_plan_generation
entrypoint: SKILL.md
input_schema: schemas/input.json
output_schema: schemas/output.json
permissions:
  - browser_read
  - filesystem_read
  - filesystem_write
---

# Web 测试方案生成

## 目标

将页面探索结果或需求文档转换为可评测、可追溯的测试方案。方案是全链路测试的第一阶段，质量门禁通过后才能进入测试用例生成。

## 输入

- `workflow_id`：本次全链路测试标识。
- `website_url`：目标网站地址。
- `requirements`：需求正文、文档引用或结构化需求，可选。
- `page_structure`：已有页面结构，可选；缺失时按 `references/PageExplorer.md` 探索。
- `historical_context`：历史缺陷、Badcase、站点特征和旧方案，可选。
- `output_root`：产物目录。

`requirements` 与 `page_structure` 至少提供一个。涉及登录时只接收平台托管的认证引用，不在产物中保存明文凭据。

## 执行

1. 使用 `references/RequirementAnalyzer.md` 提取功能点、规则、验收标准、异常与边界。
2. 必要时使用 `references/PageExplorer.md` 核验页面、交互元素和流程；优先复用 `websiteFeature/` 中的站点经验。
3. 按 `references/TestPlan.md` 生成方案，确保每个功能点至少考虑正向、反向和边界场景；无法覆盖时必须记录原因。
4. 建立需求/页面元素到方案编号的追溯关系，识别依赖、风险、测试数据和环境要求。
5. 输出 `test_plan.xlsx`，并同步输出符合 `schemas/output.json` 的结构化结果，供平台门禁和下一阶段消费。

## 质量约束

- P0 核心流程不得缺失。
- 每个末级方案项必须有唯一 `plan_item_id`、优先级、场景类型和来源引用。
- 不编造未知业务规则；存在冲突时写入 `open_questions`。
- 输出中必须给出 `coverage`，取值 0 到 1，并列出未覆盖项。
- 方案门禁未通过时不得触发测试用例阶段。

## 产物

- `test_plan.xlsx`
- `analysis/page_structure.json`（执行过页面探索时）
- `analysis/requirements_parsed.json`（输入含需求时）
- 结构化阶段产出 JSON

