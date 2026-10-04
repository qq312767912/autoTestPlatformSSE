---
name: 上证e投票测试执行
description: 基于上证e投票测试用例分流执行 UI 自动化、数据校验和人工留痕，回填结果并保存可复核证据。
version: 1.0.0
stage: test_execution
entrypoint: SKILL.md
input_schema: schemas/input.json
output_schema: schemas/output.json
permissions:
  - filesystem_read
  - filesystem_write
  - browser_automation
  - process_execute
---

# 上证e投票平台测试执行

本 Skill 承担四阶段测试链路中的**测试执行阶段**，将已通过门禁的用例分流为 UI 自动化、数据校验或人工执行，回填结果并保留证据。

## 前置门禁与安全约束

- 必须提供同一 `workflow_id` 下已通过门禁的 `test_cases.xlsx` 和用例集产出引用。
- 执行前核验环境可达、测试账号和数据就绪；前置不满足记为“阻塞”，不得误判“失败”。
- 生产环境禁止破坏性操作；凭据仅使用平台密钥/登录态引用，不写入脚本、日志、报告或配置。
- 默认串行；共享会议、股权处理等状态的数据用例不得并发。

## 执行流程

1. 按 `references/TestExecution.md` 将每条用例分为 UI 自动化、数据校验脚本或人工执行留痕；无法端到端观测的项目必须走人工复核，不伪装成自动化通过。
2. UI 用例参考 `templates/ui_case_template.py`，优先读取 `websiteFeature/vote.sseinfo.com.md` 中已验证策略；按预期结果构造断言，完整 URL 包括 query 参数。
3. 数据用例参考 `templates/data_check_template.py`，对照 `references/投票业务规则库.md` 校验表字段、唯一性、Kafka 消息及下游数据；将 SQL、结果集、消息原文或导出物留存至 evidence。
4. 人工用例留存截图/录屏，并记录执行人、时间、环境、申请单或会议等可复核标识。
5. 在 `test_cases.xlsx` 按用例编号回填 M 列结果与 N 列执行人；不更改 A~P 表头与历史行顺序。失败记录实际结果和证据路径，阻塞记录原因、责任域及解除条件。
6. 保留首次失败和重试轨迹；不得以重试通过覆盖初次失败。对不可自动验证结果标“需人工二次确认”，不直接计为通过。
7. 输出脚本、脱敏配置、截图、日志、证据和 `test_results.json`；执行门禁未通过时不得声称已完成。

## 结果状态

- `passed` / 通过：断言或人工核验均符合明确预期。
- `failed` / 失败：观测结果与预期不符，附失败步骤和证据。
- `blocked` / 阻塞：环境、数据或外部依赖不满足，说明责任域。
- `skipped` / 未执行：明确原因，不能并入通过数。

默认测试目录为 `reports/evote_{YYYYMMDD}/`；开始执行前确认本次输出目录。
