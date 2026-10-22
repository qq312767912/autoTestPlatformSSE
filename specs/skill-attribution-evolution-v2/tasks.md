# Skill 自进化 V2 实施计划

- [x] 1. 建立归因词表与数据模型
  - 建立一级归因、二级标签、允许修复目标的单一服务端词表。
  - 扩展 `FailureAttribution`，新增三元组、证据状态、影响面与诊断协议版本。
  - 增加兼容性迁移，历史归因保持可读。
  - 增加词表和模型单元测试。
  - _Requirements: R1, R6_

- [x] 2. 构建 Critic 证据包
  - 从已确认报告生成可定位的 `report_row` 证据。
  - 从 output/trace/span 收集输入快照、决策摘要、工具调用、检索和输出比对证据。
  - 为证据生成稳定 ID/指纹，大 Payload 只保留引用与脱敏摘要。
  - 增加证据可定位与项目权限测试。
  - _Requirements: R2, R6_

- [x] 3. 将候选生成升级为 V2 Critic 协议
  - 改造分批 Prompt 和语义归并 Prompt，输出三元组、证据引用与影响面。
  - 在落库前执行枚举、修复路由、证据解析、去重与置信度验证。
  - 返回已丢弃候选的数量和原因，不将它们落成可采纳归因。
  - 保留旧 `category` 返回字段供兼容。
  - 增加非法标签、无证据、不可定位证据和全量分批回归测试。
  - _Requirements: R1, R2_

- [x] 4. 升级人工归因闸口
  - 扩展确认 API，支持 `accept/relabel/reject`，保留 `edit` 别名。
  - Re-label 联动校验一级归因、二级标签、修复目标和诊断描述。
  - 保留 Critic 原始结论和人工操作轨迹。
  - 拦截未确认或证据未验证的归因进入补丁生成。
  - _Requirements: R3, R6_

- [x] 5. 实现按修复目标路由的可执行补丁
  - 实现 `skill_sop/prompt/few_shot` 的最小 Skill 包内补丁。
  - 实现 `tool_schema/workflow/code/knowledge/rag` 的隔离试验副本和可执行补丁。
  - 扩展 `OptimizationProposal.change_patch` 为 `skill_patch/v2` 结构。
  - 增加影响文件、公共能力、Token 变化和风险级别评估。
  - 新增 taxonomy 和 patch-preview API 及权限/路由测试。
  - _Requirements: R4, R6_

- [x] 6. 改造候选审核界面
  - 在第 3 步展示“原始信号 -> 一级归因 -> 二级原因 -> 修复目标”。
  - 新增证据抽屉，展示证据来源、摘要和可定位状态。
  - 将“改写”升级为 Re-label 级联编辑。
  - 显示已丢弃诊断统计，不给无证据项提供采纳操作。
  - 增加候选补丁的 diff 和影响面预览。
  - 遵循已确认的 Industrial / utilitarian 设计规格与现有 Arco 品牌 token。
  - _Requirements: R2, R3, R4_

- [x] 7. 实现完整执行进化与 A/B Mutation
  - 从同一精确 Skill 基线自动派生 `exp.A` 与 `exp.B` 两个不可变试验分支。
  - Candidate A 执行最小扰动补丁。
  - Candidate B 调用预置 `skill-creator`，注入前置检查、分步拆解、分支 SOP 和兜底流。
  - 为 A/B 变体分配独立 sandbox、版本、补丁、指纹、影响面与回滚点。
  - 禁止变体互相污染，并验证 active Skill 指纹不变。
  - _Requirements: R5, R6_

- [x] 8. 实现 Baseline/A/B 本阶段对照评测
  - 使用相同输入在三个隔离运行上下文中重跑 Baseline/A/B。
  - 展示三者产出、失败、时延、Token、修复命中与新增退化的原始对比数据。
  - 由人工选择 winner 并填写对比理由；未选中变体保留试验记录。
  - 未完成重跑或未选出 winner 时禁止进入发布审批。
  - _Requirements: R5, R6_

- [x] 9. 接入发布与 Hub 归档闸口
  - 只将已确认、证据已验证且属于 Skill 包内的补丁交给派生服务。
  - 继续从当次审查实际选中的精确 Skill 版本派生。
  - 只允许人工选中的 A/B winner 进入发布审批。
  - 验证 active Skill 指纹在所有候选/失败路径中不变。
  - _Requirements: R5, R6, R7_

- [x] 10. 完成端到端回归与部署
  - 运行模型、API、候选生成、审核、派生与兼容性测试。
  - 运行 Vue 类型检查和生产构建。
  - 用真实已确认报告验证全量分批、三元组、证据与 Re-label 流程。
  - 同步到当前运行容器并验证健康状态。
  - _Requirements: R1, R2, R3, R4, R5, R6, R7_
