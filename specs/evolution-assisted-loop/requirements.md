# 全链路自进化闭环补齐需求

> 代号 `evolution-assisted-loop`。诊断时间 2026-10-03，全部结论来自本地工作区代码实测（含 12 个未提交文件）。

## 目标

「用例审查」单能力已跑通一条自进化链路：平台导出审查报告 → 人工在报告内填判定 → 上传 → 采纳率门槛校验 → 派生 Skill 候选 → 下载候选包。

本期把这条链路推广到「全链路测试」四阶段，并把链路中最耗人力的**人工逐条标注**换成 **AI 先对比生成候选优化点、人工只做确认**；同时让每次生成可**溯源到所参考的需求点、用例与知识内容**。

一条主线，五个断面。不是五个独立需求。

## 现状诊断

| # | 用户诉求 | 代码事实 | 判定 |
| --- | --- | --- | --- |
| D1 | 全链路测试的下载报告与上传反馈入口 | `AIQualityEvolutionView.vue:294-307` 阶段卡片 footer 仅 6 个动作：查看结果 / 执行本阶段 / 运行门禁测评 / 人工评分 / 确认进入下一阶段 / 负责人放行。`openStageOutput()` 只读 `GenerationOutput.content` 正文与门禁证据，**不出文件**；无任何上传入口 | **缺** |
| D2 | 进化工坊应由 AI 对比前后报告生成优化点、人工确认 | `case_review_evolution.py:652-690` `_build_attributions()` 把人工在 xlsx 里填的"修改点"**直接**落成 `FailureAttribution(source="human", state="confirmed", confidence=1.0)`，`confirmed_by=actor`，不经任何 AI 环节 | **缺**（但 AI 能力已存在，见下） |
| D3 | 测试链路生成接入知识图谱，溯源到需求点/用例所参考的文件与 skill 内容 | 图谱侧、检索侧**均已完备**；断点全在"接线"：① 图谱通道被路由策略摘掉；② `channels.graph` 写死关闭；③ 血缘服务不返回引用。详见 §D3 三处断点 | **缺**（接线问题，非能力缺失） |
| D4 | 报告采纳率超过 70% 才可上传 | 已实现于未提交改动：`DEFAULT_HUMAN_SCORE_THRESHOLD = 70`、`CaseReviewReportParser._acceptance_value()` 按标签定位报告末页采纳率、`evolve`/`preflight` 双重拦截、新增 `feedback` 端点只记反馈不派生 | **已实现（未提交）** |
| D5 | 知识库节点无法拖拽 | `knowledge-graph/KnowledgeGraphView.vue` 为纯手写 SVG；节点坐标由 `positions` computed 按 `kind` 环形布局算出，**无位置覆盖层**；整图只绑 `pan`（画布平移），节点仅 `@click` | **缺** |

### D3 三处断点（本期最有价值的判断）

平台**不缺**图谱能力，缺的是把已建好的通道接到生成链路上：

| 断点 | 位置 | 事实 |
| --- | --- | --- |
| ① 通道被摘 | `retrieval.py` `_should_use_graph()` + `_collect_candidates()` | `GraphRetriever`（`name="graph"`）已实现 `search_nodes` + `subgraph` 扩展；`RetrievalOrchestrator` 已注册 5 路召回（dense/sparse/graph/structured/historical）+ 加权 RRF + MMR；但 `graph_policy` 默认 `conditional` 且 `graph_task_types` 未含四阶段任务类型 → 判定不通过 → `per_source.pop("graph")` 直接摘掉通道 |
| ② 记录写死 | `services.py:175` | `channels.graph = {"enabled": False, "reason": "phase_0_observability"}` 为硬编码，与实际路由结果无关 |
| ③ 血缘不含引用 | `lineage.py:95-160` `OutputLineageService.trace()` | 返回 output / skill / bindings / feedback / gold / attribution / proposal / experiment / release / stages，**完全不读 `trace.citations` 与 `channels`**，不展示任何内容来源 |

图谱侧已就绪的证据（`code_analysis/views.py:415-430`）：4 类数据源均已接通 —— `code_repository`（CRG）、`knowledge_document`（`KnowledgeDocumentGraphClient`）、`requirement`（`RequirementGraphClient`）、`test_case`（`TestCaseGraphClient`），且节点类型含 `requirement_document` / `requirement_module` / `test_module` / `test_case` / `test_step`。

### D2 已有但未被使用的 AI 能力（本期可直接复用）

| 能力 | 位置 | 输入 → 输出 |
| --- | --- | --- |
| `LLMAssistedAttributionService.run()` | `attribution.py:347-411` | `output`（读 trace.spans + feedback + 规则归因）→ ≤3 条 `FailureAttribution(source="llm", state="proposed")`。设计上已声明"LLM 只生成待确认假设，不能覆盖确定性归因或自动进入优化" |
| `OptimizationProposalService.generate()` | `optimization.py:56-121` | 归因 → 优化提案（四类） |
| `PromptOptimizer.generate()` | `optimization.py:122-160` | 提案 + 基线 Prompt → 增量候选 Prompt（强制 `append_only`，原 Prompt 必须全文保留） |
| `SkillCandidateDeriver.build_patch()` | `skill_evolution.py:78-134` | `skill_version` + `attributions` → 补丁 |
| `assert_derivation_allowed()` | `skill_evolution.py:408` | 派生护栏（要求归因已确认） |

结论：D2 的落点是**替换输入形态 + 插入人工确认步**，不是新建归因引擎。

## 验收需求

### R1. 全链路测试阶段报告出口

- 每个已完成阶段应能**下载该阶段的报告文件**；未产出阶段不显示该按钮，而非显示后报错。
- 报告文件优先取该阶段 Skill 产出的登记产物（防伪造、与 skill 实际输出一致）；无登记时回落到平台按 `GenerationOutput.content` 生成的统一模板文件。
- 下载入口必须在阶段卡片内常驻，不依赖 toast 或弹窗。

### R2. 全链路测试阶段反馈入口

- 每个已完成阶段应能**上传该阶段的已确认报告**（平台导出格式）。
- 上传后平台应以报告中的人机一致口径（采纳率）作为该阶段的质量分数，落 `FeedbackEvent` 并绑定 `output` 与 `skill_version`。
- 上传反馈**只记录、不派生** Skill 版本；派生是独立动作，两者不合并。
- 采纳率未达门槛时，上传应被拒绝并说明卡在哪一条。

### R3. 采纳率门槛口径

- 门槛默认 70%，可由测试负责人按流程覆盖。
- 采纳率必须从报告内容解析，**不接受页面手工输入**（手工输入可被随意填写，等于没有门槛）。
- 门槛判定必须是单点真值，预检与提交走同一判据。
- ⚠️ **待裁决口径歧义**（用户原话"不需要确认的报告采纳率超过70%才可以上传文件"存在两种读法）：
  - **口径 A**：采纳率 ≥ 门槛才允许用该报告驱动进化（= 现状实现）。
  - **口径 B**：采纳率 ≥ 门槛的报告**免人工确认**直接可用；低于门槛才转入人工确认。
  - 本期默认按 A 实现，B 作为可选放宽项，待确认后再改。

### R4. AI 生成候选优化点、人工确认

- 上传报告通过门槛后，系统应基于**前后报告对比**（本轮 vs 基线）与当前 Skill 包内容，由 LLM 生成候选优化点。
- 候选优化点必须是**待确认**状态，不得直接进入派生。
- 人工应能对每条候选做采纳 / 修改 / 驳回，只有被采纳的才进入派生输入。
- 归因来源必须诚实标注：AI 生成、人工确认、以及确认人，不得把 AI 假设记成 `confidence=1.0` 的人工结论。
- 无激活 LLM 配置时，流程应可降级为纯人工标注，并在界面明示降级原因。

### R5. 生成链路内容溯源

- 测试链路各阶段生成时，图谱检索通道应真正参与召回，而非被路由策略静默摘除。
- `channels` 应记录通道的**实际执行结果**（启用/停用及原因），不得硬编码。
- 产出应能回答"这条产出参考了哪些需求点、用例、文档片段、以及哪一版 Skill 的哪些内容"。
- 血缘查询应展示内容来源，与现有闭环血缘（产出→反馈→金标→归因→提案→发布）并列而非替代。

### R6. 知识图谱节点拖拽

- 节点应可拖拽到任意位置，拖拽后位置保持，刷新布局可复位。
- 拖拽节点不得与画布平移冲突（节点拖动时画布不动）。
- 缩放与平移后拖拽落点仍准确。
- 节点数变化时，已有手动位置不应被布局重算覆盖。

## 非目标

- 不重写图谱渲染引擎（不引入 d3 / cytoscape / G6）；在现有 SVG 上扩展。
- 不做 `Skill` 行的物理合并。
- 不把 `code_review`、`knowledge_query` 纳入 Skill 型自进化 Agent。
- 不在本期实现"打回上一阶段"动作（平台无此语义）。
- 不改四阶段主链路定义：方案生成 → 用例生成 → 测试执行 → 报告产出。
