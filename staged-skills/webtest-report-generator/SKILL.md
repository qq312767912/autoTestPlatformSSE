---
name: webtest-report-generator
description: 汇总同一全链路测试中的方案、用例与执行产出，生成可核验的 Web 测试报告、统计指标、失败分布和未闭环问题清单。
version: 1.0.0
stage: report_generation
entrypoint: SKILL.md
input_schema: schemas/input.json
output_schema: schemas/output.json
permissions:
  - filesystem_read
  - filesystem_write
---

# Web 测试报告生成

## 前置门禁

仅消费同一项目、同一 `workflow_id` 下已通过门禁的测试方案、测试用例和测试执行产出。三个上游产出 ID 缺一不可。

## 执行

1. 校验 `plan_output_ids`、`case_output_ids`、`execution_output_ids` 的归属和链路一致性。
2. 按 `references/TestReport.md` 汇总用例结果、截图、日志、失败轨迹和环境配置。
3. 计算覆盖率、通过率和失败分布，列出全部未闭环问题及责任域。
4. 生成便于人工阅读的 `test_report.html` 和供平台门禁消费的 `test_results.json`。
5. 报告正文必须是 JSON 对象，并满足平台 `workflow-report/v1` 契约。

## 报告正文硬契约

必须显式包含：

- `coverage`：0 到 1。
- `pass_rate`：0 到 1。
- `failure_distribution`：对象或数组。
- `unclosed_issues`：数组，即使为空也必须提供。
- 三类上游产出引用。

统计口径、人工验证项和阻塞项必须单独披露，不能通过排除失败样本虚增通过率。

