# 项目级质量飞轮与阶段门禁 - 技术设计

## 信息架构

- 总览：项目人员、待办、覆盖率和阻塞流程。
- 单次能力：用例审查、代码审查、知识问答。
- 全链路测试：测试方案 -> 测试用例 -> 测试执行 -> 报告生成。
- 公共资产：金标、评测、归因、优化发布。

## 后端

- `WorkflowStageGate` 持久化项目、workflow_id、阶段、产出、L0-L3 分数、门禁状态和人工决策。
- `WorkflowGateService` 根据评测结果计算门禁，并校验下一阶段是否可进入。
- `operations/cockpit` 聚合项目成员、按类型金标、单次能力统计和流程实例。
- `operations/evaluate-workflow-stage` 运行确定性门禁计算。
- `operations/override-workflow-stage` 仅允许 owner/admin 留痕放行。
- 统一产出协议在声明 `enforce_quality_gate` 时执行真实前置门禁检查。
- 四阶段产出均关联 `CapabilityDefinition/CapabilityRelease`，评测失败后进入统一归因和 Skill 候选版本流程。

## 前端

- 左侧按“总览 / 单次能力 / 链路质量 / 公共资产”组织。
- 右侧成员栏展示真实项目人员。
- 链路使用横向阶段轨道，明确通过、失败、待测评和阻塞状态。
- 金标先按业务类型筛选，再显示版本和分区统计。

## 安全与兼容

- 查询继续按项目成员隔离。
- 强制放行仅 owner/admin。
- 历史适配器默认兼容；只有显式开启门禁的链路产出才被前置阻断。
