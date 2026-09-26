# Implementation Plan

- [x] 1. 明确深度模式覆盖与降级规则
  - 全量机器覆盖，OCR 为语义主审，平台 AI 仅补审失败文件
  - _Requirement: 1, 2, 3, 4_
- [x] 2. 重构深度模式任务编排
  - 去除全量重复 AI 调用并计算综合覆盖率
  - _Requirement: 2, 3, 4, 5_
- [x] 3. 扩充诊断与 HTML 报告
  - 展示避免重复审查、补审文件数及综合覆盖率
  - _Requirement: 5_
- [x] 4. 增加回归测试并完成构建验证
  - _Requirement: 1, 2, 3, 4, 5, 6_
