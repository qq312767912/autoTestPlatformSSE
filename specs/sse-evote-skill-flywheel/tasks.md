# 上证 e 投票全流程 Skill 数据飞轮 — 实施任务

> 基线：`requirements.md`、`design.md`。任务按依赖顺序执行；每项完成时必须同时提交测试，不以“接口可调用”代替验收。

## 执行边界

- 本次在现有 `knowledge_evolution`、`skills`、`requirements`、`testcases` 和前端质量进化工作区内增量开发，不新建平行飞轮系统。
- P0/P1 先完成在线闭环与项目隔离；历史回放随后交付；高级隐藏集与跨项目导入向导不阻塞首版上线。
- 自动化只能创建候选和建议。任何资产进入金标、回归或专用测试集前，都必须经过人工审核。
- 上证 e 投票首版分类与关键场景清单由测试负责人维护并审批，系统不得自动发布分类版本。

## T01：建立项目级 Skill/版本选择与执行锁（P0）

**状态**：已完成（后端回归 43 项通过，前端生产构建通过）。

**需求映射**：P6、R13。

**代码落点**：

- `WHartTest_Django/knowledge_evolution/task_binding.py`
- `WHartTest_Django/skills/runtime.py`
- `WHartTest_Django/knowledge_evolution/workflow_models.py`
- `WHartTest_Django/knowledge_evolution/serializers.py`
- `WHartTest_Django/knowledge_evolution/tests_t15.py`

**实施内容**：

1. 保持 Skill Hub 公开可见，并允许项目在启动流程前为各阶段选择具体 `SkillVersion`。
2. 扩展绑定服务和启动协议：显式版本优先，校验版本属于所选 Skill、处于可运行状态且阶段匹配。
3. 将选择固化到当前项目的 `WorkflowSkillLock`；流程重试、恢复和后续阶段不得漂移版本。
4. 同一项目后续流程可重新选择版本，但不得改写已启动流程的锁；不同项目的选择互不影响。
5. 增加数据审计命令，只报告锁、产出和实际 Skill 版本不一致，不自动修改历史数据。

**验收**：A/B 都能看到公开 Skill Hub；A 选择 v1、B 选择 v2 后各自流程始终使用所选版本；任何一方改选或商店激活新版本都不影响另一方及已启动流程。

## T02：建立统一 `FlywheelRun` 与上下文服务（P0）

**状态**：已完成（迁移检查通过，上下文与 API 测试 6 项通过）。

**需求映射**：P3、R5、R6、R13。

**代码落点**：

- `WHartTest_Django/knowledge_evolution/models.py`
- `WHartTest_Django/knowledge_evolution/workflow_models.py`
- `WHartTest_Django/knowledge_evolution/services.py`
- `WHartTest_Django/knowledge_evolution/serializers.py`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Django/knowledge_evolution/urls.py`
- 新增 migration 与上下文测试文件

**实施内容**：

1. 新增 `FlywheelRun`，实现 `(project, workflow_id)` 唯一约束和入口/意图/状态字段。
2. 实现 `FlywheelContextService`：项目成员、阶段、上游产出、Skill 锁及需求文档校验。
3. 新接口：创建/查询运行、解析阶段上下文。
4. 兼容原字符串 `workflow_id`，首期双写，不强制一次性迁移所有存量数据。
5. 为 `GenerationOutput`、`WorkflowStageGate`、`WorkflowSkillLock`、`TestExecution` 增加可回填关联或一致性校验。

**验收**：需求管理、Agent 对话、测试管理和飞轮入口可以解析到同一运行上下文；跨项目父产出、版本锁或执行引用全部失败。

## T03：扩展金标数据模型并引入分类治理（P0）

**状态**：已完成（迁移检查通过，新增治理测试 3 项、既有金标回归 64 项通过）。

**需求映射**：P4、P5、P6、R10、R11、R12、R13。

**代码落点**：

- `WHartTest_Django/knowledge_evolution/gold_models.py`
- `WHartTest_Django/knowledge_evolution/serializers.py`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Django/knowledge_evolution/urls.py`
- 新增 migration 与模型/API 测试

**实施内容**：

1. `GoldDataset` 增加业务范围、治理策略、分类版本和审批人。
2. `GoldCase` 增加候选来源、推荐分区/标签、去重指纹和预检清单；建议字段与人工最终字段分离。
3. 新增 `TestAssetTaxonomy`，支持草稿、送审、发布、退役和不可变版本。
4. 将“专用集”建模为数据集业务范围，将“回归集”沿用 `GoldCase.split=regression`，不复制案例。
5. 数据迁移为存量数据补通用范围和默认治理策略，不改变既有确认/冻结状态。

**验收**：测试负责人可发布上证 e 投票分类版本；发布后不可原地修改；同一案例能同时属于上证 e 投票专用数据集和回归分区。

## T04：实现自动候选事件、预检与补偿（P0）

**状态**：已完成（候选事件、幂等/重试/死信、项目隔离及 API 回归纳入 T01–T06 共 124 项测试并通过）。

**需求映射**：P2、P5、R3、R4、R5、R10。

**代码落点**：

- `WHartTest_Django/knowledge_evolution/models.py`
- `WHartTest_Django/knowledge_evolution/services.py`
- `WHartTest_Django/knowledge_evolution/gold.py`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Django/task_center/`（复用异步任务能力）
- 新增 migration、候选服务和失败补偿测试

**实施内容**：

1. 新增 `AssetCandidateEvent` 幂等事件与重试/死信状态。
2. 在正式阶段产出、确认稿、缺陷、漏测、误报、失败、回滚和编辑反馈后创建事件。
3. 实现当前项目内的完整性、隐私、精确去重、近似冲突和推荐归属预检。
4. 事件处理只创建/更新 `GoldCase(state=candidate)`，不产生自动确认。
5. 提供失败重试接口和任务中心可见的死信告警。

**验收**：重复事件不重复建候选；处理失败可重试；跨项目不参与去重；高置信候选仍停留在待人工审核状态。

## T05：收紧人工审核、仲裁与冻结门禁（P0）

**状态**：已完成（双轮人工审核、冲突仲裁、关键场景覆盖和冻结不可变门禁回归通过）。

**需求映射**：P5、R4、R10、R11、R12。

**代码落点**：

- `WHartTest_Django/knowledge_evolution/gold.py`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Django/knowledge_evolution/serializers.py`
- 现有金标测试与新增治理测试

**实施内容**：

1. 沿用初标、复核、冲突仲裁，并将复核/仲裁/冻结权限限定为测试负责人。
2. 审核动作写入最终标签、分区、分类、理由和证据快照。
3. 冻结前检查未确认数、未解决冲突、分类版本状态和关键场景覆盖缺口。
4. 数据集版本冻结后保持内容与治理策略不可变。
5. 审计记录保存操作者、前后状态、理由和项目。

**验收**：没有人工审核的资产无法进入冻结版本；存在冲突或关键场景缺口时无法冻结；非测试负责人不能复核、仲裁或冻结。

## T06：打通四类入口和四阶段产出联动（P1）

**状态**：已完成（需求文档、Agent Loop、用例审查和飞轮入口统一到项目级 `FlywheelRun`，入口联动回归通过；前端生产构建通过）。

**需求映射**：P1、P2、P3、R1、R2、R3、R5、R6、R7、R8。

**代码落点**：

- `WHartTest_Django/requirements/`
- `WHartTest_Django/langgraph_integration/`
- `WHartTest_Django/testcases/`
- `WHartTest_Django/knowledge_evolution/protocol.py`
- `WHartTest_Django/knowledge_evolution/task_binding.py`
- 对应前端入口与集成测试

**实施内容**：

1. 从需求文档、Agent 对话、测试管理和飞轮页面发起时统一创建/选择 `FlywheelRun`。
2. 四阶段全部传递 `project/workflow/stage/parent_output_ids/skill_version`。
3. 确认稿上传、人工修改和测试执行结果自动回写反馈与候选事件。
4. 页面不再要求用户手工复制上下文 ID；由当前项目和流程选择自动派生。
5. 兼容现有入口；未开启新闭环的旧任务仍可读取。

**验收**：需求拆解→方案→用例→执行/报告形成单一可追溯链；任一入口产出都能进入同一项目候选审核队列。

## T07：交付资产审核与数据集版本工作区（P1）

**状态**：已完成（候选审核、初标/复核、冲突仲裁、分类维护、版本冻结、项目切换清理和前端生产构建已验收）。

**需求映射**：P2、P4、P5、P6、R10、R11、R12、R13。

**代码落点**：

- `WHartTest_Vue/src/features/knowledge-evolution/AIQualityEvolutionView.vue`
- `WHartTest_Vue/src/features/knowledge-evolution/components/`
- `WHartTest_Vue/src/features/knowledge-evolution/service.ts`
- `WHartTest_Vue/src/features/knowledge-evolution/types.ts`
- 前端单测/构建与端到端验收

**实施内容**：

1. 新增“资产审核”工作区：候选筛选、证据/差异抽屉、批准、修改后批准、驳回和转仲裁。
2. 新增“数据集版本”工作区：主集、回归分区、专用集、覆盖缺口、冻结和退役。
3. 自动建议与人工结论使用明确不同的状态和文案。
4. 顶部持续显示项目；项目切换清空选中项、查询缓存和统计。
5. 长任务统一进入任务中心，不依赖一次性 toast。

**验收**：测试负责人无需离开质量进化工作区即可完成候选审核、冲突处理、分类维护和版本冻结；项目切换不残留旧项目数据。

## T08：历史资料预检、导入与人工确认（P1）

**状态**：已完成（预检无写入、项目文件权限/哈希复核、确认导入幂等、只生成待人工审核候选）。

**需求映射**：P1、P5、R1、R2、R9、R10、R13。

**代码落点**：

- `WHartTest_Django/knowledge_evolution/history_ingestion.py`（新增）
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Django/knowledge_evolution/serializers.py`
- `WHartTest_Django/file_management/`
- 前端历史回放工作区
- 导入与安全测试

**实施内容**：

1. 定义历史包清单：需求、真实方案、真实用例及可选执行/报告。
2. 先预检文件映射、哈希、缺失、冲突、隐私和预计候选数，再由人工确认导入。
3. 导入结果只创建历史来源候选和基准引用，不直接创建冻结金标。
4. 文件读取、下载和详情均通过项目权限校验。

**验收**：预检不会写业务数据；确认导入后候选仍需人工审核；跨项目或无权限文件不可引用且不泄漏正文。

## T09：历史回放、结构化比对与评测门禁（P1）

**状态**：已完成（隔离回放、配置指纹、五类结构化差异、不确定项人工判定和关键退化硬阻断）。

**需求映射**：P1、P4、R1、R2、R6、R7、R8、R9、R11、R12。

**代码落点**：

- `WHartTest_Django/knowledge_evolution/evaluation.py`
- `WHartTest_Django/knowledge_evolution/services.py`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Vue/src/features/knowledge-evolution/`
- 回放/评测集成测试

**实施内容**：

1. 使用 `intent=history_replay` 锁定四阶段 Skill、模型和 Prompt。
2. 结构化比较生成 `missing/extra/conflict/equivalent/uncertain`，不确定项必须人工判定。
3. 保存阶段分、端到端分、格式契约、成本和差异下钻证据。
4. 回归分区全量执行；关键案例任一退化即阻断候选 Skill 晋级。
5. 回放产出与生产产出隔离，不能覆盖生产链路。

**验收**：相同冻结集和模型配置下可复现实验；报告可定位到案例、阶段和 Skill 版本；未完成人工判定的结果不能用于晋级。

## T10：全链路回归、迁移审计和上线开关（P1）

**状态**：已完成（项目级开关、A/B 隔离、迁移 dry-run、审计命令、上线/补偿/回滚手册及 128 项相关回归）。

**需求映射**：全部。

**代码落点**：

- Django 单元/集成测试
- Vue 构建与关键交互测试
- 数据审计命令
- 部署配置和运维说明

**实施内容**：

1. 建立项目 A/B 隔离矩阵测试，覆盖 Skill 选择/版本锁、知识库、产出、反馈、金标、评测、文件和发布指针。
2. 建立在线闭环与历史回放端到端测试。
3. 执行 migration dry-run、存量一致性审计和可回滚验证。
4. 增加项目级功能开关，先灰度上证 e 投票项目，再逐步开放。
5. 输出上线检查表、异常补偿和回滚手册。

**验收**：后端测试、前端构建及关键端到端用例通过；审计无未解释的跨项目引用；关闭开关后原有流程不受影响。

## 后续增强（不阻塞首版）

- 隐藏集期望仅在评测 Worker 内可见。
- 语义近似冲突聚类与主动学习推荐。
- 质量、成本、延迟联合晋级门禁。
- 跨项目显式导入/复制向导及审批审计。

## 推荐执行批次

1. **批次 A（隔离底座）**：T01 → T02 → T03。
2. **批次 B（在线闭环）**：T04 → T05 → T06 → T07。
3. **批次 C（历史回放）**：T08 → T09。
4. **批次 D（上线验证）**：T10。

每个批次完成后做一次可运行验收；不等待全部任务完成才第一次联调。
