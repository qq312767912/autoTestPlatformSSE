# 知识数据飞轮与自进化实施计划

## Phase 0：基线和可观测

- [x] 1. 建立检索轨迹和生成输出模型
  - 记录查询改写、各召回通道、排序、引用、Token、耗时和版本
  - 日志写入失败时不阻断主任务，但标记不可进入飞轮
  - _Requirements: R1, R5, R9, R10_

- [x] 2. 在现有业务流程植入标准化输出标识
  - 先接入知识库查询、代码审查、用例生成和测试执行
  - 将反证裁决、确定缺陷、待确认风险和建议分别建模
  - _Requirements: R5, R6_

- [ ] 3. 建立种子评测集
  - 从真实 Java/Python/Vue 仓库、知识库问答和已复核任务中取 50–100 条
  - 去重、固定 split，记录证据和标注人
  - _Requirements: R7, R10# 知识数据飞轮与自进化实施计划

## Phase 0：基线和可观测

- [x] 1. 建立检索轨迹和生成输出模型
  - 记录查询改写、各召回通道、排序、引用、Token、耗时和版本
  - 日志写入失败时不阻断主任务，但标记不可进入飞轮
  - _Requirements: R1, R5, R9, R10_

- [ ] 2. 在现有业务流程植入标准化输出标识
  - 先接入知识库查询、代码审查、用例生成和测试执行
  - 将反证裁决、确定缺陷、待确认风险和建议分别建模
  - _Requirements: R5, R6_

- [ ] 3. 建立种子评测集
  - 从真实 Java/Python/Vue 仓库、知识库问答和已复核任务中取 50–100 条
  - 去重、固定 split，记录证据和标注人
  - _Requirements: R7, R10_

## Phase 1：知识资产化与文档图谱

- [ ] 4. 创建 `knowledge_evolution` 边界和核心数据模型
  - 实现 SourceSnapshot、KnowledgeAsset/Version/Evidence、Candidate、Conflict、IndexProjection
  - 实现状态机、幂等键、ACL 和审计字段
  - _Requirements: R1, R2, R3, R9_

- [ ] 5. 升级文档处理管线
  - 从纯 token 分块升级为标题/段落/表格/代码块优先的结构分块
  - 保留页码、章节、解析器版本和原文定位
  - _Requirements: R1, R2_

- [ ] 6. 实现知识提炼、去重、冲突和分级
  - 规则与 LLM 分层提取概念、关系和知识原子
  - L1 候选必须具备权威证据和双人审批
  - _Requirements: R2, R3_

- [ ] 7. 实现版本化 Qdrant 投影
  - collection + alias 双缓冲切换，payload 带完整版本与 ACL
  - Outbox + Celery 幂等写入，支持对账、重建和回滚
  - _Requirements: R3, R4, R8, R9_

- [ ] 8. 接入文档图谱
  - 实现 KnowledgeDocumentGraphAdapter 和中性 GraphSource API
  - 展示 Document/Section/Concept/Rule/Evidence 节点和语义关系
  - 不改写 CRG 代码图谱数据库
  - _Requirements: R1, R4, R9_

## Phase 2：融合检索、反馈和测评

- [ ] 9. 拆分可版本化检索编排器
  - 并行 Dense/Sparse/Structured/Graph，weighted RRF + Reranker + MMR
  - 返回证据包、冲突和可验证 citation
  - 按任务类型路由图谱，保留延迟/Token 预算
  - _Requirements: R4, R5, R9_

- [ ] 10. 实现标准 Feedback Event
  - 接入采纳/驳回/编辑 diff、测试结果、缺陷状态、合并/回退
  - 强制任务—输出—轨迹—版本关联，去重并防刷
  - _Requirements: R6, R9_

- [ ] 11. 实现评测集和回放引擎
  - 实现 Gold/Regression/Fresh/Challenge 分层、去泄漏 split
  - 计算 L0–L3 指标、切片指标、显著性和成本
  - _Requirements: R7, R10_

- [ ] 12. 实现反馈与测评界面
  - 检索轨迹、编辑 diff、反馈归因、失败样本和版本对比
  - 支持从评测失败一键生成待复核候选
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
_

## Phase 1：知识资产化与文档图谱

- [ ] 4. 创建 `knowledge_evolution` 边界和核心数据模型
  - 实现 SourceSnapshot、KnowledgeAsset/Version/Evidence、Candidate、Conflict、IndexProjection
  - 实现状态机、幂等键、ACL 和审计字段
  - _Requirements: R1, R2, R3, R9_

- [ ] 5. 升级文档处理管线
  - 从纯 token 分块升级为标题/段落/表格/代码块优先的结构分块
  - 保留页码、章节、解析器版本和原文定位
  - _Requirements: R1, R2_

- [ ] 6. 实现知识提炼、去重、冲突和分级
  - 规则与 LLM 分层提取概念、关系和知识原子
  - L1 候选必须具备权威证据和双人审批
  - _Requirements: R2, R3_

- [ ] 7. 实现版本化 Qdrant 投影
  - collection + alias 双缓冲切换，payload 带完整版本与 ACL
  - Outbox + Celery 幂等写入，支持对账、重建和回滚
  - _Requirements: R3, R4, R8, R9_

- [ ] 8. 接入文档图谱
  - 实现 KnowledgeDocumentGraphAdapter 和中性 GraphSource API
  - 展示 Document/Section/Concept/Rule/Evidence 节点和语义关系
  - 不改写 CRG 代码图谱数据库
  - _Requirements: R1, R4, R9_

## Phase 2：融合检索、反馈和测评

- [ ] 9. 拆分可版本化检索编排器
  - 并行 Dense/Sparse/Structured/Graph，weighted RRF + Reranker + MMR
  - 返回证据包、冲突和可验证 citation
  - 按任务类型路由图谱，保留延迟/Token 预算
  - _Requirements: R4, R5, R9_

- [ ] 10. 实现标准 Feedback Event
  - 接入采纳/驳回/编辑 diff、测试结果、缺陷状态、合并/回退
  - 强制任务—输出—轨迹—版本关联，去重并防刷
  - _Requirements: R6, R9_

- [ ] 11. 实现评测集和回放引擎
  - 实现 Gold/Regression/Fresh/Challenge 分层、去泄漏 split
  - 计算 L0–L3 指标、切片指标、显著性和成本
  - _Requirements: R7, R10_

- [ ] 12. 实现反馈与测评界面
  - 检索轨迹、编辑 diff、反馈归因、失败样本和版本对比
  - 支持从评测失败一键生成待复核候选
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
