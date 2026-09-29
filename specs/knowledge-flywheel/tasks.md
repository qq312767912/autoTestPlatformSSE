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
  - 不改写 CRG 代码图谱数据库
  - _Requirements: R1, R4, R9_

## Phase 2：融合检索、反馈和测评

- [x] 9. 拆分可版本化检索编排器
  - 已实现 `RetrievalOrchestrator` + `RetrievalPolicy` 模型
  - 支持 Dense/Sparse/Structured/Graph/Historical 多路召回、加权 RRF、MMR 去冗余
  - 返回证据包、开放冲突/图 CONTRADICTS 标注、可验证 citation
  - 支持按任务类型路由图谱、延迟/Token 预算控制
  - _Requirements: R4, R5, R9_

- [x] 10. 实现标准 Feedback Event
  - 已新增 `FeedbackContext` / `FeedbackService`：支持 `accepted`/`rejected`/`edited`/`test_result`/`defect`/`merged`/`rolled_back` 等信号
  - 强制关联 `task_id`/`output_id`/`trace_id`/`version_ids`，按 idempotency_key + actor 去重，含冷却/反刷/编辑 diff
  - _Requirements: R6, R9_

- [x] 11. 实现评测集和回放引擎
  - 已新增 `EvaluationRun` / `EvaluationResult` 模型与 `EvaluationEngine`
  - 支持按 split / task_type 切片，计算 L0–L3 指标、成本汇总、配对 t 统计量
  - _Requirements: R7, R10_

- [x] 12. 实现反馈与测评界面
  - 已扩展 DRF API：`retrieval-traces` / `generation-outputs` / `feedback` / `evaluation-suites` / `evaluation-runs` / `evaluation-results` / `knowledge-candidates`
  - 已新增 `EvaluationReviewBridge`：从失败样本归因到 trace/output/version，一键生成 `KnowledgeCandidate`（origin=evaluation_failure）
  - `POST /api/knowledge-evolution/evaluation-runs/{id}/generate-review-candidates/` 支持一键转候选
  - _Requirements: R5, R6, R7, R10_

## Phase 3：受控自进化

- [ ] 13. 实现二次经验蒸馏
  - 使用最小样本数、客观信号优先、脱敏和去个人化
  - 只生成 KnowledgeCandidate，不直接发布
  - _Requirements: R6, R9_

- [ ] 14. 实现能力候选与发布单元
  - 版本化 RetrievalPolicy/Prompt/Skill/Agent 配置
  - 关联构建器、模型、依赖、评测和 previous release
  - _Requirements: R7, R9_

- [ ] 15. 实现影子运行与晋级门禁
  - 同输入对比 baseline/candidate，候选不对用户可见
  - 硬门禁、软门禁、切片回归、专家+管理员审批
  - _Requirements: R7, R9, R10_

- [ ] 16. 实现发布、监控和回滚
  - 原子切换项目级 active release
  - 安全/L1/延迟/误报阈值自动回滚，支持人工一键回滚
  - _Requirements: R7, R9, R10_

## Phase 4：防腐、联合图与运营

- [ ] 17. 实现知识防腐定时任务
  - 每日过期/断链/孤儿，每周投影对账，每月全量评测
  - 提供从来源快照重建 Qdrant 和文档图谱的演练脚本
  - _Requirements: R3, R8_

- [ ] 18. 建立需求—代码—用例—执行—缺陷联合图
  - 用统一 ID 和证据边连接各边界，支持影响链与覆盖缺口
  - 保持每个源的权限边界和独立重建能力
  - _Requirements: R1, R4, R9_

- [ ] 19. 上线数据飞轮运营看板
  - 展示有效知识率、被引用率、任务收益、人工成本、腐化和回滚
  - 支持按版本、项目、任务和时间窗口下钻
  - _Requirements: R10_

- [ ] 20. 执行真实场景验收
  - Java/Python/Vue 代码审查、文档问答、需求转用例、故障根因四类场景
  - 对比质量、漏报/误报、Token、耗时、建索/增量耗时和复核成本
  - _Requirements: R4, R7, R8, R10_
