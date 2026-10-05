# 质量飞轮—Agent 执行—Skill 自进化联动实施任务

> 状态：待实施  
> 需求基线：[`requirements.md`](requirements.md)  
> 设计基线：[`design.md`](design.md)  
> 实施边界：复用现有 `knowledge_evolution`、Agent Loop、Skill Hub、文件管理、金标和评测能力，不创建平行飞轮或第二套 Agent。

## 执行约束

1. 每项任务必须同时提交自动化测试，不以“接口可调用”代替验收。
2. 业务任务成功与飞轮登记成功必须分离；飞轮失败不得吞掉业务产物。
3. 上传反馈、确认归因、派生候选、评测和发布必须是独立动作。
4. 任何候选都不得直接修改 active Skill 包。
5. 不采集、展示或依赖模型隐藏思维链；只记录显式结构化决策与可观察执行事件。
6. 一期仅将“上证 e 投票测试方案生成”升级为 L3，其他 Skill 保持兼容。
7. 所有新增数据必须按 `project_id` 隔离，并保留操作者、时间、版本和哈希。

## 依赖关系

```text
T01 → T02 → T03 → T04
              ├──→ T05 → T06 → T07
              └──→ T08
T06 + T08 → T09 → T10 → T11
T04 → T12
T07 + T09 + T10 + T11 + T12 → T13
```

---

## T01：建立阶段执行尝试模型（P0）

**状态**：已完成（2026-10-05，`tests_t01.py` 35 项通过）  
**依赖**：无  
**设计映射**：§4.4、§13

**代码落点**（实际）：

- `WHartTest_Django/knowledge_evolution/workflow_models.py`（模型 + 状态机真值）
- `WHartTest_Django/knowledge_evolution/migrations/0037_stageexecutionattempt.py`
- `WHartTest_Django/knowledge_evolution/operations.py`（`StageExecutionAttemptService`）
- `WHartTest_Django/knowledge_evolution/serializers.py`、`views.py`、`urls.py`
- `WHartTest_Django/knowledge_evolution/tests_t01.py`

**实施内容**：

- [x] 新增 `StageExecutionAttempt`。
- [x] 实现 `planned/dispatched/running/output_published/completed/failed/cancelled/timed_out` 状态。
- [x] 保存流程、阶段、实际 SkillVersion、上游产出、会话、入口、输出、失败摘要和时间。
- [x] 增加 `retry_of` 和 `idempotency_key`，防止重复点击生成两个任务。
- [x] 校验 attempt、Skill 锁、上游产出和流程属于同一项目。
- [x] 为状态迁移实现服务层方法，禁止非法回退和跨终态更新。

**验收**：

- Agent 尚未产生正式输出时也能查询执行状态。→ `GET /stage-attempts/{id}/`，`output` 为空仍可读。
- 同一幂等键重复派发只返回同一个 attempt。→ 唯一约束 `uniq_stage_attempt_idempotency`（`idempotency_key` 非空时生效）。
- 失败 attempt 保留，重试创建新 attempt 并关联原记录。→ `retry()` 新建 + `retry_of`。
- 跨项目 Skill、流程或父产出全部被拒绝。→ `_assert_same_project`（不存在与跨项目合并为同一结论，避免探测）。

---

## T02：建立可信执行上下文与阶段派发接口（P0）

**状态**：待实施  
**依赖**：T01  
**设计映射**：§4.1～§4.3、§11

**代码落点**：

- `WHartTest_Django/knowledge_evolution/operations.py`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Django/knowledge_evolution/urls.py`
- `WHartTest_Django/knowledge_evolution/task_binding.py`
- 阶段派发与安全测试

**实施内容**：

- [ ] 扩展 `execute-workflow-stage`：校验门禁、解析 Skill 锁和父产出后创建 attempt。
- [ ] 新增短期有效的 `execution_context_id`，服务端保存可信参数。
- [ ] 新增执行上下文解析接口，只返回当前用户和项目可访问的数据。
- [ ] 返回 `attempt_id`、`execution_context_id` 和业务页面 `launch_url`。
- [ ] 上下文绑定项目、用户、流程、阶段、SkillVersion 和过期时间。
- [ ] 上下文重复解析保持幂等，项目切换、过期或版本不一致时拒绝执行。

**验收**：

- 前端无需复制 `workflow_id`、`module_key` 或 SkillVersion。
- 篡改 URL 不能替换流程锁定的 SkillVersion。
- 上一阶段未放行时无法派发下一阶段。
- 派发成功后飞轮能看到“已派发、等待执行”。

---

## T03：完成飞轮到方案分析的自动跳转（P0）

**状态**：待实施  
**依赖**：T02  
**设计映射**：§4.2、§4.3、§12

**代码落点**：

- `WHartTest_Vue/src/features/knowledge-evolution/AIQualityEvolutionView.vue`
- `WHartTest_Vue/src/features/knowledge-evolution/service.ts`
- `WHartTest_Vue/src/features/knowledge-evolution/types.ts`
- `WHartTest_Vue/src/views/TestPlanGenerationView.vue`
- 路由与前端交互测试

**实施内容**：

- [ ] 将方案阶段按钮改为“跳转到Agent执行”。
- [ ] 派发成功后自动跳转后端返回的 `launch_url`。
- [ ] 方案分析页解析 `execution_context_id`。
- [ ] 受控模式显示流程横幅、阶段、锁定 Skill 和上游产出。
- [ ] 锁定 Skill 选择器，禁止切换到其他版本。
- [ ] 生成请求携带 `attempt_id`，上下文失效时阻止执行并给出恢复入口。
- [ ] 保留原有直接进入方案分析的旁路模式。

**验收**：

- 从飞轮点击后一次跳转即可进入可执行页面。
- 用户看不到需要手工复制的流程 ID。
- 受控模式无法更换 Skill；旁路模式仍可正常选择 Skill。
- 刷新页面后能恢复 attempt 和受控上下文。

---

## T04：打通 Agent 运行状态与正式产出事件（P0）

**状态**：待实施  
**依赖**：T03  
**设计映射**：§4.5、§5、§13

**代码落点**：

- `WHartTest_Django/orchestrator_integration/agent_loop_view.py`
- `WHartTest_Django/knowledge_evolution/protocol.py`
- `WHartTest_Django/knowledge_evolution/services.py`
- `WHartTest_Vue/src/features/langgraph/services/chatService.ts`
- `WHartTest_Vue/src/views/TestPlanGenerationView.vue`
- Agent Loop 与 SSE 集成测试

**实施内容**：

- [ ] Agent 开始执行时将 attempt 更新为 `running` 并绑定 `session_id`。
- [ ] 在检索、工具、文件和校验事件发生时更新可观察状态。
- [ ] 正式发布 `GenerationOutput` 后发送 `output_published` SSE。
- [ ] SSE 包含 `attempt_id/output_id/workflow_id/stage`。
- [ ] 业务页面以正式事件判断完成，不再仅根据文件名或聊天文本推断。
- [ ] 正式产出与 attempt 绑定，随后更新 `completed`。
- [ ] 中途失败时写失败状态和受限错误摘要，保留已有执行 Span。

**验收**：

- 飞轮和方案页面能区分“已派发、运行中、正式产出、失败”。
- Agent 中途失败时，即使没有 `GenerationOutput` 也能看到失败 attempt。
- 重连或刷新后状态不依赖浏览器内存。
- 敏感工具参数和凭据不进入执行事件。

---

## T05：定义 `stage-result/v1` 与 Skill 兼容等级（P0）

**状态**：待实施  
**依赖**：T04  
**设计映射**：§6、§10

**代码落点**：

- `WHartTest_Django/knowledge_evolution/schemas/`（新增）
- `WHartTest_Django/knowledge_evolution/stage_outputs.py`（新增）
- `WHartTest_Django/knowledge_evolution/protocol.py`
- `WHartTest_Django/skills/metadata_generation.py`
- Skill Hub 前端元数据展示
- schema 与兼容性测试

**实施内容**：

- [ ] 定义 `stage-result/v1` JSON Schema。
- [ ] 必填 `stage/status/primary_artifacts/items/evidence` 及稳定业务项 ID。
- [ ] 定义结构化决策、反证和不确定项为推荐字段。
- [ ] 实现运行时 schema 校验与错误报告。
- [ ] 定义 L0 普通文件、L1 可识别、L2 可评审、L3 可进化等级。
- [ ] Skill Hub 展示声明等级和最近一次运行校验结果。
- [ ] 协议失败不删除业务文件，只标记“结构化协议失败”。

**验收**：

- 旧 Skill 没有 `stage_result.json` 时仍可按 L0/L1 运行。
- 合法 L2/L3 输出能被平台统一解析。
- 缺稳定 ID、主产物声明或非法引用时返回具体字段错误。
- 结构化失败与业务生成失败在页面上明确区分。

---

## T06：将 e投票方案生成 Skill 升级为 L3（P0）

**状态**：待实施  
**依赖**：T05  
**设计映射**：§5.3、§5.4、§6

**代码落点**：

- `staged-skills/sse-evote-test-plan/SKILL.md`
- `staged-skills/sse-evote-test-plan/schemas/output.json`
- `staged-skills/sse-evote-test-plan/references/TestPlan.md`
- 必要的模板与校验脚本
- Skill 包静态校验与运行样例

**实施内容**：

- [ ] 保留现有正式 `test_plan.xlsx` 格式。
- [ ] 新增 `stage_result.json`，为每个末级方案项生成稳定 ID。
- [ ] 每项关联需求 ID、证据 ID、场景类型、优先级和可判定预期。
- [ ] 证据记录文档、版本、Chunk、原句、offset 和哈希。
- [ ] 输出显式结构化决策摘要、假设、不确定项和反证检查。
- [ ] 声明主产物，避免前端依赖文件名正则识别。
- [ ] 增加 Skill 自检：引用定位、ID 唯一、需求关联和输出 schema。

**验收**：

- 同一次生成同时得到业务方案与合法 `stage_result.json`。
- 每个末级方案项有唯一稳定 ID。
- 知识引用可回到具体文档版本和原句。
- Skill 不填写任何人工结论字段。

---

## T07：实现方案阶段适配器与平台生成物（P0）

**状态**：待实施  
**依赖**：T06  
**设计映射**：§5、§6.3、§7

**代码落点**：

- `WHartTest_Django/knowledge_evolution/stage_outputs.py`
- `WHartTest_Django/knowledge_evolution/evidence_graph.py`（新增）
- `WHartTest_Django/knowledge_evolution/review_reports.py`（新增）
- `WHartTest_Django/knowledge_evolution/quality_summary.py`（新增）
- 文件下载接口与生成器测试

**实施内容**：

- [ ] 定义通用 `StageOutputAdapter` 接口。
- [ ] 实现 `TestPlanOutputAdapter`。
- [ ] 从 `stage_result.json` 和运行轨迹生成 `execution_evidence_graph.json`。
- [ ] 对证据原句执行文档版本、Chunk、offset 和哈希校验。
- [ ] 生成 `stage_quality_summary.json`。
- [ ] 生成方案确认报告，人工列初始为空。
- [ ] 将生成物登记为平台派生产物，保留生成器版本和源产出哈希。

**验收**：

- 同一输入重复生成得到内容一致的确认报告和图谱。
- 引用只在确定性验证通过后标记 `verified`。
- 无法定位的引用标记 `invalid`，不冒充可信证据。
- 平台生成物不覆盖 Skill 的业务主产物。

---

## T08：实现可实时查询的执行链路（P1）

**状态**：待实施  
**依赖**：T04  
**设计映射**：§5、§12

**代码落点**：

- `WHartTest_Django/knowledge_evolution/trace_models.py`
- `WHartTest_Django/knowledge_evolution/attribution.py`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Vue/src/features/knowledge-evolution/`
- 轨迹权限与前端展示测试

**实施内容**：

- [ ] 允许执行过程中增量写入或查询 Span，不等待最终产出。
- [ ] 提供 attempt 维度的事件和 Span 查询接口。
- [ ] 飞轮阶段卡展示业务摘要：输入、检索、工具、文件、校验和失败。
- [ ] 提供详细链路下钻，展示工具名、状态、耗时、哈希和错误摘要。
- [ ] 不显示隐藏思维链、凭据和完整敏感参数。
- [ ] 对知识原句按原文权限做二次鉴权。

**验收**：

- Agent 运行期间飞轮能够看到进度变化。
- 无正式产出的失败任务仍有可诊断轨迹。
- 无知识文档权限的用户看不到原句正文。
- 执行链路刷新后仍可恢复。

---

## T09：实现最简人工确认报告闭环（P0）

**状态**：待实施  
**依赖**：T07  
**设计映射**：§7

**代码落点**：

- `WHartTest_Django/knowledge_evolution/review_reports.py`
- `WHartTest_Django/knowledge_evolution/workflow_feedback.py`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Vue/src/features/knowledge-evolution/`
- Excel 解析与反馈测试

**实施内容**：

- [ ] 确认报告只保留四个人工列：人工结论、修改类型、修改内容、备注。
- [ ] 人工结论仅允许：采纳、修改后采纳、删除；初始为空。
- [ ] 修改类型仅允许：内容错误、覆盖不足、粒度不当、证据错误、优先级不当、重复或无效、其他。
- [ ] 支持保存草稿与正式提交两个动作。
- [ ] 正式提交时检查空白和条件必填关系。
- [ ] 服务端扫描明细重算统计，不完全依赖公式缓存。
- [ ] 最后一个 Sheet 保留“采纳率”标签，并增加有效保留率、修改率、删除率、确认完成率和证据准确率。
- [ ] 形成版本化反馈，绑定原始 output、trace 和 SkillVersion。

**验收**：

- 空白结论可保存草稿，但不能进入进化。
- 修改后采纳缺修改类型或内容时拒绝正式提交。
- 删除缺修改类型时拒绝正式提交。
- 同一文件哈希重复提交保持幂等。
- 低采纳率可以记录，不作为上传门槛。

---

## T10：实现阶段人工补充文件上传（P0）

**状态**：待实施  
**依赖**：T07  
**设计映射**：§7.4、§13

**代码落点**：

- `WHartTest_Django/knowledge_evolution/models.py` 或新增反馈资产模型
- `WHartTest_Django/file_management/`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Vue/src/features/knowledge-evolution/`
- 文件权限、哈希和项目隔离测试

**实施内容**：

- [ ] 支持“确认后完整产物、人工补充产物、参考附件、问题证据”四种用途。
- [ ] 文件绑定项目、流程、阶段、原始 output、用途、哈希和上传人。
- [ ] 允许在方案、用例、执行和报告阶段分别上传。
- [ ] 提供预览、下载、删除/退役和审计记录。
- [ ] 上传后只进入反馈与候选解析，不自动成为金标或修改 Skill。
- [ ] 重复哈希保持幂等；跨项目文件引用直接拒绝。

**验收**：

- 人工可以在用例阶段另行上传补充用例文件。
- 文件能追溯到对应阶段和原始产出。
- 上传动作不会隐式派生或激活 Skill。
- 无权限用户不能读取文件名、正文或下载地址。

---

## T11：实现差异、证据反查与人工确认归因（P1）

**状态**：待实施  
**依赖**：T09、T10  
**设计映射**：§8.1～§8.3

**代码落点**：

- `WHartTest_Django/knowledge_evolution/attribution.py`
- `WHartTest_Django/knowledge_evolution/evidence_graph.py`
- 新增方案差异服务
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Vue/src/features/knowledge-evolution/`
- 差异与归因测试

**实施内容**：

- [ ] 解析采纳、修改后采纳、删除和人工补充内容。
- [ ] 生成字段级结构化差异和证据状态变化。
- [ ] 沿 `HumanEdit → 业务项 → DecisionRecord → EvidenceQuote → AgentStep → SkillRule` 反查。
- [ ] 将问题归入现有八层责任模型。
- [ ] 自动归因只创建 `proposed`，并保存反证和置信度。
- [ ] 提供人工确认、改写和驳回归因的工作区。
- [ ] 环境问题不得误生成 Skill 内容补丁。

**验收**：

- 每个归因能回到具体人工修改和原始业务项。
- 有需求和知识但已召回未使用时，能与“知识缺失”区分。
- 未经人工确认的归因不能进入候选派生。
- 归因被驳回后不会继续用于优化。

---

## T12：实现旁路产出纳管与替换语义（P1）

**状态**：待实施  
**依赖**：T04  
**设计映射**：§9

**代码落点**：

- `WHartTest_Django/knowledge_evolution/workflow_models.py`
- `WHartTest_Django/knowledge_evolution/operations.py`
- `WHartTest_Django/knowledge_evolution/lineage.py`
- `WHartTest_Vue/src/views/TestPlanGenerationView.vue`
- `WHartTest_Vue/src/features/knowledge-evolution/`
- 纳管、替换和冲突测试

**实施内容**：

- [ ] 新增 `WorkflowStageSubmission` 或等价绑定模型。
- [ ] 旁路生成完成后提供“纳入质量飞轮”。
- [ ] 支持纳入新流程或符合条件的已有流程。
- [ ] 校验项目、阶段、SkillVersion、父产出和目标阶段冲突。
- [ ] 替换已有产出时要求显式确认并写 `supersedes_output_id`。
- [ ] 保留旧产出、旧门禁和旧反馈；新产出重新进入待评测。
- [ ] 首期拒绝同阶段并行分支。

**验收**：

- 直接从方案分析生成的产出可以显式纳管。
- SkillVersion 与流程锁不一致时拒绝纳管。
- 纳管不篡改原 `GenerationOutput` 历史协议。
- 替换后旧版本仍可追溯，新版本必须重新评测。

---

## T13：实现 `skill_content` 候选、评测与上线闭环（P1）

**状态**：待实施  
**依赖**：T11  
**设计映射**：§8.3、§8.4

**代码落点**：

- `WHartTest_Django/knowledge_evolution/skill_evolution.py`
- `WHartTest_Django/knowledge_evolution/optimization.py`
- `WHartTest_Django/knowledge_evolution/evaluation.py`
- `WHartTest_Django/skills/`
- Skill 进化工坊前端
- 候选、评测、审批和回滚测试

**实施内容**：

- [ ] 新增 `skill_content` 优化类型。
- [ ] 允许修改白名单：`SKILL.md`、相关 references、schemas、模板和确定性校验脚本。
- [ ] 候选只能来自已确认归因和允许用于优化的数据。
- [ ] 生成最小增量补丁、风险说明、预期收益和回滚目标。
- [ ] 创建新的不可变 `SkillVersion`，不修改 active 包。
- [ ] 在相同冻结数据集、模型和配置上执行基线/候选对照。
- [ ] 强制本轮 Badcase 修复、关键回归零退化、schema 通过和隐藏集隔离。
- [ ] 支持审批、灰度、激活、观察和回滚。

**验收**：

- 工坊能从具体人工修改追溯到候选补丁来源。
- 未确认归因、禁止优化样本或隐藏期望泄漏时拒绝派生/晋级。
- 候选改善平均分但关键案例退化时阻止激活。
- 激活新版本不影响已锁定旧版本的运行中流程。

---

## T14：完成全链路验收、补偿和灰度上线（P1）

**状态**：待实施  
**依赖**：T07、T08、T09、T10、T11、T12、T13  
**设计映射**：§13～§15

**代码落点**：

- Django 单元、集成与迁移测试
- Vue 构建与关键交互测试
- 端到端测试
- 异步补偿、任务中心与审计命令
- 上线和回滚文档

**实施内容**：

- [ ] 跑通“飞轮发起 → 方案分析 → 正式产出 → 确认 → 归因 → 候选 → 评测 → 激活”。
- [ ] 建立实时轨迹、引用权限、文件权限和项目隔离测试。
- [ ] 建立 Agent 中途失败、SSE 断线、产出发布失败和飞轮登记失败测试。
- [ ] 飞轮写入失败进入幂等重试和死信告警，不只记录日志。
- [ ] 执行 migration dry-run、存量兼容和回滚验证。
- [ ] 增加项目级开关，先灰度上证 e 投票方案阶段。
- [ ] 输出上线检查表、异常补偿手册和回滚手册。

**验收**：

- 设计文档 §15 的六类验收场景全部通过。
- 关闭开关后原有方案分析和 Agent 调用不受影响。
- 业务生成成功但飞轮登记失败时，业务文件仍可访问且补偿任务可见。
- 审计无未解释的跨项目引用、版本漂移和无主反馈。

## 推荐执行批次

1. **批次 A：受控执行底座**：T01 → T02 → T03 → T04。
2. **批次 B：L3 方案产出**：T05 → T06 → T07，同时实施 T08。
3. **批次 C：人工反馈闭环**：T09 → T10 → T11。
4. **批次 D：兼容与进化**：T12 → T13。
5. **批次 E：上线验证**：T14。

每批完成后必须进行可运行验收，不等待所有任务完成才第一次联调。
