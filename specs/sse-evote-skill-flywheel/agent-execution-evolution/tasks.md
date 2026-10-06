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

**状态**：已完成（2026-10-05，`tests_t02.py` 16 项通过；新增 `StageExecutionContext` 模型 + `0038` 迁移）  
**依赖**：T01  
**设计映射**：§4.1～§4.3、§11

**代码落点**：

- `WHartTest_Django/knowledge_evolution/operations.py`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Django/knowledge_evolution/urls.py`
- `WHartTest_Django/knowledge_evolution/task_binding.py`
- 阶段派发与安全测试

**实施内容**：

- [x] 扩展 `execute-workflow-stage`：校验门禁、解析 Skill 锁和父产出后创建 attempt。
- [x] 新增短期有效的 `execution_context_id`，服务端保存可信参数。
- [x] 新增执行上下文解析接口，只返回当前用户和项目可访问的数据。
- [x] 返回 `attempt_id`、`execution_context_id` 和业务页面 `launch_url`。
- [x] 上下文绑定项目、用户、流程、阶段、SkillVersion 和过期时间。
- [x] 上下文重复解析保持幂等，项目切换、过期或版本不一致时拒绝执行。

**实现说明**：

- 幂等不靠幂等键，靠「复用活跃 attempt」——前端每次算出的键在页面刷新后就会变，等于没有幂等。
- 上下文解析不消费，只累加 `resolve_count` 做审计——页面挂载/刷新/重试都会解析，一次性消费会让第二次刷新直接失败。
- 跨项目与不存在的上下文返回**同一结论**，避免用错误码差异探测其他项目的数据。

**验收**：

- 前端无需复制 `workflow_id`、`module_key` 或 SkillVersion。
- 篡改 URL 不能替换流程锁定的 SkillVersion。
- 上一阶段未放行时无法派发下一阶段。
- 派发成功后飞轮能看到“已派发、等待执行”。

---

## T03：完成飞轮到方案分析的自动跳转（P0）

**状态**：已完成（2026-10-05，见 T04 批次 A 回归 171 项通过）  
**依赖**：T02  
**设计映射**：§4.2、§4.3、§12

**代码落点**：

- `WHartTest_Vue/src/features/knowledge-evolution/AIQualityEvolutionView.vue`
- `WHartTest_Vue/src/features/knowledge-evolution/service.ts`
- `WHartTest_Vue/src/features/knowledge-evolution/types.ts`
- `WHartTest_Vue/src/views/TestPlanGenerationView.vue`
- 路由与前端交互测试

**实施内容**：

- [x] 将方案阶段按钮改为“跳转到Agent执行”。
- [x] 派发成功后自动跳转后端返回的 `launch_url`。
- [x] 方案分析页解析 `execution_context_id`。
- [x] 受控模式显示流程横幅、阶段、锁定 Skill 和上游产出。
- [x] 锁定 Skill 选择器，禁止切换到其他版本。
- [x] 生成请求携带 `attempt_id`，上下文失效时阻止执行并给出恢复入口。
- [x] 保留原有直接进入方案分析的旁路模式。

**实现说明**：

- 受控模式锁定版本的落库**必须排在 `loadSkills()` 之后**，否则"默认版本"会盖掉
  流程锁定的版本 —— 页面显示 A、流程记录 B，是最难查的一类不一致。
- 上下文解析失败**不降级成旁路**：静默降级会产出"看起来在流程里、实际不在"的
  产出。只给恢复入口（回飞轮重新派发）。
- 按钮口径统一为「跳转到Agent执行」（`design.md` §4.2 已同步）。

**验收**：

- 从飞轮点击后一次跳转即可进入可执行页面。
- 用户看不到需要手工复制的流程 ID。
- 受控模式无法更换 Skill；旁路模式仍可正常选择 Skill。
- 刷新页面后能恢复 attempt 和受控上下文。

---

## T04：打通 Agent 运行状态与正式产出事件（P0）

**状态**：已完成（2026-10-05，`tests_t04.py` 11 项通过；批次 A 回归 171 项通过）  
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

- [x] Agent 开始执行时将 attempt 更新为 `running` 并绑定 `session_id`。
- [x] 在检索、工具、文件和校验事件发生时更新可观察状态。
- [x] 正式发布 `GenerationOutput` 后发送 `output_published` SSE。
- [x] SSE 包含 `attempt_id/output_id/workflow_id/stage`。
- [x] 业务页面以正式事件判断完成，不再仅根据文件名或聊天文本推断。
- [x] 正式产出与 attempt 绑定，随后更新 `completed`。
- [x] 中途失败时写失败状态和受限错误摘要，保留已有执行 Span。

**实现说明**：

- `output_published` **独立成一支**，不并入 `complete`：两者不总同时发生
  （旁路运行没有 attempt，受控运行也可能只发布产出不结束会话）。
- 观测面出问题**不拖垮执行面**：`mark_running` / 完成回写 / 失败记录全部
  只 `logger.exception`，不改返回值。
- 失败摘要**受限**：只取异常首行前 300 字符 + 异常类名，避免把凭据或绝对路径
  写进会被页面读出的 attempt。

**验收**：

- 飞轮和方案页面能区分“已派发、运行中、正式产出、失败”。
- Agent 中途失败时，即使没有 `GenerationOutput` 也能看到失败 attempt。
- 重连或刷新后状态不依赖浏览器内存。
- 敏感工具参数和凭据不进入执行事件。

---

## T05：定义 `stage-result/v1` 与 Skill 兼容等级（P0）

**状态**：已完成（2026-10-05，`tests_t05.py` 27 项 + 相关回归 129 项通过；新增 `stage_outputs.py` 与 `schemas/`）  
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

- [x] 定义 `stage-result/v1` JSON Schema。
- [x] 必填 `stage/status/primary_artifacts/items/evidence` 及稳定业务项 ID。
- [x] 定义结构化决策、反证和不确定项为推荐字段。
- [x] 实现运行时 schema 校验与错误报告。
- [x] 定义 L0 普通文件、L1 可识别、L2 可评审、L3 可进化等级。
- [x] Skill Hub 展示声明等级和最近一次运行校验结果。
- [x] 协议失败不删除业务文件，只标记“结构化协议失败”。

**实现说明**：

- 等级真值在 `knowledge_evolution/stage_outputs.py` 模块级（`COMPATIBILITY_LEVELS` /
  `LEVEL_RANK` / `COMPATIBILITY_LABELS` / `COMPATIBILITY_CAPABILITIES`）。
- **有效等级 = 声明与实测中较低的一档**：声明 L3 实测 L2 → 按 L2（承诺没兑现，
  不敢拿它做归因派生）；实测 L3 只声明 L1 → 也按 L1（作者没承诺跨版本稳定）。
  两个方向都只让平台保守，不让平台乐观。
- 错误一律带 JSON Pointer 路径（`/items/0/id`），否则"schema 校验失败"等于把定位
  工作全推回给 Skill 作者。
- 未知 `schema_version` **不报错**，降级成 L0 —— 新版 Skill 搬到旧平台时，
  报错中断会让它连业务产物都交不出来。
- `OutputEnvelope` 只记 `stage_result_submitted`/`stage_result_validation`/`compatibility_level`，
  不复制信封正文（与 `output_descriptor` 只存哈希同一条理由）。
- ⚠️ `skills` 应用未挂载进容器，改 `skills/views.py`、`skills/serializers.py` 后
  必须 `docker cp` 才生效（`knowledge_evolution` 是 `:ro` 挂载，改宿主即生效）。

**验收**：

- 旧 Skill 没有 `stage_result.json` 时仍可按 L0/L1 运行。
- 合法 L2/L3 输出能被平台统一解析。
- 缺稳定 ID、主产物声明或非法引用时返回具体字段错误。
- 结构化失败与业务生成失败在页面上明确区分。

---

## T06：将 e投票方案生成 Skill 升级为 L3（P0）

**状态**：已完成（2026-10-05，`tests_t06.py` 16 项通过；SKILL.md 升至 1.1.0）  
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

**人工验收方式**：包内 `examples/` 提供了一份合法样例与配套原文索引，可直接复跑
`python scripts/validate_stage_result.py examples/stage_result.valid.json --corpus examples/corpus.sample.json`
看到 `[OK]`；把样例里的 ID 改重复、证据改成不存在的编号、原句改一个词，
脚本会分别以非 0 退出码指出具体字段路径。

---

## T07：实现方案阶段适配器与平台生成物（P0）

**状态**：已完成（2026-10-05，`tests_t07.py` 27 项通过；新增 `derived_artifacts.py` 与 `StageDerivedArtifact` 模型 + `0039` 迁移）  
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

**状态**：已完成（2026-10-05，`tests_t08.py` 23 项通过；新增 `AttemptTraceService` 与 `/stage-attempts/{id}/trace|trace-spans|trace-span/`）  
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

**状态**：已完成（2026-10-05，`tests_t09.py` 29 项通过；新增 `review_reports.py`）  
**依赖**：T07  
**设计映射**：§7

**代码落点**：

- `WHartTest_Django/knowledge_evolution/review_reports.py`
- `WHartTest_Django/knowledge_evolution/workflow_feedback.py`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Vue/src/features/knowledge-evolution/`
- Excel 解析与反馈测试

**实施内容**：

- [x] 确认报告只保留四个人工列：人工结论、修改类型、修改内容、备注。
- [x] 人工结论仅允许：采纳、修改后采纳、删除；初始为空。
- [x] 修改类型仅允许：内容错误、覆盖不足、粒度不当、证据错误、优先级不当、重复或无效、其他。
- [x] 支持保存草稿与正式提交两个动作。
- [x] 正式提交时检查空白和条件必填关系。
- [x] 服务端扫描明细重算统计，不完全依赖公式缓存。
- [x] 最后一个 Sheet 保留“采纳率”标签，并增加有效保留率、修改率、删除率、确认完成率和证据准确率。
- [x] 形成版本化反馈，绑定原始 output、trace 和 SkillVersion。

**实现说明**：

- 正式提交校验失败抛 `ReviewSubmissionError` 而**不是** Django `ValidationError`：
  后者会把 dict 形式的逐行错误展平成字符串，"第 3 行缺修改类型"的**行号在半路就丢**。
  视图层单独接这个异常，原样回 400 + 行级错误数组。
- 拒绝必须发生在**写库之前** —— "提交被拒却留下一条草稿"会让人以为已经审过。
- 草稿与正式提交**共用同一个幂等键**（由文件哈希派生）：先存草稿再提交是同一条记录的
  状态推进，不是两条反馈；重复上传同一份已提交文件也命中同一条。
- 统计一律由服务端按明细行重算；「确认汇总」页只是给人看的，篡改它不影响入库值。
- 低采纳率**不是门槛**：照常入库并标出"低于参考线"，能否进进化由 `detail['state']` 决定。

**验收**：

- 空白结论可保存草稿，但不能进入进化。
- 修改后采纳缺修改类型或内容时拒绝正式提交。
- 删除缺修改类型时拒绝正式提交。
- 同一文件哈希重复提交保持幂等。
- 低采纳率可以记录，不作为上传门槛。

---

## T10：实现阶段人工补充文件上传（P0）

**状态**：已完成（2026-10-05，`tests_t10.py` 30 项通过；新增 `feedback_attachments.py`、`StageFeedbackAttachment` 模型 + `0040` 迁移、`file_management` `0005`）  
**依赖**：T07  
**设计映射**：§7.4、§13

**代码落点**：

- `WHartTest_Django/knowledge_evolution/models.py` 或新增反馈资产模型
- `WHartTest_Django/file_management/`
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Vue/src/features/knowledge-evolution/`
- 文件权限、哈希和项目隔离测试

**实施内容**：

- [x] 支持“确认后完整产物、人工补充产物、参考附件、问题证据”四种用途。
- [x] 文件绑定项目、流程、阶段、原始 output、用途、哈希和上传人。
- [x] 允许在方案、用例、执行和报告阶段分别上传。
- [x] 提供预览、下载、删除/退役和审计记录。
- [x] 上传后只进入反馈与候选解析，不自动成为金标或修改 Skill。
- [x] 重复哈希保持幂等；跨项目文件引用直接拒绝。

**实现说明**：

- 用途 / 阶段的合法取值是**模块级真值**（`feedback_attachments.ATTACHMENT_PURPOSES` /
  `ATTACHMENT_STAGES`），并通过 `workflow-stage-attachment-catalog` 接口下发给前端 ——
  前端各抄一份必然出现"页面能选、提交 400"。
- 物理文件复用 `file_management.FileAsset`，并**登记一条 `FileReference`**
  （新增 `ref_type=knowledge_evolution_feedback`）：`cleanup_unreferenced_files`
  删的是"零引用"文件，不登记引用就会把人工证据当垃圾清掉。
- **幂等按绑定判**，不按文件判：同一份材料可以既是"人工补充产物"又是"问题证据"，
  唯一约束落在 (项目, 流程, 阶段, 用途, 哈希)。项目内同哈希只保留一份物理文件。
- 退役是**软删除 + 记原因**，不做物理删除：它是某次人工确认的一部分，
  之后要回答"当时交了什么、后来为什么撤"只能靠它；重新上传同一份会复活该绑定。
- ⚠️ 产出主键是 **UUID**，不能按"是不是数字"判合法取出（第一版就是这里返 400）。

**验收**：

- 人工可以在用例阶段另行上传补充用例文件。
- 文件能追溯到对应阶段和原始产出。
- 上传动作不会隐式派生或激活 Skill。
- 无权限用户不能读取文件名、正文或下载地址。

---

## T11：实现差异、证据反查与人工确认归因（P1）

**状态**：已完成（2026-10-05，`tests_t11.py` 46 项 + 批次回归通过）  
**依赖**：T09、T10（已完成）  
**设计映射**：§8.1～§8.3

**代码落点**：

- `WHartTest_Django/knowledge_evolution/attribution.py`
- `WHartTest_Django/knowledge_evolution/evidence_graph.py`
- 新增方案差异服务
- `WHartTest_Django/knowledge_evolution/views.py`
- `WHartTest_Vue/src/features/knowledge-evolution/`
- 差异与归因测试

**实施内容**：

- [x] 解析采纳、修改后采纳、删除和人工补充内容。
- [x] 生成字段级结构化差异和证据状态变化。
- [x] 沿 `HumanEdit → 业务项 → DecisionRecord → EvidenceQuote → AgentStep → SkillRule` 反查。
- [x] 将问题归入现有八层责任模型。
- [x] 自动归因只创建 `proposed`，并保存反证和置信度。
- [x] 提供人工确认、改写和驳回归因的工作区。
- [x] 环境问题不得误生成 Skill 内容补丁。

**实现说明**：

- 新增 `stage_diff.py`（`stage-diff/v1`）：字段级差异 + 人工补充 + 契约问题 + 汇总。
  **列表字段先归一化再比**（`normalize_refs` 把中英文分隔符统一成「、」并排序去重）：
  人只是调了引用顺序不是内容修改，按字符串比会造出一批假差异，而假差异会一路变成假归因。
  `before` 恒取产出、`after` 恒取报告（方向固定为「平台 → 人工」），否则页面会显示成
  "平台改了他填的东西"。**未填且未改的行不算审过** —— 把它算成采纳，一次没人看的评审
  就会让进化以为"这版挺好"。
- `evidence_graph.py`：`NODE_TYPES` 增 `SkillRule`，`build_evidence_graph` 增可选
  `human_edits` / `skill`（不传时输出与改造前一致，保持"先建图、后评审"的顺序）；
  新增模块级 `REVERSE_CHAIN_ORDER` 与 `reverse_trace()`。链**按类型排序而不是遍历顺序**：
  图谱的边是无向可达的，按遍历顺序会让同一份数据换一次插入顺序就换一条叙事；
  `missing` 显式暴露断链（旧图谱本就没有 `SkillRule`），而不是画一条看起来完整的链。
- `attribution.py`：新增 `HumanEditAttributionService`（`hypothesize` / `run_for_output`）、
  `assert_usable_for_content_patch`，模块级 `EDIT_CATEGORY_RULES` / `ENVIRONMENT_ERROR_MARKERS`
  / `EVIDENCE_STATUS_RULES`；`AttributionService.rewrite()` 提供"改写"动作。
  - **反证先于结论**：`需求与知识都在` 与 `知识缺失` 在产出上长得一样，区别只在图谱里
    "召回了但没被引用"（`graph_signals.retrieved_unused`）那条状态。命中时判 `prompt_error`
    并附 `knowledge_available` 反证，而不是送去做知识补录。
  - 证据类差异用**图谱状态**纠正人工填的词：`invalid → retrieval_error`、
    `contradicted → knowledge_stale`；引用定位得到却判"证据错误"时记 `evidence_locatable`
    反证并把置信度压到 0.6。
  - ⚠️ `_upsert` **只在新创建时写 `proposed`**，已存在时只刷新内容、**不动 `state`**：
    用 `update_or_create` 会让"人工确认"被随后一次"重新归因"重置回待确认，
    确认这道闸门就成了可撤销的摆设。`layer` 显式写入（`save(update_fields=...)`
    不会落 `save()` 里补的值）。
  - 环境类失败**单独成条**且被 `assert_usable_for_content_patch` 剔除：改 Skill 修不好
    超时/配额/网络问题，却会在下一轮评测里表现为"改了也没用"，掩盖真正的环境故障。
  - 只记了 `error_type`、没记 `error_message` 的 span 很常见，取首行前必须判空 ——
    否则一次"环境失败"会把归因整条链路崩掉，而那恰恰是它最该被记下来的时刻。
- `workflow_feedback.py`：确认报告 `detail` 增 `human_rows`（**只留填过的行**）并由
  `WorkflowStageReviewService.latest()` 带出 —— 报告文件由人保管、可能离线流转，
  不在这里留档，差异就只能靠重新下载 Excel 才知道人改了什么。
- `views.py`（`FlywheelOperationsViewSet`）新增四个动作：
  `workflow-stage-diff`（差异 + 每项反查链 + 归因工作区，**一次读取保证三者同源**）、
  `workflow-stage-attribution-run`（只建 `proposed`）、
  `workflow-stage-attribution-decide`（确认/驳回，测试负责人）、
  `workflow-stage-attribution-rewrite`（改类别必须重算 `layer`）。
  派生物**读登记的图谱/质量摘要文件**而不是现场重算：检索结果事后会变，
  重算出的图谱与人工当时看到的不是同一张。
- 前端落点（`WHartTest_Vue/src/features/knowledge-evolution/`）待本轮后续批次统一接入。

**验收**：

- 每个归因能回到具体人工修改和原始业务项。
- 有需求和知识但已召回未使用时，能与“知识缺失”区分。
- 未经人工确认的归因不能进入候选派生。
- 归因被驳回后不会继续用于优化。

---

## T12：实现旁路产出纳管与替换语义（P1）

**状态**：已完成（2026-10-05，`tests_t12.py` 34 项 + 批次回归通过）  
**依赖**：T04（已完成）  
**设计映射**：§9

**代码落点**：

- `WHartTest_Django/knowledge_evolution/workflow_models.py`
- `WHartTest_Django/knowledge_evolution/operations.py`
- `WHartTest_Django/knowledge_evolution/lineage.py`
- `WHartTest_Vue/src/views/TestPlanGenerationView.vue`
- `WHartTest_Vue/src/features/knowledge-evolution/`
- 纳管、替换和冲突测试

**实施内容**：

- [x] 新增 `WorkflowStageSubmission` 或等价绑定模型。
- [x] 旁路生成完成后提供“纳入质量飞轮”。
- [x] 支持纳入新流程或符合条件的已有流程。
- [x] 校验项目、阶段、SkillVersion、父产出和目标阶段冲突。
- [x] 替换已有产出时要求显式确认并写 `supersedes_output_id`。
- [x] 保留旧产出、旧门禁和旧反馈；新产出重新进入待评测。
- [x] 首期拒绝同阶段并行分支。

**实现说明**：

- 新增 `workflow_models.WorkflowStageSubmission` + `0041` 迁移：**纳管是新增一条绑定，
  不是改写产出**。``GenerationOutput.metadata`` 是产出当时的协议快照，事后补写
  ``workflow_id`` 会让一份"从业务页面直接生成"的产出看起来从来就在受控流程里。
  唯一约束落在 (项目, 产出, 阶段)——一份产出只能属于一条链路的同一个阶段。
  语义集合（`SUBMISSION_TARGETS` / `SUBMISSION_STATES`）按项目铁律放**模块级**。
- 新增 `submissions.py`（`WorkflowSubmissionService`，与 T10/T11 一致：一个闭环环节
  一个文件，`operations.py` 保持门禁/流程编排职责）：`analyze`（预检）+ `admit`（纳管）
  + `list_for` / `submission_for` / `view`。
  - **预检与提交共用同一份判定**（`admit` 内部调 `analyze`），所以"预检说能纳管、
    提交却被拒"这种口径分叉在结构上不可能发生——前端可以放心把预检当成按钮可用性依据。
  - 拒绝**带码**（`SubmissionRefused.code`），可确认的码（`replace_not_confirmed`）
    走 409、硬错（版本不一致、父产出缺失、替换对象不对）走 400：前端据此决定
    "弹确认替换"还是"直接报错"，而不是只能弹一句"纳管失败"。
  - 硬错**优先**上报：同时命中"要确认替换"与"替换对象不对"时先报参数错，
    不让用户去确认一次注定失败的替换。
- ⚠️ 不能借 `WorkflowGateService.register_output` 建门禁：它按**产出 protocol 里的**
  `workflow_id` 判阶段归属，旁路产出的协议里没有（也不该有），于是它会直接 no-op ——
  后果不是报错，而是"纳管显示成功、门禁却没建"，点到"评测"才发现找不到对象。
  改为 `_attach_gate` 按**纳管目标流程**显式挂载 + 补锁 + best-effort 入候选队列。
- 替换语义：要求**显式** `replace_output_id` + `confirm_replace`（两个条件都要给），
  不允许"默认替换当前那一份"——那等于把"作废一次已完成的评审"变成默认行为。
  替换前先 `_snapshot_gate` 把旧门禁的 status/scores/reason/decided_by 留档，
  再重置 gate 为 `pending`（新产出必须重新过门禁，旧结论不能顺延）；
  旧产出、旧反馈原地保留，旧绑定的 `state` 降级为 `superseded`。
- 版本一致性：流程已有该阶段 `WorkflowSkillLock` 时，产出携带的 Skill 版本必须
  与锁一致，否则拒绝——纳进来的产出若来自另一个版本，之后所有归因与候选都会指向错的对象。
- `lineage.py`：`trace()` 增 `submission` 字段（无纳管时为 `None`，不伪造对象），
  `stages.binding.ok` 把"已纳管"也算通过，并给出 `submitted` 便于前端区分
  "从没纳管过"与"纳管了但没锁到版本"。
- 接口：`workflow-submission-catalog`（选项真值由后端下发）、
  `workflow-stage-submission-preflight`、`workflow-stage-submit`、`workflow-stage-submissions`。
- 前端落点（`TestPlanGenerationView.vue` 的"纳入质量飞轮"按钮与
  `features/knowledge-evolution/`）待本轮后续批次统一接入。

**验收**：

- 直接从方案分析生成的产出可以显式纳管。
- SkillVersion 与流程锁不一致时拒绝纳管。
- 纳管不篡改原 `GenerationOutput` 历史协议。
- 替换后旧版本仍可追溯，新版本必须重新评测。

---

## T13：实现 `skill_content` 候选、评测与上线闭环（P1）

**状态**：已完成（2026-10-06：`tests_t13.py` 71 项全绿；`knowledge_evolution` + `file_management` + `skills` 全量回归 1200 项 OK）  
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

- [x] 新增 `skill_content` 优化类型。
- [x] 允许修改白名单：`SKILL.md`、相关 references、schemas、模板和确定性校验脚本。
- [x] 候选只能来自已确认归因和允许用于优化的数据。
- [x] 生成最小增量补丁、风险说明、预期收益和回滚目标。
- [x] 创建新的不可变 `SkillVersion`，不修改 active 包。
- [x] 在相同冻结数据集、模型和配置上执行基线/候选对照。
- [x] 强制本轮 Badcase 修复、关键回归零退化、schema 通过和隐藏集隔离。
- [x] 支持审批、灰度、激活、观察和回滚。

**实现说明**：

- 新增 `optimization_models.OptimizationProposal.TYPE_CHOICES` 增加 `skill_content`
  （迁移 `0042_alter_optimizationproposal_proposal_type`）。⚠️ 它**刻意不进**
  `TYPE_BY_CATEGORY`：`skill_content` 是**目标型候选**——由工坊人显式指定
  `target_type="skill_content"`，整批已确认归因归为一个候选。把"某类归因只能产出某种
  候选"写死进映射表，等于让人对"同一失败该走哪条候选通道"的判断失去出口。
  `optimization.py` 新增模块级 `SKILL_CONTENT_TYPE` / `TARGET_TYPES` /
  `SKILL_CONTENT_TYPES` / `SKILL_CONTENT_CATEGORIES` / `CATEGORY_CHANNELS`
  （真值单点，`skill_evolution.py` 直接引用，不再各写一份）。
- 白名单真值 `skill_evolution.SKILL_CONTENT_WHITELIST`：`SKILL.md`、`references/*.md`、
  `schemas/*.json`、`templates/*.{md,txt,json,j2,jinja2}`、`scripts/{validate,check}*.py`。
  **无 catch-all 模式**（测试 `test_whitelist_has_no_catch_all_pattern` 钉死）。
  校验顺序是先 `normalize_package_relative_path` 再 `fnmatch`：拒绝绝对路径/盘符/`..`，
  归一化 `.\`→`/`、去 `./` 与空段——否则 `./SKILL.md` 或 `a/../../SKILL.md` 能绕过匹配。
  ⚠️ `remove` 列表**一并校验**：只校验写路径等于给"删文件"留后门。
  刻意不放松依赖声明、凭据、`entrypoint` 与任意路径——那是"换一个包"不是"改一个包"，
  放进来等于绕开上传通道的静态扫描。
- 新增 `skill_content.py`（一个闭环环节一个文件，与 T10/T11/T12 一致）：
  - `SkillContentOptimizationService.materialize`：**硬校验顺序**为 类型/状态 → 候选须来自
    **已确认归因** → `assert_usable_for_content_patch` 剔除 `environment` 层且不许全为环境
    → 类别须在 `SKILL_CONTENT_CATEGORIES`（否则给 `ALTERNATIVE_CHANNELS` 提示"这类失败
    该走知识/检索通道或修运行环境"）→ 禁止优化金标样本 → 构补丁 → 派生 → 回写
    `change_patch`/`risk_notes`/`rollback_plan`。环境类归因**单独成条且被剔除**，
    不是整批拒掉——八层责任模型（R6）里环境问题改 Skill 包本就无效。
  - 派生复用 `SkillEvolutionService.derive_candidate`（临时目录 apply_patch → 静态校验
    `scan_package_dir` → 递增版本号 → `create_candidate_from_dir(source_type=evolution)`），
    并用 `verify_package_integrity` 断言**基线包零改动**，返回 `active_untouched=True`。
    幂等键是 `change_patch.materialized_skill_version_id`，重复调用返回 `reused=True`，
    不会连出两个候选版本。
  - 逐样本门禁底座 `evaluation.CaseComparisonService`（`LEVEL="l1"`）：`score_map`
    **跳过无分数的样本而不记 0**——记 0 会把"没跑"伪装成"退化"，把门禁变成运气。
    `compare_cases` 按 delta 升序返回，最差在前，便于工坊直接展示退化样本。
  - `SkillContentGateService`：T09 `EvaluationGateService.run_gate` + 四条 T13 硬门禁
    （`badcase_fixed` / `key_regression_zero_degradation` / `schema_passed` /
    `hidden_isolation` / `freeze_comparable`）。要点：Badcase 无目标样本时 `ok=False`
    且 detail 提示"冻结评测集"，不是默认通过；关键回归**优先**读 metadata 显式标注，
    无标注则退化为"基线已通过的 regression 样本"，**一条都没有则 `ok=False`**
    （没有回归基线 ≠ 没退化）；schema 同时要求 `validation_report.ok` 与
    `verify_package_integrity.ok`；隐藏集隔离按**目标 case 与 hidden 分区求交**，
    再按 `source_output` 复检一次共享产出；冻结一致性校验 gold 处于 frozen、
    `replay_hash == content_hash`、`FREEZE_CONFIG_KEYS`（模型/温度/seed/top_k…）两侧一致。
    `run()` 结尾走模块级 `assert_can_reach(release.state, "shadow")`——**走合法状态边**
    而不是直接赋值，`draft→shadow` 必须是状态机认可的迁移。
  - `SkillContentLifecycleService` 只加两道前置条件（门禁没过不许提交审批、激活前复看
    门禁结论），其余**全部委托 `CapabilityReleaseService`**——发布状态机真值只有一份，
    另造一套必然漂移。`running_flow_evidence(skill)` **回源读库**而不是用调用方传入的
    skill 实例（调用方手上常是激活前的内存快照，拿它读 `active_version` 会把"没生效"
    报成"已生效"，比不报更糟），并给出 `locked_flows` 证明激活只改解析缓存、
    不回溯改写已发出的流程锁。
- 接口（`views.py` `OptimizationProposalViewSet`，均限项目）：`skill-content-plan`(GET)、
  `skill-content-materialize`(POST，reused→200 否则 201)、`skill-content-evaluate`(POST)、
  `skill-content-submit-approval`、`skill-content-activate`、`skill-content-reject`、
  `skill-content-observe`(201)、`skill-content-rollback`、`skill-content-running-flows`(GET)。
  `generate` 支持 `target_type` 入参。金标版本、评测运行、`WorkflowSkillLock` 均按项目过滤，
  跨项目 payload 返回 404 而不是 403（不泄露"这个 id 存在但不归你"）。
- 测试 `tests_t13.py` **71 项**：真值/白名单（含 `./` 归一化、越界、remove、无 catch-all）、
  派生（新不可变版本且 active 未被碰、显式 edits 命中 references、越界编辑不留痕、
  环境归因被剔除而非致命）、四条硬门禁各自的通过/拒绝分支
  （含 `test_higher_average_but_key_regression_blocks`：**先断言均分确实涨了**，
  再断言关键回归失败并阻止晋级、proposal 回落 draft）、生命周期
  （未跑门禁不许提交审批 / 门禁失败不许提交 / 驳回须记原因 / 激活不碰运行锁 /
  观察超阈值自动回滚 / 回滚复原基线 / 只有测试负责人可操作，执行人员 403）、
  API、`CaseComparisonService`。
- 前端 Skill 进化工坊页面（候选计划 / 门禁摘要 / 派生 / 评测 / 审批 / 激活 / 观察 / 回滚）
  待本轮后续批次统一接入，本轮交付后端全链路与接口。

**验收**：

- 工坊能从具体人工修改追溯到候选补丁来源。
- 未确认归因、禁止优化样本或隐藏期望泄漏时拒绝派生/晋级。
- 候选改善平均分但关键案例退化时阻止激活。
- 激活新版本不影响已锁定旧版本的运行中流程。

---

## T14：完成全链路验收、补偿和灰度上线（P1）

**状态**：已完成（2026-10-06：`tests_t14_launch.py` 25 项全绿；`knowledge_evolution` + `file_management` + `skills` 全量回归 1225 项 OK；`0043_flywheelregistrationfailure` 迁移经临时库实测空库建/回退/再升级与存量兼容；三份上线手册已落盘）  
**依赖**：T07、T08、T09、T10、T11、T12、T13  
**设计映射**：§13～§15

**代码落点**：

- Django 单元、集成与迁移测试
- Vue 构建与关键交互测试
- 端到端测试
- 异步补偿、任务中心与审计命令
- 上线和回滚文档

**实施内容**：

- [x] 跑通“飞轮发起 → 方案分析 → 正式产出 → 确认 → 归因 → 候选 → 评测 → 激活”。
- [x] 建立实时轨迹、引用权限、文件权限和项目隔离测试。
- [x] 建立 Agent 中途失败、SSE 断线、产出发布失败和飞轮登记失败测试。
- [x] 飞轮写入失败进入幂等重试和死信告警，不只记录日志。
- [x] 执行 migration dry-run、存量兼容和回滚验证。
- [x] 增加项目级开关，先灰度上证 e 投票方案阶段。
- [x] 输出上线检查表、异常补偿手册和回滚手册。

**实现说明**：

- 新增 `rollout.py` 作为**灰度开关的唯一真值**：`linkage_enabled` / `linkage_state` /
  `assert_linkage_enabled` / `LaunchReadinessService`。开关语义是 **opt-in**——
  "未配置 = 关闭"。若写成"未配置 = 开启"，灰度就等价于全量开放，
  "先灰度上证 e 投票方案阶段"这句话会失去意义。
  ⚠️ 开关**只闸控制面入口，不闸执行面**：闸门落在
  `flywheel-runs` 的创建与 `open`、`operations/start-workflow`、`workflow-stage-submit`
  以及历史导入/回放（原先散在 3 处的 `未开启质量飞轮` 文案也统一到这里）。
  `publish_output`、门禁登记、反馈、归因**一律不受开关影响**——非目标里写明
  "不重建第二套 Agent"，把执行面也闸住等于让飞轮从"可选控制面"变成"业务前置依赖"；
  而且运维事后关开关会把一条跑到一半的受控链路掐断。
- ⚠️ 闸门顺序是**先成员、后开关**。反过来的话，非成员会因为"项目没开开关"拿到 403，
  与成员同样的状态码——等于顺手把"这个项目是否已灰度"泄露给不该知道的人。
  这也是 `views._ensure_linkage_enabled` 用 **403**（权限语义）而不是 400 的原因：
  400 会把用户引向去检查 payload，而 payload 一点问题都没有。
- 新增 `registration.py` + `FlywheelRegistrationFailure` 模型（迁移 `0043`）：
  把"业务已产出、飞轮没登记上"变成一条**可查、可重试、可告警**的记录。
  三句话各自对应一个不能省的取舍：
  - **"不得让业务产物丢失"** —— 登记失败发生在 `record_task_output` 之后，
    且失败**不回滚**业务写入。用"飞轮的账没记上"去销毁"业务已经产出的东西"，
    是这类联动里代价最大的一个错。
  - **"不得只记日志"** —— 上线后要回答的是"**有没有**产出没登记上"，
    而这个问题在日志里搜不出来（只有出现的反例，没有缺席的正例）。
  - **"幂等事件"** —— 幂等键 =（产出, 流程, 阶段），重复失败只累加 `attempts`。
    否则一次网络抖动重试三次会让控制台显示"3 个产出登记失败"，
    实际只有一个；告警数字一旦失真就再没人相信它。
  `publish_output` 改为经 `FlywheelRegistrationService.register` 登记，**绝不抛出**。
  状态语义 `REGISTRATION_{PENDING,FAILED,DEAD_LETTER,RESOLVED}` +
  `REGISTRATION_OPEN_STATES` / `REGISTRATION_ALERT_STATES` 按项目铁律放**模块级**
  （`workflow_models.py`）。⚠️ `alert` **只看死信**：把 `failed` 也算告警会让控制台
  在正常重试窗口里持续闪红，真正该看的人很快就不看了。
  非受控流程产出（协议里本就没有 `workflow_id`）返回 `reason=not_workflow_output`
  且**不落记录**——正常的旁路产出记成失败，死信队列会被噪声淹没。
- `LaunchReadinessService`：把上线检查表里**可自动判定**的条件变成一次可复现调用
  （迁移是否全部应用、跨项目引用、死信积压、手册齐备、开关状态）。
  ⚠️ 两条设计取舍：① `linkage_flag_configured` / `linkage_switch_state` 是**软条件**，
  不影响 `ready`——"该不该开开关"是自检通过之后才决定的事，算成硬条件会绕成死循环；
  ② 手册目录不可达时该检查显式标 **`skipped`** 而不是 `ok`——把"我判不了"写成"通过"，
  等于用一条假绿抹掉上线检查表上真正该人工确认的那一项（手册是发布物，
  不在后端运行时镜像里，所以目录通过 `FLYWHEEL_RUNBOOK_DIR` 可配 + 源码树祖先目录兜底）。
- 接口：`GET flywheel-settings/state/`（成员可读，前端据此决定入口可见性）、
  `GET operations/registration-failures/`（`&detail=1` 出明细）、
  `POST operations/registration-failures-retry/`（负责人）、
  `GET operations/launch-readiness/`（成员可读，上线前可反复跑）。
- 测试 `tests_t14_launch.py` **25 项**（文件名刻意避开既有的 `tests_t14.py`，
  那是另一套编号体系下的"用例审查接入飞轮"）：开关（未配置=关闭 / 四入口全拒且不留半成品 /
  关闭不影响业务生成 / 非成员不泄露配置 / 仅在途链路不受事后关闭影响）、
  登记补偿（幂等累加 / 到阈值转死信 / 成功重试关闭 / 已解决后再失败复用同一条 /
  `publish_output` 不抛 / 旁路产出不产生噪声）、补偿队列接口与隔离、
  上线就绪（含手册检查的 ok/skipped 双路径）、端到端链路
  （发起 → 锁版本 → `resolve-stage` → 正式产出 → 门禁 → 候选入队，每跳 id 可回走）、
  最小人工确认（空白可存草稿但必填缺失即拒）、轨迹权限、
  失败补偿（Agent 失败保留 Span 且不冒充产出、重试新建并带 `retry_of`、
  发布失败不凭空建门禁）。
- 迁移实测（独立临时库 `t14_mig_check`，演练后已删除，**未触碰开发库**）：
  `makemigrations --check --dry-run` → `No changes detected`；
  空库全量 `migrate` → 回退 `0042` → 再升 head 三步均成功；
  回退到 `0038` 后插入一行存量 `OptimizationProposal(proposal_type="prompt")`，
  再升到 head：老行**原样保留**（`prompt/draft`），`skill_content` 选项生效，
  新表可读写。`0042` 只改 `choices`（非 DB 约束）、`0043` 只建表，均为纯增量、可逆。
- 三份手册落 `runbook/`：`go-live-checklist.md`（自动化项 + 人工项 + 灰度步骤 + 回退触发条件）、
  `compensation-runbook.md`（三类失败分开处置 + 队列入口 + 状态语义 + 隔离事故流程）、
  `rollback-runbook.md`（L1 关开关 / L2 回滚 Skill 发布 / L3 回滚镜像三层，
  含"为什么先用代价小的"和回滚完成判据）。
- 前端「跳转到Agent执行」口径已统一（`design.md` §4.2 与 §15 A、
  `AIQualityEvolutionView.vue`），开关状态接口可供前端在渲染入口前判断可见性；
  飞轮 UI 的其余落点（T09–T13 页面）仍待后续批次接入。

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
