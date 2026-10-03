# 全链路自进化闭环补齐 — 设计

> 需求见 `requirements.md`。本文件回答"怎么落"，每条都给到文件与接口级落点。

## 0. 主线

五个断面是同一条管道上的五段：

```
生成（四阶段） ──[R5 要接图谱，产出带引用]──▶ 产出落库
      │                                        │
      │                                   [R1 下载报告]
      ▼                                        ▼
  人工在报告内判定 ──────────────────▶ 上传已确认报告
                                               │
                                    [R3 采纳率门槛 70%]
                                               ▼
                                    [R4 AI 对比生成候选优化点]
                                               ▼
                                       人工逐条确认
                                               ▼
                                     派生 Skill 候选 → 下载
                                    （R2 反馈只记不派生）
                    [R6 图谱节点拖拽 —— 独立体验项，不在这条管道上]
```

R1/R2 是**入口**，R3 是**闸门**，R4 是**省人力的那一步**，R5 是**让产出可信**，R6 独立。

---

## 1. 工作面 A：阶段报告出口与反馈入口（R1 / R2 / R3）

### 1.1 报告文件从哪来（核心决策）

`GenerationOutput` 只有 `content: TextField`，**没有文件字段**。方案二选一：

| 方案 | 做法 | 取舍 |
| --- | --- | --- |
| ① skill 登记产物 | 产出侧写 `GenerationOutput.metadata.artifacts[]`，平台只发下载链接 | **首选**。与 skill 实际输出一致，能表达专业结构 |
| ② 平台模板渲染 | 按 `content` 用统一模板现场生成 xlsx | **兜底**。只能表达平台认识的字段，丢了 skill 的专业结构 |

**决策：① 优先、② 回落。** 判据与 T23 新增的 `_url` 存在性校验同源 —— 文件不存在就不给出按钮，而不是给了再报错。

`metadata.artifacts` 约定：

```json
[{ "key": "report", "name": "回归-20261003-方案.xlsx",
   "path": "media/skill_runtime/artifacts/...", "sha256": "...", "size": 15360 }]
```

### 1.2 下载端点

`GET /api/knowledge-evolution/operations/workflow-stage-artifact/?project=&workflow_id=&stage=&key=report`

- 定位产出：复用 `WorkflowGateService` 既有的 `workflow-stage-output` 定位逻辑，不新写一套。
- 有登记产物 → 直接下发；无 → 按 `content` 渲染统一模板。
- 响应复用 T23 的 `content-disposition: attachment; filename="..."` 形态。

### 1.3 上传反馈端点

`POST /api/knowledge-evolution/operations/workflow-stage-feedback/`
（multipart：`project` / `workflow_id` / `stage` / `file`）

流程：定位产出 → 解析采纳率 → 门槛校验 → 落 `FeedbackEvent` → **返回、不派生**。

- `FeedbackEvent`：`signal="accepted"`、`value=采纳率(0-1)`、`reason_code="report_acceptance_rate"`、绑定 `output` + `skill_version`、`idempotency_key` 含报告 sha256（复用 T23 的去重手法）。
- **与派生解耦**：反馈是"质量记录"，派生是"改包动作"。合并会让"只想记录反馈"被迫改包。

### 1.4 解析器怎么复用（关键约束）

四阶段报告结构**互不相同**（方案/用例/执行/报告各有格式），`CaseReviewReportParser` 整体不可复用。

可复用的只有一条约定：**报告最后一个 Sheet 内，用标签定位「采纳率」单元格，不绑坐标。**

```python
# 从 case_review_evolution.py 抽出为公共件（新建 report_parsing.py）
def read_acceptance_rate(sheet) -> float | None:
    """按标签定位「采纳率」，支持 0.85 / 85 / "85%" 三种写法；公式无缓存值返回 None。"""
```

- 两处共用：`CaseReviewReportParser` 与四阶段反馈端点。
- **对 skill 侧的契约**：四阶段的报告生成 Skill 必须在报告末页输出「采纳率」标签单元格。这是 R1/R2 能成立的前提，属于阶段 Skill 的产出规范。
- 未命中标签 → 400 并点名"报告末页缺少「采纳率」，请上传平台导出并完成人工确认的报告"（沿用 T23 文案口径）。

### 1.5 前端落点

`AIQualityEvolutionView.vue` 阶段卡片 `footer.wf-card-actions` 增两个按钮：

| 按钮 | 显示条件 |
| --- | --- |
| 下载报告 | `step.output_id` 存在（未产出不显示，不是禁用） |
| 上传反馈 | 同上；上传后回显采纳率与门槛判定 |

上传成功后就地显示 `采纳率 x% · 门槛 70%`，不弹 toast 了事。

### 1.6 门槛真值

- 沿用 `DEFAULT_HUMAN_SCORE_THRESHOLD = 70`，**上提到公共件**成为唯一真值，`case_review_evolution` 改为引用它。
- 预检与提交必须调同一判据函数（现状已是，需保持）。
- 口径 A/B 歧义见 `requirements.md` R3；默认按 A 实现。

---

## 2. 工作面 B：AI 生成候选优化点 + 人工确认（R4）

### 2.1 现路径的问题

```
上传报告 → _build_attributions() → source="human", state="confirmed", confidence=1.0 → 直接派生
```

人工在 xlsx 里写什么就是什么，`confidence=1.0` 是**伪装的确定性** —— 它把"人随手写的一句话"记成了"已确证结论"。

### 2.2 目标路径（两步 → 三步）

```
① 选审查项目
② 上传已确认报告（R3 门槛）
③ AI 生成候选优化点 ──▶ 人工逐条「采纳 / 改写 / 驳回」   ← 新增
④ 确认 Skill 版本 → 派生
```

### 2.3 新增端点

| 端点 | 作用 |
| --- | --- |
| `POST .../case-review-evolution/attributions/` | 生成候选优化点（**不落库或落 `proposed`**），返回逐条候选 |
| `POST .../case-review-evolution/attributions/confirm/` | 入参 `[{idx, action: accept\|edit\|reject, hypothesis?, category?}]`；`accept/edit` 落 `confirmed` 并记 `confirmed_by`，`reject` 丢弃 |

`evolve` 改为**只读 `state="confirmed"` 的归因**（现有 `assert_derivation_allowed` 已要求这一点，顺序天然强制）。

### 2.4 归因生成器

复用已有能力，不改归因引擎本身：

- 落点：给 `LLMAssistedAttributionService`（`attribution.py:347`）增加报告对比入口 `run_for_scan(output, scan, baseline)`；或新建 `ReportDiffAttributor` 委托它。
- 输入：本轮报告解析结果（缺陷分组）+ 当前 Skill 包正文 + 基线归因（若有）。
- 输出：`FailureAttribution(source="llm", state="proposed", confidence≤0.8)`，**逐条带证据与反证**（沿用既有 prompt 契约："先寻找反证，再提出最多 3 条可验证假设"）。
- 该服务已明确声明"LLM 只生成待确认假设，不覆盖确定性归因、不自动进入优化"——**本期正是要遵守它，而不是绕过它**。

### 2.5 "前后报告对比"的基线定义（决策点）

| 方案 | 基线 | 可用性 |
| --- | --- | --- |
| a | 同一 review 的上一份报告 | 多数场景**不存在**（一个审查通常只有一份报告） |
| b | 当前 Skill 活跃版本上次进化所用的报告 | 依赖历史留痕，不一定有 |
| **c** | **不做报告两两 diff，改为 LLM 读「当前 Skill 包 + 本轮缺陷 + 历史归因」提优化点** | **最小可用**，不依赖历史报告 |

**决策：c 为最小可用，a/b 作为增强。** 理由：用户要的实质是"AI 提出可优化点"，"对比"是手段不是目的；在没有稳定第二份报告时强行 diff 会引入噪声。

### 2.6 归因来源诚实性（不变量）

| 项 | 现状 | 改后 |
| --- | --- | --- |
| `source` | `"human"` | `"llm"`（AI 生成）/ `"human"`（人工直接写，降级路径） |
| `confidence` | 恒 `1.0` | AI 候选 ≤0.8；人工确认后仍保留原置信度，另记 `confirmed_by` |
| `state` | 直接 `confirmed` | `proposed` → 人工确认 → `confirmed` |

**禁止**把 AI 假设记成 `confidence=1.0`。

### 2.7 降级

无激活 LLM 配置（`LLMConfig.objects.filter(is_active=True)` 为空）时：

- 跳过步骤 ③，回落到现有"人工标注 → 直接落 confirmed"路径；
- 界面**明示**"未配置 LLM，已降级为人工标注"，不静默降级。

---

## 3. 工作面 C：图谱接线与内容溯源（R5）

### 3.1 断点 ① — 让图谱通道真正参与召回

`retrieval.py` 已具备全部机制：`GraphRetriever` + 5 路召回 + `_should_use_graph(request, config, direct_scores)`（支持 `graph_policy: always|conditional|never` 与 `graph_task_types` 白名单）。

改法：

- 把四阶段 `task_type` 加入 `graph_task_types`；
- `graph_policy` 保持 `conditional`（直连检索已有高分时跳过图谱，省 token）；
- **不做**：不改成 `always`（会无谓放大上下文与成本）。

### 3.2 断点 ② — 记录实况而非硬编码

`services.py:175` 的 `channels.graph = {"enabled": False, "reason": "phase_0_observability"}` 必须改为记录**实际路由结果**：

- 检索编排器返回 per_source 执行情况（哪个通道跑了、命中几条）；
- `record_task_output` 的 `channels` 由调用方传入（签名已支持），落点在各阶段提交侧；
- 注意：四阶段不走 `record_knowledge_query`，走 `record_task_output` + `orchestrator_integration/agent_loop_view.py` 提交，**改动在提交侧**。

### 3.3 断点 ③ — 血缘补内容来源

`OutputLineageService.trace()`（`lineage.py:95-160`）增 `sources` 段，**与现有闭环阶段并列，不替代**：

```json
"sources": {
  "channels":  { "dense": {...}, "graph": {"enabled": true, "hits": 6} },
  "citations": [ { "citation_id": "...", "source_type": "graph|document|requirement|test_case",
                   "source_id": "...", "node_id": "...", "chunk_index": 3, "rank": 1, "title": "..." } ],
  "graph_nodes":   [ { "node_id": "...", "kind": "requirement_module", "label": "..." } ],
  "skill_content": { "skill_version_id": "...", "package_sha256": "...", "files": [ { "path": "SKILL.md", "sha256": "..." } ] }
}
```

- `citations` 规范化：统一 `{citation_id, source_type, source_id, node_id?, document_id?, chunk_index?, rank, title?}`，让四阶段与知识问答同构（现状知识问答是 `kb:{kb}:{doc}:{chunk}` 形态）。
- `skill_content` 回答"参考了这版 Skill 的哪些内容"——用包内文件清单 + 哈希，**不落文件正文**（包不可变，哈希即可定位）。

### 3.4 前端

血缘/产出详情面板增「内容来源」区：通道实况 + 引用条目（可跳转对应图谱节点 / 文档 / 需求 / 用例）+ Skill 包摘要。现有 `KnowledgeGraphView` 已支持按节点 `expandNode`，引用条目可直接带节点 id 跳转。

---

## 4. 工作面 D：图谱节点拖拽（R6）

### 4.1 现状约束

`KnowledgeGraphView.vue` 节点位置来自 `positions` computed（按 `kind` 分组的环形布局），**每次重算**，没有可写位置层。整图 `@mousedown="startPan"` 绑在 `<svg>` 上。

### 4.2 改法

```ts
const nodeOverrides = ref(new Map<string, {x:number;y:number}>());
function position(id: string) {
  return nodeOverrides.value.get(id) ?? layoutPosition(id);  // 覆盖优先
}
```

节点事件：`@mousedown.stop="startNodeDrag(node, $event)"`。

⚠️ **`.stop` 是必需的** —— 不阻止冒泡就会同时触发 `<svg>` 的 `startPan`，出现"拖节点时画布跟着跑"。

坐标换算（client → viewBox → 反解 pan/zoom）：

```ts
const rect = svgRef.value!.getBoundingClientRect();
const vbX = (event.clientX - rect.left) * (1000 / rect.width);
const vbY = (event.clientY - rect.top)  * (680  / rect.height);
const x = (vbX - pan.value.x) / zoom.value;
const y = (vbY - pan.value.y) / zoom.value;
```

- 拖拽中直接写 `nodeOverrides`（响应式，即时可见）；
- **防误触**：位移 < 3px 视为 click，保留选中语义；
- 复位：`resetViewport()` 清空 override，另给一个显式「重排」按钮；
- 节点集合变化（筛选/展开邻域）时**保留**已有 override（用户手工位置不该被布局重算冲掉）；失联节点的 override 惰性清理。

---

## 5. 优先级与依赖

| 序 | 工作面 | 依赖 | 为什么这个序 |
| --- | --- | --- | --- |
| **P0-1** | C-①② 图谱通道接线 + 记录不写死 | 无 | 配置级改动，直接兑现"接入知识图谱"；先做可立刻见效 |
| **P0-2** | A 阶段报告出口 + 反馈入口 | 抽公共解析件 | 没有入口，闭环走不起来；后端能力几乎已就绪 |
| **P1-1** | B AI 归因 + 人工确认 | 需激活 LLM | 省人力最多，但提示词需调优 |
| **P1-2** | C-③④ 血缘展示 + citations 规范化 | C-① | 溯源可见 |
| **P2** | D 节点拖拽 | 无 | 独立体验项，可随时插入 |

建议 P0 两项一轮交付 —— 它们共同构成"闭环能跑通"的最小面。

---

## 6. 风险与不变量

1. **版本包不可变**：派生只写新版本目录，绝不触碰基线包；判据用 `skills.models._is_version_package_path`（纯结构判定，**不要**用"有版本记录"一刀切）。
2. **口径单点真值**：门槛值、放行状态集、阶段序列各只一处。放行判定只读 `GATE_PASSING_STATES`。
3. **归因来源不得伪装**：AI 假设不得记成 `confidence=1.0` 的人工结论。
4. **无 LLM 可降级**：不得因缺 LLM 阻断人工路径。
5. **生效方式**：`skills` / `testcases` 未挂载进容器 → 改动必须 `docker cp`；`knowledge_evolution` 是 `:ro` 挂载，改宿主即生效但需 `docker restart wharttest-new-backend`（约 75–85s 才 healthy）。
6. **未提交改动的处置**：本设计假定工作区 12 个未提交文件（用例审查反馈闭环）**先验收后再叠加**，避免在未验证的改动上继续盖楼。
