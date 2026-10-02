---
name: webtest-execution-runner
description: 将已通过门禁的 Web 测试用例转换为自动化脚本并安全执行，采集日志、截图、失败轨迹和结构化结果，支持后续归因与报告生成。
version: 1.0.0
stage: test_execution
entrypoint: SKILL.md
input_schema: schemas/input.json
output_schema: schemas/output.json
permissions:
  - browser_automation
  - process_execute
  - filesystem_read
  - filesystem_write
  - network_access
---

# Web 测试执行

## 前置门禁

仅执行已通过 `testcase_generation` 门禁的用例集。默认禁止在生产环境执行破坏性操作；登录凭据只通过平台密钥引用注入，不写入脚本、日志或截图元数据。

## 执行

1. 读取 `case_output_id` 和 `test_cases_artifact`，校验用例 ID 与方案追溯关系。
2. 按 `references/TestScripts.md` 生成 Playwright/Selenium 脚本；优先复用 `websiteFeature/` 的稳定定位与等待策略。
3. 按配置执行，采集每个步骤的状态、耗时、日志和证据；失败时保存失败节点、实际值、期望值和错误类型。
4. 区分 `passed`、`failed`、`blocked`、`skipped`，禁止将“仅验证跳转”冒充完整业务验证。
5. 输出可重放脚本、脱敏配置、截图、日志及结构化执行结果，为轨迹归因和报告阶段提供输入。

## 质量约束

- 所有执行结果必须关联 `case_id`。
- 重试前后结果分别保留，不能用最终重试通过覆盖初次失败轨迹。
- 阻塞项必须给出阻塞原因和责任域。
- 执行门禁通过后才允许生成正式报告。

