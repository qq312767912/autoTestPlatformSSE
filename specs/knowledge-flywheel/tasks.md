# 知识数据飞轮与自进化实施计划

## Phase 0：基线和可观测

- [x] 1. 建立检索轨迹和生成输出模型
  - 记录查询改写、各召回通道、排序、引用、Token、耗时和版本
  - 日志写入失败时不阻断主任务，但标记不可进入飞轮
  - _Requirements: R1, R5, R9, R10_

- [x] 2. 在现有业务流程植入标准化输出标识
  - 已接入知识库查询、代码审查、测试执行、用例生成（agent 输出记录）
  - 将反证裁决、确定缺陷、待确认风险和建议分别建模
  - _Requirements: R5, R6_

- [x] 3. 建立种子评测集
  - 已新增 `EvaluationSuite` / `EvaluationCase` 模型与 `build_seed_evaluation_set` 命令
  - 从真实 Java/Python/Vue 仓库、知识库问答和已复核任务中取 50–100 条
  - 去重、固定 split，记录证据和标注人
  - _Requirements: R7, R10_

## Phase 1：知识资产化与文档图谱

- [x] 4. 创建 `knowledge_evolution` 边界和核心数据模型
  - 已实现 SourceSnapshot、KnowledgeAsset/Version/Evidence、Candidate、Conflict、IndexProjection、AuditLog
  - 已实现状态机、幂等键、ACL 和审计字段
  - _Requirements: R1, R2, R3, R9_

- [x] 5. 升级文档处理管线
  - 已实现 `StructuredTextChunker`，标题/段落/表格/代码块优先分块
  - `DocumentChunk` 已增加 `section_title`、`block_type`、`location` 字段
  - _Requirements: R1, R2_

- [x] 6. 实现知识提炼、去重、冲突和分级
  - 已实现 `RuleBasedExtractor`、`CandidateDeduplicator`、冲突检测与分级
  - 已提供 `extract_knowledge_candidates` 管理命令
  - _Requirements: R2, R3_

- [x] 7. 实现版本化 Qdrant 投影
  - 已实现 `ProjectionService`：collection + alias 双缓冲切换
  - 已新增 `IndexProjectionOutbox`，支持 Outbox 幂等写入、重建、回滚
  - 已新增 Celery 任务 `sync_projection_to_index` / `rollback_projection_alias`
  - _Requirements: R3, R4, R8, R9_

- [x] 8. 接入文档图谱
  - 已实现中性 `GraphSource` API + `PostgreSQLGraphSource`
  - 已实现 `KnowledgeDocumentGraphAdapter`：`build_from_snapshot/version/candidate`
  - 新增 `GraphNode` / `GraphEdge` 模型与 `build_document_graph` 管理命令
  - 新增 `knowledge_evolution/graph_client.py` `KnowledgeDocumentGraphClient`，把文档图包装为前端 `GraphSnapshot` 协议
  - 新增 `RequirementGraphAdapter` / `TestCaseGraphAdapter` 与对应 `RequirementGraphClient` / `TestCaseGraphClient`
  - `code_analysis/views.py::KnowledgeGraphSourceViewSet` 已支持 `knowledge_document` / `requirement` / `test_case` 类型，前端 `/knowledge-graph` 可切换「代码仓库 / 知识库文档 / 需求 / 测试用例」并浏览对应节点
  - 不改写 CRG 代码图谱数据库
  - _Requirements: R1, R4, R9_

## Phase 2：融合检索、反馈和测评

- [x] 9. 拆分可版本化检索编排器
  - 已实现 `RetrievalOrchestrator` + `RetrievalPolicy` 模型
  - 支持 Dense/Sparse/Structured/Graph/Historical 多路召回、加权 RRF、MMR 去冗余
  - 返回证据包、开放冲突/图 CONTRADICTS 标注、可验证 citation
  - 支持按任务类型路由图谱、延迟/Token 预算控制
  - 已接入现有知识库查询主链路（`knowledge/services.py::query()`），注入 `VectorManager.similarity_search` 作为 Dense 召回源
  - _Requirements: R4, R5, R9_

- [x] 10. 实现标准 Feedback Event
  - 已新增 `FeedbackContext` / `FeedbackService`：支持 `accepted`/`rejected`/`edited`/`test_result`/`defect`/`merged`/`rolled_back` 等信号
  - 强制关联 `task_id`/`output_id`/`trace_id`/`version_ids`，按 idempotency_key + actor 去重，含冷却/反刷/编辑 diff
  - `FeedbackEvent.knowledge_version_ids` 已从 JSONField 升级为 `FeedbackEvent.knowledge_versions` ManyToMany，保留引用完整性并可按版本统计
  - _Requirements: R6, R9_

- [x] 11. 实现评测集和回放引擎
  - 已新增 `EvaluationRun` / `EvaluationResult` 模型与 `EvaluationEngine`
  - 支持按 split / task_type 切片，计算 L0–L3 指标、成本汇总、配对 t 统计量
  - _Requirements: R7, R10_

- [x] 12. 实现反馈与测评界面
  - 已扩展 DRF API：`retrieval-traces` / `generation-outputs` / `feedback` / `evaluation-suites` / `evaluation-runs` / `evaluation-results` / `knowledge-candidates`
  - 已补齐 `knowledge-assets` / `knowledge-versions` / `knowledge-conflicts` / `knowledge-evidence` / `knowledge-audit-logs` / `knowledge-retrieval/search` REST 端点
  - 已新增 `POST /api/knowledge-evolution/feedback/outcomes/` 外部客观信号回填、`GET /api/knowledge-evolution/evaluation-runs/{id}/comparison/` 运行对比
  - 已新增 `EvaluationReviewBridge`：从失败样本归因到 trace/output/version，一键生成 `KnowledgeCandidate`（origin=evaluation_failure）
  - `POST /api/knowledge-evolution/evaluation-runs/{id}/generate-review-candidates/` 支持一键转候选
  - _Requirements: R5, R6, R7, R10_

## Phase 3：受控自进化

- [x] 13. 实现二次经验蒸馏
  - 已实现 `ExperienceDistiller`：至少 3 个去重任务，或至少 2 个独立客观信号
  - 已按任务类型、正负方向和原因编码聚类，脱敏评论且不复制原始问答正文
  - 已支持幂等更新，只生成 `KnowledgeCandidate(origin=distillation)`，不直接发布
  - 已提供 `POST /api/knowledge-evolution/knowledge-candidates/distill-feedback/`
  - _Requirements: R6, R9_

- [x] 14. 实现能力候选与发布单元
  - 已新增 `CapabilityRelease` / `PromotionDecision`，支持 Knowledge/RetrievalPolicy/Prompt/Skill/Agent 版本
  - 已记录配置哈希、候选、基线/候选评测、上一生产版本和审批审计
  - _Requirements: R7, R9_

- [x] 15. 实现影子运行与晋级门禁
  - 已实现同评测集 baseline/candidate 配对对比，覆盖 L0–L3
  - 已实现样本一致、L1/L2 不回归、延迟和 Token 预算硬门禁；通过后进入人工审批
  - _Requirements: R7, R9, R10_

- [x] 16. 实现发布、监控和回滚
  - 已实现项目 + 能力类型维度的原子 active release 切换
  - 已实现人工晋级、决策审计和恢复 previous release 的一键回滚 API
  - _Requirements: R7, R9, R10_

## Phase 4：防腐、联合图与运营

- [x] 17. 实现知识防腐定时任务
  - 已实现每日健康巡检、失败/过期/冲突/孤立节点检查
  - 已实现每周 stale/failed 投影幂等重建入口和每月飞轮指标快照任务
  - _Requirements: R3, R8_

- [x] 18. 建立需求—代码—用例—执行—缺陷联合图
  - 已实现 `platform-output/v1` 统一协议和八阶段适配器
  - 已用 `workflow_id + parent_output_ids + supersedes_output_id` 连接风险—方案—用例—执行—问题链路
  - 已实现 `WorkflowGraphBuilder`，生成 `workflow_output / FEEDS_INTO` 联合图
  - _Requirements: R1, R4, R9_

- [x] 19. 上线数据飞轮运营看板
  - 按“暂停单独扩展控制台”的产品决策，已先完成公共指标 API
  - 支持轨迹/产出/反馈、采纳率、误报、漏报、Token、L0–L3、发布/回滚和冲突指标
  - _Requirements: R10_

- [x] 20. 执行真实场景验收
  - 已提供 `run_flywheel_acceptance` 可重复验收命令，缺少真实轨迹时会明确失败而不以模拟数据冒充
  - 本机项目 1 已覆盖知识问答、代码审查、用例生成、测试执行四类真实轨迹
  - Java 与平台混合 Python/Vue 仓库同 Commit 的 CRG 对比结果保存在 `code-review-graph-integration/poc-results.md`
  - _Requirements: R4, R7, R8, R10_

## Phase 5：飞轮成为平台公共能力

- [x] 21. 建立平台级能力定义模型
  - 新增 `CapabilityDefinition`：区分 `single`（单次测评）与 `workflow`（链路自进化）两种 evaluation_mode
  - 支持 `kind`（knowledge/retrieval_policy/prompt/skill/agent）、有序 `stages`、默认评测集、晋级门禁规则
  - 与 `CapabilityRelease` 绑定当前生产生效版本
  - _Requirements: R7, R9, R10_

- [x] 22. 补齐测试用例审查到飞轮链路
  - `testcases/review_service.py::run_testcase_review` 完成后调用 `publish_output`，stage=`case_review`
  - 审查摘要、问题列表、未覆盖批次进入 `RetrievalTrace`/`GenerationOutput`
  - 审查报告摘要中记录 `trace_id`/`output_id`
  - _Requirements: R5, R6_

- [x] 23. 实现能力自进化引擎
  - 新增 `CapabilityEvolutionService`：按能力定义自动从历史产出 + 反馈构建评测集并打分
  - single 模式：每个产出按反馈信号映射为 L1 质量分
  - workflow 模式：按 `workflow_id` 聚合链路各阶段，按默认权重加权得到综合分
  - 支持 `candidate_config` 自动生成 `CapabilityRelease` 并执行影子门禁
  - _Requirements: R6, R7, R10_

- [x] 24. 暴露能力定义与自进化 REST 端点
  - `GET/POST/PATCH /api/knowledge-evolution/capability-definitions/`
  - `POST /api/knowledge-evolution/capability-definitions/{id}/activate-release/`
  - `POST /api/knowledge-evolution/capability-definitions/{id}/run-evolution/`
  - 统一产出协议 `OutputEnvelope` 支持 `capability` 字段，Agent 调用可传入 `capability_id`
  - 前端 `knowledge-evolution/service.ts` 与 `types.ts` 同步新增能力相关类型与接口

- [x] 25. 把测试执行接入链路自进化
  - `TestExecution` 新增 `workflow_id`/`capability`/`source_output` 字段（迁移 `0028`）
  - `TestExecutionCreateSerializer` 支持传入 `workflow_id`/`capability_id`/`source_output_id`
  - `record_test_execution` 把 `workflow_id`/`capability_id` 写入 `GenerationOutput.metadata.protocol`
  - 使 `风险识别→测试方案→用例生成→测试执行→问题跟踪` 五阶段可按同一 `workflow_id` 聚合打分
  - _Requirements: R5, R6, R7_

- [x] 26. 把代码审查产出绑定到单次能力定义
  - `record_code_review_task` 未显式传 `capability` 时，自动按项目 get_or_create `代码审查` 单次能力定义
  - 代码审查产出带 `capability` 字段，可被 `CapabilityEvolutionService` 聚合评测
  - _Requirements: R6, R7_

- [ ] 27. 在各业务页面增加显式反馈按钮
  - 用例审查、代码审查、知识库问答结果页增加「采纳/驳回/编辑」按钮，调用 `POST /api/knowledge-evolution/feedback/`
  - 测试执行、缺陷跟踪页增加「回填客观结果」按钮，调用 `POST /api/knowledge-evolution/feedback/outcomes/`
  - 反馈写入后作为真实信号驱动能力自进化
  - _Requirements: R5, R6_

- [ ] 28. 把需求风险识别/测试方案/用例生成非 Agent 入口接入飞轮
  - 识别 `requirements/views.py`、`testcases/views.py` 中非 Agent 调用但产生同类产出的接口
  - 在出口处调用 `publish_output` 或 `record_task_output`，统一 `stage`、`workflow_id`、`capability_id`
  - _Requirements: R5, R6_
  - _Requirements: R5, R7, R9, R10_
