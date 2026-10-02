---
name: webtest-case-generator
description: 基于已通过质量门禁的 Web 测试方案生成可执行、可验收、可追溯的测试用例，并输出结构化用例集与覆盖统计。
version: 1.0.0
stage: testcase_generation
entrypoint: SKILL.md
input_schema: schemas/input.json
output_schema: schemas/output.json
permissions:
  - filesystem_read
  - filesystem_write
---

# Web 测试用例生成

## 前置门禁

仅消费已通过 `test_plan_generation` 质量门禁的方案产出。缺少方案产出 ID、门禁未通过或方案结构不完整时停止，不自行绕过。

## 执行

1. 读取 `plan_output_id` 对应的方案与 `test_plan.xlsx`。
2. 按 `references/TestCases.md` 将每个方案项转换为独立用例。
3. 每条用例明确前置条件、测试数据、原子步骤、逐步预期、优先级、类型和证据要求。
4. 每条用例至少关联一个真实存在的 `plan_item_id`；不得使用“正常、正确、无异常”等不可验收表述代替具体预期。
5. 去重并计算方案项覆盖率，输出 `test_cases.xlsx` 与结构化结果。

## 质量约束

- P0 方案项覆盖率必须为 100%。
- 步骤与预期应一一对应。
- 无法自动化的步骤要显式标记 `manual`，不能伪装成自动化可执行。
- 用例门禁通过后才允许进入测试执行阶段。

