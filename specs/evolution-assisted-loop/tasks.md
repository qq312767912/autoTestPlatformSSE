# 全链路自进化闭环补齐 — 任务

> 需求 `requirements.md` / 设计 `design.md`。状态：`待办` / `进行中` / `完成`。

## 台账

| 任务 | 工作面 | 需求 | 优先级 | 状态 |
| --- | --- | --- | --- | --- |
| T00 未提交改动验收与归档 | — | — | P0 | 待办 |
| T01 抽公共采纳率解析件 + 门槛真值上提 | A | R3 | P0 | 待办 |
| T02 图谱通道接线 + channels 记录实况 | C | R5① ② | P0 | 待办 |
| T03 阶段报告下载端点 + 产物登记约定 | A | R1 | P0 | 待办 |
| T04 阶段反馈上传端点 | A | R2 / R3 | P0 | 待办 |
| T05 全链路阶段卡片两个按钮 | A | R1 / R2 | P0 | 待办 |
| T06 AI 候选优化点生成端点 | B | R4 | P1 | 待办 |
| T07 人工确认候选端点 | B | R4 | P1 | 待办 |
| T08 进化工坊三步向导 | B | R4 | P1 | 待办 |
| T09 血缘 `sources` 段 + citations 规范化 | C | R5③ ④ | P1 | 待办 |
| T10 溯源前端展示 | C | R5 | P1 | 待办 |
| T11 图谱节点拖拽 | D | R6 | P2 | 待办 |

---

## T00 未提交改动验收与归档（前置）

**为什么必须第一个做**：工作区现有 12 个未提交文件（用例审查报告反馈闭环 + Agent 大盘增强，+572/−146），它们已实现 R3 的大部分与 R2 的单能力形态。在未验收的改动上继续叠加，会把回滚边界弄没。

**范围**：`WHartTest_Django/{knowledge_evolution,testcases}/*` + `WHartTest_Vue/src/features/{knowledge-evolution,testcase-review,skills}/*`

**验收**：
- 后端：`knowledge_evolution.tests_t23_case_review_evolution` 全绿；`testcases` 全绿；`makemigrations --check` → No changes detected。
- 前端：`/knowledge-evolution?view=data` 走一遍用例审查三步向导，上传含末页「采纳率」的报告 → 采纳率被正确读出；低于 70% 被拒并说明原因。
- 浏览器 `pageerror` + `console.error` = 0。
- ⚠️ 提交前按既有纪律过三道闸门，并**先看清 working tree 是否混有其它会话的改动**（本机常并行）。

---

## T01 抽公共采纳率解析件 + 门槛真值上提

**目标**：把只服务用例审查的解析逻辑提成四阶段共用的公共件，门槛值收敛为单点真值。

**落点**：
- 新建 `knowledge_evolution/report_parsing.py`：`read_acceptance_rate(sheet) -> float | None`（按标签定位，支持 `0.85` / `85` / `"85%"`；公式无缓存值返回 `None`）。
- `case_review_evolution.py`：`_acceptance_value` 改为调用公共件（行为不变）；`DEFAULT_HUMAN_SCORE_THRESHOLD` 上提为公共常量并改引用。
- `report_gates.py` 已有的 `DEFAULT_THRESHOLD = 0.7` 与门槛常量对齐（改名或加注释说明二者关系，避免第三个值冒出来）。

**验收**：用例审查既有 33 项测试全绿（**行为零变化**）；新增公共件单测覆盖三种数值写法 + 公式无缓存 + 缺标签报错。

---

## T02 图谱通道接线 + channels 记录实况

**目标**：让四阶段生成真正走图谱召回，并如实记录通道执行情况。

**落点**：
- `retrieval.py`：四阶段 `task_type` 加入 `graph_task_types`；`graph_policy` 保持 `conditional`。
- `services.py:175`：`channels.graph` 不再硬编码 `{enabled: False, reason: "phase_0_observability"}`，改为透传编排器实际结果。
- `orchestrator_integration/agent_loop_view.py`：提交侧把检索实况（per_source）写入 `channels`。

**验收**：
- 发起一条四阶段流程并提交一阶段产出，`RetrievalTrace.channels.graph.enabled` 与 `hits` 反映真实执行结果。
- 直连检索已有高分时图谱通道应被跳过（`conditional` 生效），且 `channels` 里能看到"因什么被跳过"。
- 回归：`knowledge_evolution` 检索相关测试全绿。

---

## T03 阶段报告下载端点 + 产物登记约定

**目标**：每个已完成阶段都能下载该阶段报告。

**落点**：
- 产出约定：`GenerationOutput.metadata.artifacts[]`（`{key,name,path,sha256,size}`）。
- 新端点 `GET /api/knowledge-evolution/operations/workflow-stage-artifact/`：优先下发登记产物，缺失则按 `content` 渲染统一模板回落。
- 文件存在性校验复用 T23 `testcases/serializers.py::_url` 的思路（不存在就不给按钮）。

**验收**：有登记产物时下载得到该文件且 sha256 一致；无登记产物时得到模板文件（非 500）；未产出阶段接口返回明确"无产出"而非空文件。

---

## T04 阶段反馈上传端点

**目标**：上传已完成阶段的已确认报告，记录采纳率，不派生。

**落点**：新端点 `POST .../operations/workflow-stage-feedback/`（multipart：`project` / `workflow_id` / `stage` / `file`）。
- 定位产出复用 `WorkflowGateService` 既有 `workflow-stage-output` 逻辑；
- 采纳率走 T01 公共件；
- 落 `FeedbackEvent`（`signal="accepted"`、`value=采纳率`、`reason_code="report_acceptance_rate"`、绑 `output` + `skill_version`、`idempotency_key` 含报告 sha256 去重）；
- 低于门槛返回 400 并点名门槛与实测值。

**验收**：同一份报告重复上传不产生重复 `FeedbackEvent`；低于门槛被拒；`GenerationOutput` / `SkillVersion` 未被改动（反馈不派生）。

---

## T05 全链路阶段卡片两个按钮

**落点**：`AIQualityEvolutionView.vue` `footer.wf-card-actions`。
- 「下载报告」「上传反馈」仅在 `step.output_id` 存在时渲染（**不显示优于禁用**）。
- 上传后就地回显 `采纳率 x% · 门槛 70%`。

**验收**：阶段未产出时两按钮不在 DOM；产出后可下载、可上传；上传成功页面出现采纳率；`errors=0`。

---

## T06 AI 候选优化点生成端点

**落点**：
- `attribution.py`：`LLMAssistedAttributionService` 增报告对比入口（输入 = 报告解析结果 + 当前 Skill 包正文 + 基线归因），产出 `FailureAttribution(source="llm", state="proposed", confidence≤0.8)`，逐条带证据与反证。
- 新端点 `POST .../case-review-evolution/attributions/`：生成候选，返回逐条 `{category, issue_type, hypothesis, evidence, counterevidence, confidence}`。
- **基线口径**：采用 `design.md §2.5` 方案 c（不依赖历史第二份报告）。

**验收**：无激活 LLM 时返回可识别的降级信号（非 500）；有 LLM 时产出 ≥1 条 `proposed` 归因；`source` 必须为 `"llm"` 且 `confidence < 1.0`。

---

## T07 人工确认候选端点

**落点**：`POST .../case-review-evolution/attributions/confirm/`，入参 `[{idx, action: accept|edit|reject, hypothesis?, category?}]`。
- `accept`/`edit` → `state="confirmed"` + 记 `confirmed_by`/`confirmed_at`，**保留原置信度**；
- `reject` → 丢弃或 `rejected`；
- `evolve` 只读 `confirmed` 归因（`assert_derivation_allowed` 已强制）。

**验收**：未确认任何候选时 `evolve` 被拒并说明"没有已确认的归因"；确认 2 条后派生成功，且派生输入不含被驳回项；落库的归因 **不出现 `confidence=1.0` 的 AI 假设**。

---

## T08 进化工坊三步向导

**落点**：`AIQualityEvolutionView.vue` 用例审查向导 2/3 → 2/4、3/4、4/4。
- 新增第 3 步：候选优化点列表 + 逐条「采纳 / 改写 / 驳回」；
- 无 LLM 时明确显示"未配置 LLM，已降级为人工标注"。

**验收**：走完整流程可派生候选版本并下载；降级路径可用；`errors=0`。

---

## T09 血缘 `sources` 段 + citations 规范化

**落点**：
- `lineage.py` `OutputLineageService.trace()` 增 `sources`（`design.md §3.3` schema），**与现有闭环阶段并列**；
- `citations` 统一为 `{citation_id, source_type, source_id, node_id?, document_id?, chunk_index?, rank, title?}`，四阶段与知识问答同构。

**验收**：对一条带图谱引用的产出调血缘接口，`sources.graph_nodes` 非空且能与图谱节点 id 对上；无引用时返回空数组而非缺字段。

---

## T10 溯源前端展示

**落点**：血缘 / 产出详情面板增「内容来源」区：通道实况 + 引用条目（可跳图谱节点 / 文档 / 需求 / 用例）+ Skill 包摘要。

**验收**：引用条目可点击跳转对应节点（复用 `KnowledgeGraphView` 的 `expandNode` 语义）；`errors=0`。

---

## T11 图谱节点拖拽

**落点**：`knowledge-graph/KnowledgeGraphView.vue`（见 `design.md §4`）。
- `nodeOverrides` 覆盖层 + `position()` 优先读覆盖；
- 节点 `@mousedown.stop`（**必须 `.stop`**，否则拖节点会连带平移画布）；
- client → viewBox → 反解 pan/zoom；
- 位移 <3px 视为 click；`resetViewport()` 与「重排」按钮清覆盖。

**验收**：
- 拖动节点后位置保持，刷新布局可复位；
- 拖动节点时画布 `pan` 不变（反向对照：去掉 `.stop` 应复现"画布跟着跑"）；
- 缩放到 150% / 缩到 60% 后拖拽落点仍准确；
- 筛选/展开邻域导致节点集合变化时，已手工摆放的节点位置不被重算冲掉。

---

## 交付顺序建议

**第一轮（P0，闭环能跑通的最小面）**：T00 → T01 → T02 → T03 → T04 → T05

**第二轮（P1，省人力 + 可溯源）**：T06 → T07 → T08 → T09 → T10

**第三轮（P2）**：T11
