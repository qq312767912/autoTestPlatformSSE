# Multi-Agent 四阶段流程与界面展示（设计稿）

> 状态：**设计已成型，实现未开始**。口径依据见同目录 `requirements.md`；待拍问题见 `open-questions.md`。
> 本文件回答两件事：**流程怎么串**、**界面怎么展示**。评分体系按用户指示**暂挂**，只留接口位。

---

## 0. 一句话结论

后端的 multi-agent 编排**已经齐备**（6 个端点 + 一次性版本锁定 + 三级阻塞判定），
流程"串不起来"的原因全部在**前端与契约层**：

| # | 根因 | 证据 |
| --- | --- | --- |
| ① | 前端只接了 6 个端点中的 **2 个**，没有"发起流程"的 UI，`workflow_id` 在界面上**无处产生** | `service.ts` 仅导出 `evaluateWorkflowStage` / `overrideWorkflowStage` |
| ② | 四阶段流程只是 7 个平铺面板中的**一个 tab**，没有"流程列表 → 流程详情"的层级 | `AIQualityEvolutionView.vue` 的 `workspace` 单层枚举 |
| ③ | 面板之间**没有任何可点击的导航**——`.workspace-tabs` / `.primary-tabs` / `.stage-rail` **只有 CSS，模板里没渲染** | 模板第 7–15 行只有 hero 与 `quick-tabs`；`tabs` computed 定义了 7 项却零引用 |
| ④ | 两套流程读模型**字段名不一致**，`workflow-status` 作为"唯一真值入口"**无法被前端类型承接** | 见 §3 差异表 |
| ⑤ | `evaluate` 在"没有任何评测结果"时判 **`failed`**，直接**阻断链路**——与"评分先放一放"直接冲突 | `operations.py:206` |
| ⑥ | `SINGLE_STAGES` 仍是旧三件套，与"代码审查/知识库问答不算 Agent"冲突 | `operations.py:414` |

---

## 1. 流程主线：以 `workflow_id` 为聚合键的四阶段编排

### 1.1 身份定义

**一次端到端测试任务 = 一个 `workflow_id`**，它是这条链路上所有产出、门禁、版本锁的聚合键。

- `GenerationOutput.metadata.protocol.workflow_id` — 产出挂到流程
- `WorkflowStageGate.workflow_id` — 门禁挂到流程
- `WorkflowSkillLock.workflow_id` + `lock_key = "stage:<stage>"` — 版本锁定挂到流程+阶段

`workflow_id` **由发起方提供、非空必填**（`start_workflow` 会校验），界面必须能看见它——它是回看整条链路时唯一的检索词。

> 命名建议：**业务可读**而非 UUID，例如 `交易网关-回归-20261002-01`。
> 理由：它会出现在四个人工界面上（流程列表、详情头、联合图节点名、门禁留痕），UUID 无法被人脑核对。

### 1.2 六个端点的职责分工（已核）

| 端点 | 方法 | 谁调用 | 职责 | 改门禁状态？ |
| --- | --- | --- | --- | --- |
| `start-workflow` | POST | **测试负责人** | 一次性锁定四阶段 Skill 版本；返回 `locked_stages` / `unmanaged_stages` | 否 |
| `workflow-status` | GET | 任何项目成员 | 单条流程的**唯一真值入口**：四阶段门禁 + 锁定版本 + 契约 + `completed` / `blocked_at` | 否 |
| `evaluate-workflow-stage` | POST | 测试执行人员 | **单阶段门禁**：读该产出的 `EvaluationResult` → 算 `scores` → 置 `passed`/`failed` | **是** |
| `override-workflow-stage` | POST | **测试负责人** | 留痕放行 → `overridden`（必须填原因） | **是** |
| `evaluate-workflow` | POST | 测试执行人员 | 「阶段独立评测 + 四阶段端到端评测」，结论落 `gate.detail.evaluation` | 否 |
| `build-workflow-graph` | POST | 任何项目成员 | 按 `protocol.parent_output_ids` 建联合图（幂等 upsert） | 否 |

**关键分工，必须在设计里写死：**

- **列表**来自 `cockpit.workflows`（最多 20 条、倒序），**详情**来自 `workflow-status`。
  不能只用 `workflow-status` 列表——它**必须**有 `workflow_id` 参数，无法枚举。
- **`evaluate-workflow-stage` 与 `evaluate-workflow` 不是一件事**：
  前者改门禁（放行凭据），后者只产证据（不改门禁）。
  界面上必须是两个按钮、两种语义，不能合成一个"评测"。
- **`start_workflow` 与 `override` 只对测试负责人开放**（后端 `_ensure_test_lead`），
  执行人员看到的是禁用态 + 原因，而不是隐藏——隐藏会让人以为平台没有这个能力。

### 1.3 状态机

门禁状态（`WorkflowStageGate.status`）：

```
                 register_output（产出登记）
        ┌──────────────────────────────────────┐
        │                                      ▼
   未进入 ──► ready / blocked            pending ──evaluate──► passed ──► 放行
        （由上一阶段 gate 推导）              │                    ▲
                                              │                    │
                                              ├──evaluate──► failed ──► 阻断
                                              │                │
                                              │                └─override─► overridden ──► 放行
                                              └──无评分──► 【unscored】◄── §4 待拍
```

- **未进入阶段**的 `ready` / `blocked` 由 `workflow-status` 推导，判定条件 =
  上一阶段 gate ∈ `{passed, overridden}`，与 `assert_can_enter` **同源**。
- **已进入阶段**的 `entered = true`，`can_enter = true`，状态即 gate 状态。
- `assert_can_enter` 在**阶段入口**再校验一次，失败时抛的提示**必须能直接指向下一步动作**（已实现）：
  - 无产出 → "请先完成该阶段并提交门禁测评"
  - `failed` → "请修复后重跑该阶段，或由测试负责人留痕放行"
  - 未测评 → "上一阶段门禁尚未测评（当前状态：X）"

### 1.4 全链路时序

```
测试负责人                测试执行人员              编排层（workflow）            飞轮
    │                        │                          │                        │
    ├─① 发起流程 ────────────┼─────────────────────────►│                        │
    │  POST start-workflow   │                          ├ 一次性锁 4 阶段版本     │
    │  {project, workflow_id}│                          ├ 返回 locked/unmanaged  │
    │◄─ 201 + bindings ──────┼──────────────────────────┤                        │
    │  （unmanaged 阶段=无版本溯源，界面显式标灰）        │                        │
    │                        │                          │                        │
    │                        ├─② 阶段 1 产出 ──────────►│                        │
    │                        │  protocol.workflow_id    ├ register_output        │
    │                        │  protocol.stage          ├ → gate(pending)        │
    │                        │  parent_output_ids       ├ ensure_stage_lock      │
    │                        │                          │                        │
    │                        ├─③ 运行门禁 ─────────────►│                        │
    │                        │  evaluate-workflow-stage ├ 读 EvaluationResult    │
    │                        │◄─ passed / failed ───────┤ → scores → status      │
    │                        │                          │                        │
    │  ④ 仅当 failed 时       │                          │                        │
    │◄─ 负责人放行 ───────────┤                          │                        │
    │  override-workflow-stage│                         │                        │
    │                        │                          │                        │
    │                        ├─⑤ 进入阶段 2 ───────────►│                        │
    │                        │  assert_can_enter 校验    │                        │
    │                        │  （上一阶段 gate 通过才放行）                      │
    │                        │                          │                        │
    │                        │  …阶段 3、4 同上…         │                        │
    │                        │                          │                        │
    │                        ├─⑥ 端到端评测 ───────────►│                        │
    │                        │  evaluate-workflow       ├ → detail.evaluation    │
    │                        │                          │                        │
    │                        ├─⑦ 联合链路图 ───────────►│                        │
    │                        │  build-workflow-graph    ├ 方案→用例→执行→报告     │
    │                        │                          │                        │
    │                        │                          ├─⑧ 报告门禁 passed ────►│
    │                        │                          │  completed = true      │ 产出→反馈
    │                        │                          │                        │ →金标→评测
    │                        │                          │                        │ →归因→优化
    │                        │                          │                        │ →晋级/回滚
```

### 1.5 版本锁定为什么必须一次锁四个（已实现，界面要体现）

`start_workflow` 的 docstring 写得很清楚，两条理由都要在界面上有对应呈现：

1. **链路中途激活新版本不能改变锁** → 界面必须区分「**锁定版本**」与「**当前活跃版本**」两列，
   并注明"本流程实际使用锁定版本"。否则回滚无法界定影响范围。
2. **入口暴露"能力包没准备好"** → `unmanaged_stages` 必须在**发起结果页**立刻可见，
   而不是等跑到报告阶段才炸（那时前面阶段的算力与人工都白费了）。

`workflow-status` 刻意**不读"当前活跃版本"**（`operations.py:293`），这是设计意图不是缺陷，界面文案要跟上。

### 1.6 skill 包就绪度（现状，决定这条链路能不能真跑）

| Agent 阶段 | skill 包 | `CapabilityRelease` | 能否锁定 |
| --- | --- | --- | --- |
| `test_plan_generation` 测试方案 | `webtest-plan-generator` | **未发布** | 否 → `unmanaged` |
| `testcase_generation` 测试用例 | `webtest-case-generator` | `shadow` | 否（无 active） → `unmanaged` |
| `test_execution` 测试执行 | `webtest-execution-runner` | `shadow` | 否 → `unmanaged` |
| `report_generation` 报告生成 | `webtest-report-generator` | **未发布** | 否 → `unmanaged` |
| （旁支）`case_review` 用例审查 | `test-case-clarity-review` | `active` | **是** |

**结论：四阶段目前全部会落进 `unmanaged_stages`。**
`bind_stage` 的三分支策略里，`allow_unmanaged=True`（四阶段默认）会放行并标"使用平台默认行为（无版本溯源）"，
但**登记了却没有可用活跃版本时一律拒绝**——`shadow` 的两条属于"登记了但无 active"，会走拒绝分支。

> ⚠️ 这是当前最大的落地阻力：**四阶段链路现在能跑通界面与门禁，但锁不到任何版本**，
> 于是 §5 断链 ① ② 无法闭环。先把两个 `shadow` 包提升为 `active`、把两个未发布包发出去，
> 或先接受"四阶段无版本溯源"并在界面显式标灰，二选一。见 `open-questions.md`。

---

## 2. 界面信息架构

### 2.1 现状：三层需求被压成了一层

```
现状（单层平铺，无导航）
┌───────────────────────────────────────────────────────┐
│ Agent总览 │ 数据飞轮 │ 知识图谱      ← primaryView（仅靠左侧菜单切换）
├───────────────────────────────────────────────────────┤
│  六宫格 launch-console                                 │
│   ├ 独立能力评测 → workspace='single'                  │
│   ├ 全链路测试   → workspace='workflow'   ← 四阶段在这里
│   ├ 建设评测数据 → workspace='gold'                    │
│   ├ 运行自动评测 → workspace='evaluation'              │
│   ├ 分析失败轨迹 → workspace='attribution'             │
│   └ 优化与发布   → workspace='optimization'            │
│         ↑ 进去之后没有任何 tab 可切，只能点"返回控制台"  │
└───────────────────────────────────────────────────────┘
```

三层需求（态势 / 流程 / 飞轮）被压进了同一个 `workspace` 枚举，且**缺导航**。

### 2.2 目标：三层架构，流程拿到独立层级

```
L1  Agent总览（跨流程态势）         ← primary-tabs（补渲染）
     ├ KPI 卡片（金标反馈 / 评测运行 / 失败样本 / 改进候选）
     ├ 【新】Multi-Agent 流水线卡片：最近 4 条流程 × 四阶段迷你进度条，点击直达详情
     └ 待办清单
                    ↓ 点击某条流程
L2  流程工作台（workflow_id 维度）   ← 双栏：左栏流程列表 / 右栏四阶段详情
     ├ 详情头：workflow_id · 总体状态 · 锁定版本数 · 发起人
     ├ 【新】发起流程（测试负责人可见）
     ├ 【新】版本锁定条：4 阶段 × 包名/版本/哈希/是否锁定
     ├ 四阶段轨道：每阶段 = 状态标签 / 产出 / 单独评分 / 两个操作按钮
     └ 【新】端到端评测 + 联合链路图
                    ↓ 报告门禁通过
L3  飞轮四环（产出驱动的治理）        ← workspace-tabs（补渲染）
     金标资产 → 自动评测 → 轨迹归因 → 优化发布
```

```
┌─ Agent总览 ─┬─ 数据飞轮 ─┬─ 知识图谱 ─┐          ← primary-tabs
┌──────────────┬────────────────────────────────────┐
│ 流程列表 292px│  流程详情                          │
│              │  ┌ wf-交易网关-回归-20261002-01    │
│ ● 全流程通过  │  │ 状态 全流程通过 · 4/4 版本锁定   │
│ ◐ 有门禁未通过│  ├────────────────────────────────┤
│ ○ 待发起      │  │ 版本锁定  方案 v1.2 · 用例 v2.0 …│
│              │  ├────────────────────────────────┤
│              │  │ ①方案  ②用例  ③执行  ④报告      │
│              │  │  通过    通过   未测评  阻断     │
│              │  │  [运行门禁][放行][详情]          │
│              │  ├────────────────────────────────┤
│              │  │ [端到端评测]  [联合链路图]       │
│              │  └────────────────────────────────┘
└──────────────┴────────────────────────────────────┘
```

> **这个双栏形状的 CSS 已经写好了**：`.workspace-shell{grid-template-columns:292px minmax(0,1fr)}`、
> `.stage-rail`、`.tabs` 的 grid 变体全在 `<style>` 里，模板却渲染成了单栏。
> 也就是说**原设计意图就是这个布局，只是没落地**——重构成本比想象中低。

### 2.3 三个「信息缺失」的补位

| 缺失 | 位置 | 补什么 |
| --- | --- | --- |
| 无法发起 | 详情头右侧 | 「发起流程」按钮（`is_test_lead` 才可用）；弹窗输入 `workflow_id`，提交后**立刻展示 `locked_stages` / `unmanaged_stages`** |
| 看不见锁定版本 | 详情头下方 | 版本锁定条：4 阶段 × `skill_name` / `version` / `package_sha256` 前 8 位 / `release_state`；未锁定的标灰 + "无版本溯源" |
| 看不见端到端评测 | 四阶段轨道下方 | 「端到端评测」按钮，展开显示 `detail.evaluation`；与单阶段门禁**视觉上分开**，避免误认为同一个动作 |

### 2.4 面板文案与新口径对齐

`workspace === 'single'` 现文案：
> "用例审查、代码审查和知识库问答可以独立反馈、测评和进入飞轮。"

新口径下 `code_review` / `knowledge_query` **不算 Agent**，应改为：
> "用例审查是唯一的单次能力 Agent；代码审查与知识库问答属平台基础能力，只做质量观测、不进自进化。"

连带后端 `SINGLE_STAGES = ["case_review", "code_review", "knowledge_query"]` → `["case_review"]`
（见 §4 后端待收敛项 B1）。

---

## 3. 契约层：两套流程读模型必须收敛

**问题**：`cockpit.workflows[].stages[]`（前端在用）与 `workflow-status.stages[]`（唯一真值入口）
是**两套字段名**。前端 `types.ts::WorkflowStageGateView` 只描述前者，
接后者时类型全对不上，必然出现"页面读 A、接口给 B"。

| 语义 | `cockpit`（前端已接） | `workflow-status`（未接） |
| --- | --- | --- |
| 阶段状态 | `status` | **`state`** |
| 是否已进入 | （隐式：有 output 即进入） | **`entered`** |
| 能否进入 | （无） | **`can_enter`** |
| 产出 | `output_id` / `task_id` | **`output{id,task_id,created_at}`** |
| 门禁 | `gate_id` / `scores` / `reason` / `decided_by` 平铺 | **`gate{id,status,scores,threshold,reason,decided_by,decided_at,overridden,report_contract,evaluation}`** 嵌套 |
| 锁定版本 | `skill_name` / `skill_version` / `version_locked` / `package_sha256` | **`version{stage,managed,skill_id,skill_name,skill_version_id,version,package_sha256,lock_id,detail}`** 嵌套 |
| 阶段中文名 | （前端自己映射） | **`label`**（后端 `STAGE_LABELS` 提供） |
| 流程级 | （无） | `locked_version_count` / `managed` / `report_contract` / `completed` / `blocked_at` |
| 门禁阈值 | （无） | `gate.threshold` |

**收敛方案（推荐）**：以 `workflow-status` 的 schema 为**规范形状**，理由是它更完整——
多出 `entered` / `can_enter` / `label` / `threshold` / `evaluation` / `completed` / `blocked_at` 七项，
而 cockpit 缺这些字段，界面只能自己猜。

具体做法：
1. `types.ts` 新增 `WorkflowStatusView`（对应 `workflow-status` 全量返回），
   `ProjectWorkflowView` 保留给 cockpit 列表，但**只保留列表所需的最小字段**。
2. cockpit 的 `workflows[].stages[]` 字段名向 `workflow-status` 对齐（`status`→保留，新增 `entered`/`can_enter`/`label`），
   或直接让 cockpit 内部调 `workflow_status` 生成 stages（代价：20 条流程 × 查询，需评估）。
3. 前端详情页**只用 `workflow-status`**，列表页**只用 cockpit**——两边不再互相凑字段。

**另发现一处双份逻辑**：cockpit 用 `previous_open = bool(output and gate and gate.status in {...})`（`operations.py:529`）
推 `ready`/`blocked`，而 `assert_can_enter` 用 `gate.status in {...}`。
当前实践中两者等价（gate 一定有 output），但**这是两份会各自漂移的判定**，应抽成一个函数。

---

## 4. 后端待收敛项（本轮只登记，不改）

| # | 位置 | 现状 | 建议 | 影响 |
| --- | --- | --- | --- | --- |
| **B1** | `operations.py:414` | `SINGLE_STAGES = ["case_review","code_review","knowledge_query"]` | `["case_review"]` | 单次能力面板会多出 2 张不该有的卡片 |
| **B2** | `operations.py:206` | `status = "passed" if scores and all(...) else "failed"` | 无评分时置 **`unscored`**；`assert_can_enter` 放行集加 `unscored` | **当前会阻断链路**，与"评分先放一放"冲突（详见 §5） |
| **B3** | `operations.py:529` vs `:148` | `ready`/`blocked` 与 `can_enter` 两处各算一遍 | 抽 `_stage_can_enter(project_id, workflow_id, stage)` 单点 | 双份逻辑易漂移 |
| **B4** | `operations.py:515` / `:340` | 两套 stages schema | 见 §3 收敛方案 | 前端类型分裂 |
| **B5** | `capability_registry.py` | `is_evolvable('code_review') == True` | `False`（`requirements.md` §7 已定） | 与"代码审查不做自进化"一致 |
| **B6** | `task_binding.py:143` | `registered` 判定用 `versions__manifest__stage` **或** `capability__stages__contains` | 保持，但 `shadow` 包"登记了却无 active"会拒绝——需确认这是期望行为 | 四阶段当前全部落 `unmanaged` 或直接拒绝 |

---

## 5. 与评分的边界（本轮明确挂起）

用户口径：**"没评分之前流程可以往下走，需要自进化 skill 时才需要评分。"**

但当前实现**不满足这一点**，这是流程设计里必须处理的一个结：

```python
# operations.py:205-207
gate.scores = scores                                    # 无完成态 EvaluationResult 时 scores = {}
gate.status = "passed" if scores and all(...) else "failed"
gate.reason = "分层评测自动计算" if scores else "没有可用于门禁判断的已完成评测结果"
```

→ **没有评分 = `failed`**，而 `assert_can_enter` 只放行 `{passed, overridden}`。
于是"没评分"直接被判死，链路只能靠负责人逐阶段强行放行。这与"评分先放一放"直接矛盾。

**建议的最小改动（B2）**：新增 `unscored` 语义，区分"评了没过"和"压根没评"。

| 状态 | 含义 | 是否放行 | 与用户口径 |
| --- | --- | --- | --- |
| `pending` | 有产出，未点评测 | 否 | 提醒执行人去点门禁（保留人审动作） |
| **`unscored`** | 跑过评测，但无可用评分结果 | **是** | ← 满足"没评分也能往下走" |
| `passed` | 评分达标 | 是 | — |
| `failed` | 评分未达标 | 否（需负责人放行） | 真失败仍要拦 |
| `overridden` | 负责人留痕放行 | 是 | — |

**评分体系的接口位（只留位，不展开）**：
- 落点：`WorkflowStageGate.scores`（单阶段）+ `gate.detail.evaluation`（端到端）
- 量表：`EvaluationRubric(task_type, version, dimensions, ...)`，5 个 Agent 各一套（现只有 `case_review` 一套，且重复录入）
- 三轨统一落 `JudgeResult`，`evaluator_type` 现已支持 `llm_judge`/`rubric_rule`/`jury_aggregate`，**第三轨人工尚无取值**
- **触发点**：不是"每次产出都要求评分"，而是**在需要派生 skill 候选版本前校验评分存在且已校准**
  （`OptimizationProposalService.generate` 需归因 confirmed；晋级需门禁 passed）——具体待 `open-questions.md` §D 拍

---

## 6. 前端改动清单

| # | 改动 | 文件 | 依赖 |
| --- | --- | --- | --- |
| **F1** | 补 `primary-tabs` 渲染（Agent总览 / 数据飞轮 / 知识图谱），`primaryView` 可页内切换 | `AIQualityEvolutionView.vue` 模板 | CSS 已有 |
| **F2** | 补 `workspace-tabs` 或左侧 `stage-rail` 渲染，7 个面板可互相跳转 | 同上 | CSS 已有（`.stage-rail` / `.workspace-shell`） |
| **F3** | `workspace==='workflow'` 重写为**双栏**：左栏流程列表（来自 cockpit）/ 右栏流程详情（来自 `workflow-status`） | 同上 | F6 |
| **F4** | 新增「发起流程」弹窗：`workflow_id` 输入 + 提交后展示 `locked_stages`/`unmanaged_stages` | 同上 | F6 |
| **F5** | 新增版本锁定条（4 阶段 × 包/版本/哈希/是否锁定） | 同上 | F6 |
| **F6** | `service.ts` 补 **4 个函数**：`startWorkflow` / `getWorkflowStatus` / `evaluateWorkflow` / `buildWorkflowGraph`；`types.ts` 补 `WorkflowStatusView` | `service.ts` / `types.ts` | — |
| **F7** | `workspace==='single'` 文案与卡片收敛为仅 `case_review` | `AIQualityEvolutionView.vue` | B1 |
| **F8** | 状态映射表加 `unscored`（`gateText` / `gateColor`） | 同上 | B2 |
| **F9** | 删除死代码：`tabs` computed（7 项零引用）+ 未用 CSS | 同上 | F1/F2 完成后 |
| **F10** | 「运行门禁」与「端到端评测」拆成两个按钮、两种语义 | 同上 | F6 |

**F6 的接口形状（照抄后端契约，不要自己编）**：

```ts
// POST /operations/start-workflow  → 201
startWorkflow(projectId, workflowId) → {
  workflow_id, stage_order, bindings: Record<stage, BindingView>,
  locked_stages: string[], unmanaged_stages: string[]
}

// GET /operations/workflow-status?project=&workflow_id=
getWorkflowStatus(projectId, workflowId) → {
  workflow_id, stage_order,
  stages: Array<{
    stage, label, state, entered, can_enter,
    output: { id, task_id, created_at } | null,
    gate: { id, status, scores, threshold, reason, decided_by,
            decided_at, overridden, report_contract, evaluation } | null,
    version: { stage, managed, skill_id, skill_name, skill_version_id,
               version, package_sha256, lock_id, detail } | null
  }>,
  locked_version_count, managed, report_contract, completed, blocked_at
}

// POST /operations/evaluate-workflow
evaluateWorkflow(projectId, workflowId, stage) → { ...evaluation payload }

// POST /operations/build-workflow-graph
buildWorkflowGraph(projectId, workflowId) → { workflow_id, node_count, edge_count }
```

---

## 7. 验收口径

| # | 验收项 | 判据 |
| --- | --- | --- |
| A1 | 发起流程 | 负责人提交 `workflow_id` → 201，且**页面立刻显示** `locked_stages` 与 `unmanaged_stages`；非负责人看到按钮禁用 + 原因 |
| A2 | 版本锁定可见 | 详情页每阶段显示锁定包名/版本/哈希；未锁定阶段显示"无版本溯源"且**不显示"当前活跃版本"冒充** |
| A3 | 阶段推进 | 阶段 1 gate 通过前，阶段 2 显示 `blocked`；通过后显示 `ready`；`assert_can_enter` 抛错文案与页面状态**一致** |
| A4 | 无评分可推进 | 无 `EvaluationResult` 时门禁落 `unscored` 且**允许进入下一阶段**（B2 落地后） |
| A5 | 真失败仍拦 | `failed` 阶段阻断下一阶段，且「负责人放行」按钮出现、必须填原因 |
| A6 | 两个评测不混淆 | 「运行门禁」改状态、「端到端评测」不改状态，两者结果分别可查（`gate.scores` vs `gate.detail.evaluation`） |
| A7 | 流程可枚举 | 列表页从 cockpit 拿到历史流程；点击可进详情（`workflow-status`），刷新不丢选中态 |
| A8 | 页面零报错 | 浏览器 `pageerror` = 0，`blockedError` 不计为错误（门控结果） |

---

## 8. 本轮不做（明确边界）

- **评分体系**（第三轨人工校准、5 套量表补齐、校准一致率）——按指示挂起，只留 §5 的三个接口位。
- **`risk_identification` / `issue_tracking`**——平台无实现，不进 Agent 台账（`requirements.md` §3.2 已定）。
- **TTFT / 对话数**——不做（`requirements.md` §4 已定）。
- **构建与容器同步**——本文件只出设计；`npm run build` + `docker cp` 在实现阶段按
  `wharttest-local-frontend-verify` 技能执行。

---

## 9. 实现与验收记录（2026-10-02）

### 9.1 已实现

| 项 | 落点 | 说明 |
| --- | --- | --- |
| Agent 大盘 | `AIQualityEvolutionView.vue` | KPI 5 卡（各带 14 根**真实数据**迷你柱）+ 近 14 天会话/Token 双柱趋势 + 5 行 Agent 台账 |
| 发起流程 | 同上，`workspace === 'workflow'` | 入口定在 **数据飞轮 → 控制台 → 全链路测试**；弹窗填 `workflow_id`，提交后展示四阶段版本锁定结果 |
| 前端接线 | `service.ts` | 补齐 `startWorkflow` / `getWorkflowStatus` / `evaluateWorkflow` / `buildWorkflowGraph` |
| 契约类型 | `types.ts` | 新增 `WorkflowBindingView` / `StartWorkflowResult` / `WorkflowStageStatusView` / `WorkflowStatusView` |
| 口径收敛 | 同上 | `single` 面板的文案与数据源一并收敛为仅 `case_review` |

**指标口径**：会话数 / 活跃用户 / Token 消耗 / 平均耗时 / 失败率，**全部由 `RetrievalTrace` 现场聚合**，
不新增接口。TTFT 与对话数按 `requirements.md` §4 不做；评分按用户指示暂挂，故未纳入 KPI。

> ⚠️ 上一轮删掉的 KPI 迷你条是**无数据来源的装饰**（`height: 24 + ((n*11)%22)` 硬算出来的）；
> 本轮的迷你柱是**真数据**（按天分桶的真实会话/Token 序列）。两者不是同一件东西，不要互相回退。

### 9.2 顺带修掉的两个真实缺陷

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 1 | 发起完流程，列表仍是 **0 条**，看起来像发起失败 | `cockpit.workflows` 只从**产出**反推 `workflow_id`，而 `start_workflow` 只锁版本、不写产出 | `operations.py` 把 `WorkflowSkillLock` 里的 `workflow_id` 并入列表（按 `locked_at` 升序追加，配合 `reversed` 让最新排最前） |
| 2 | 「独立能力评测」仍显示 3 张卡，与新文案自相矛盾 | `SINGLE_STAGES` 仍是旧三件套 | 收敛为 `["case_review"]` |

### 9.3 四阶段存量包激活（用户指示 3）

四个 `webtest-*` 包**原本就都在库里**、`manifest.stage` 声明也正确，但 `CapabilityRelease`
全部停在 `shadow`。而 `SkillRuntimeResolver.resolve_version` 只认 `active`，于是出现两种都不想要的结果：

- **登记了这些包的项目**（`test`）：`bind_stage` 走「已登记却无活跃版本」的**拒绝**分支，**发起即 400**；
- **未登记的项目**（`演示项目`）：按 `allow_unmanaged` 放行，但四阶段全部「无版本溯源」。

激活后（`scripts/activate_webtest_stages.py`）：

| 阶段 | skill 包 | 版本 | 状态 | package_sha256 前 12 位 |
| --- | --- | --- | --- | --- |
| `test_plan_generation` | `webtest-plan-generator` | 1.0.0 | **active** | `f56344b9fd7c` |
| `testcase_generation` | `webtest-case-generator` | 1.0.0 | **active** | `209ec25773cf` |
| `test_execution` | `webtest-execution-runner` | 1.0.0 | **active** | `e504db324aa7` |
| `report_generation` | `webtest-report-generator` | 1.0.0 | **active** | `07bc9b20f22d` |

> ⚠️ **治理放宽留痕**：`complete_canary` 默认要求 3 个灰度观察窗口；存量包首次接入时平台上尚无观察数据，
> 脚本显式传 `min_observations=0`。这是**有意的放宽**，不是默认路径。
> 回退方式：`CapabilityReleaseService.rollback(...)` 或把 `state` 改回 `shadow`。
> 四个包全部挂在**项目 7（test）**；要挪到其它项目需另行规划（改 `project_id` 或重新上传）。

### 9.4 验收（真实浏览器）

| 验收项 | 结果 |
| --- | --- |
| Agent 大盘结构 | KPI 5 卡 × 14 根迷你柱、趋势 14 柱、台账 5 行、表头 6 列 |
| 旧装饰块清除 | `.metrics` / `.loop-panel` / `.source-panel` / `.spark` 计数**全为 0** |
| 发起流程 | 四阶段**全部 `binding locked`**，包名 / 版本 / sha256 齐全，无 `unmanaged` 警告 |
| 发起后可见 | 流程列表由 0 变 1（9.2 的修复生效） |
| 单次能力收敛 | 面板仅剩「用例审查」1 张卡 |
| 页面报错 | `pageerror` + `console.error` = **0** |

**证据**：`output/agent-board-shots/{shot-agent-board,shot-flow-panel,shot-flow-started,shot-single-panel}.png`
**脚本**：`/tmp/agent_board_check.js`（宿主 Chrome，`NODE_PATH=<WHartTest_Vue>/node_modules`）

### 9.5 转为后续项

- **`weekDelta` 全为 `null`**：项目 1 的轨迹都落在最近 7 天内，前 7 天无数据 → 按设计返回 `null`
  而不是编造 `0%`。数据积累后环比会自动出现。
- **B2（`unscored`）仍未做**：`evaluate` 在无评分时判 `failed` 会阻断链路，仍是隐患。
- **B3 / B4（双份放行判定、两套 stages schema）**：本轮未动。
- **未 push**：本轮改动尚未提交（本地 `dev` 领先 `origin/dev`）。

## 10. 逐阶段推进 + 人工评分 / 人工确认（2026-10-02 第二轮）

### 10.1 状态机扩展（`workflow_models.py`）

`WorkflowStageGate.STATUS_CHOICES` 由 4 态增至 6 态，并**把语义集合提到模块级**：

| 状态 | 含义 | 放行 | 可确认 | 可评分 |
| --- | --- | --- | --- | --- |
| `pending` | 待测评 | 否 | 是 | 是 |
| `unscored` | **无评分**（信号缺失，非"未达标"） | 否 | 是 | 是 |
| `passed` | 测评通过 | 是 | 否 | 是（改判） |
| `failed` | 测评失败（结论为负） | 否 | **否** | 是（改判） |
| `confirmed` | **人工确认放行**（无评分也能推进） | 是 | 否 | 否 |
| `overridden` | 负责人强制放行（失败后推翻） | 是 | 否 | 否 |

模块级常量（`workflow_models.py` 顶部）：`GATE_PASSING_STATES` / `GATE_CONFIRMABLE_STATES` /
`GATE_SCORABLE_STATES` / `GATE_HUMAN_FINAL_STATES`。

> ⚠️ **必须放在模块级，不能放类体里**：别的模块要写
> `from .workflow_models import GATE_PASSING_STATES`，而**类属性导不出来**。
> 且 `operations` 是从各 view 里惰性 import 的，`manage.py check` 碰不到它——
> 写成类属性时 `check` 照样通过，要等跑测试/真调接口才炸。

**放行判定收敛为单点**：原先 `{"passed","overridden"}` 散落在 5 处、2 个模块
（`operations.py` 的 `assert_can_enter` / `workflow_status` 前一阶段判定 / 报告完成判定 /
`cockpit` 的 `previous_open`，以及 `report_gates.py` 的 `gate_passed`），全部改读常量。
否则加一个 `confirmed` 就会出现"页面显示可以继续、接口却 400"。

### 10.2 不变式：人工结论不被自动重算推翻（`is_human_decided`）

判据三个来源任一成立：`status ∈ {confirmed, overridden}`，或 `detail.manual_score` 存在
（人工评分后的状态是 `passed`/`failed`，**光看 status 分不出机器评的与人评的**，判据落在留痕上）。

`evaluate` 命中时：**不改 status、不覆盖 scores**（人工分才是状态的依据，机器分挪到
`detail.auto_scores` 作补充证据）。若 `scores` 被清空而 `status=passed`，页面会显示
"已通过但无分数"，解释不通。

产出换了则 `register_output` 清空 `detail`、状态回 `pending` → **"新内容必须重新过门禁"依然成立**。

### 10.3 三个新动作（`WorkflowGateService`）

| 方法 | 起始状态 | 结果 | 权限档 |
| --- | --- | --- | --- |
| `score(gate, actor, score, reason)` | pending/unscored/passed/failed | 百分制入参，存 `scores={"manual": v/100}`，按阈值判 passed/failed | 项目成员（同 `evaluate`） |
| `confirm(gate, actor, reason)` | pending/unscored | `confirmed`，默认原因兜底 | 项目成员 |
| `override(gate, actor, reason)` | any（原样） | `overridden`，**必须填原因** | 测试负责人 |
| `plan_execution(project, workflow_id, stage, actor)` | 前置阶段已放行 | 下发执行参数 + 落痕，不代替执行 | 项目成员 |

- **刻度**：对外百分制、对内 0–1。否则 `threshold`（0.7）会在两套刻度下被两种解释同时使用。
- **`confirm` 与 `score` 并存不是冗余**：确认回答"能不能先过"，评分回答"做得怎么样"。
- **`confirm` 不得用于 `failed`**：已有负面结论时该走 `override`（要填原因、留更重的痕），
  一次普通点击不该把"不达标"变成"已通过"。

### 10.4 `plan_execution` 为什么不自己跑阶段

平台**只有 `test_execution` 有真正的执行器**（TestExecution + Celery，且必须先由人选好用例套件）；
方案 / 用例 / 报告三个阶段没有可被服务端直接调用的生成实现，产出由 agent 经
`/orchestrator/agent-loop/` 提交。造一个"点了就在后台跑"的假入口，比没有按钮更糟。

因此它做三件真事：① 把"能不能执行"收敛到与 `assert_can_enter` **同一个判断**（越阶段必须被拒，
否则步骤条的"逐阶段推进"只是装饰）；② 下发 `channel` / `module_key` / 锁定的 Skill 版本 /
`parent_output_ids`；③ 写 `detail["execution"]` 并占位建无产出门禁 → 步骤条显示 `running`。

执行通道真值表 = `WorkflowGateService.STAGE_EXECUTION_CHANNELS`（`platform` / `agent`）。
**这张表必须在后端**：放前端按阶段名写 if-else，后端将来补执行器时页面不会跟着变。

### 10.5 API

`BASE = /api/knowledge-evolution/operations/`

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `score-workflow-stage/` | POST | 人工评分（百分制） |
| `confirm-workflow-stage/` | POST | 人工确认放行 |
| `execute-workflow-stage/` | POST | 执行派发（前置校验 + 参数下发） |
| `workflow-stage-output/` | GET | 查看结果（产出正文 + 门禁证据） |

- `workflow-status` 的 stage 增加 `execution`；gate 增加 `confirmable` / `scorable` / `passed` /
  `human_decided` / `manual_score`；顶层增加 `current_stage`。
- `cockpit` 的 stage 增加 `threshold` / `confirmable` / `scorable` / `passed` / `human_decided` /
  `manual_score` / `execution`；workflow 增加 `current_stage`；status 新增 `running`。
- `workflow-stage-output` 定位走 **project + workflow_id + stage 三者一起**，不让页面按 output id 直取：
  否则任何项目成员都能靠猜 id 读到别的项目的产出正文。

### 10.6 前端（`AIQualityEvolutionView.vue`）

- `全链路测试` 面板由"四张平铺 stage 卡"改为**步骤条**：`done`（已放行）/ `active`·`running`·`failed`
  （当前步）/ `todo`（未轮到，灰 + 降透明度）。连接线只在**前一步已放行**时点亮。
- 只有**一个阶段**展开详情（默认 `current_stage`），可点步骤条切换 → 满足"不是一次性四个阶段都出来"。
- 详情动作为：查看结果 / 执行本阶段 / 运行门禁测评 / 人工评分 / 确认进入下一步 / 负责人放行，
  显隐全部读后端回来的 `confirmable` / `scorable` 等字段，**不在前端重算准入**。
- 派发参数（`module_key` / `workflow_id` / 上游产出 ID）**常驻**在阶段详情里，不只弹 toast：
  用户要拿着它去另一个页面填。
- 布局坑：`.step-link` 是"非步骤"元素，用 **flex**（步骤 `flex:1`、连接线固定 20px）。
  放在 `grid-template-columns:repeat(4,1fr)` 里会变成 4 步 + 3 线 = 7 个格子，必然折成 2×2。

### 10.7 验收

**后端**：新增 `tests_t21_stage_progression.py`（23 项，全绿）；`tests_t15` / `tests_t16` /
`test_project_quality_cockpit` 回归 69 项全绿；`makemigrations --check` → `No changes detected`。
迁移 `0029_workflowstagegate_status_choices`（只改 `choices`，库层面空操作）。

**真实浏览器**（项目 5 `drill-wf-57e0824d` / 项目 7）：

| 验收项 | 实测 |
| --- | --- |
| 步骤条 | 4 步一行；只有当前步亮，其余 `todo` 灰 |
| 查看结果 | 弹窗读到产出正文 + 门禁证据 + 任务号 |
| 人工评分 | 弹窗标题「评分（0–100，阈值 **70**）」→ 阈值取自后端门禁；提交后 `scores={'manual':0.9}`、`decided_by=admin`、`detail.manual_score` 落库 |
| 确认进入下一步 | `testcase_generation` → `confirmed`；下一步 `测试执行` 随即由 `todo` 变 `active` |
| 逐阶段生效 | 步骤条 2/4 已放行，连接线前两段点亮 |
| 未轮到无动作 | 灰步展开无任何按钮 |
| 执行本阶段 | 下落痕 `detail.execution`、`output` 仍为 `None` → 状态 `running`（**不假亮起**）；派发参数常驻显示 |
| 页面报错 | `pageerror` + `console.error` = **0** |

**证据**：`/tmp/shot-stepper-flow-after.png`、`/tmp/shot-stepper-dispatched.png`、
`/tmp/shot-stage-output.png`、`/tmp/shot-stage-score-modal.png`、`/tmp/shot-dispatch-note.png`
**脚本**：`/tmp/stage_stepper_check.js`、`/tmp/dispatch_note_check.js`

### 10.8 仍未做

- **B4**：`cockpit.workflows[].stages[]` 与 `workflow-status.stages[]` 两套 schema 仍未收敛
  （本轮把新字段同步加到了两边，收敛动作本身还没做）。
- 三个阶段（方案 / 用例 / 报告）**仍无平台内执行器**：`执行本阶段` 只做校验 + 参数下发，
  真正的产出要靠 agent 经 agent-loop 提交。
- 未 push / 未 commit。

---

## 11. 主链路口径变更：风险识别 → 测试用例 → 测试执行 → 问题跟踪

（2026-10-02）主链路由历史四阶段（方案/用例/执行/报告）改为
**`risk_identification → testcase_generation → test_execution → issue_tracking`**。
`test_plan_generation` 与 `report_generation` **不删除**，只是降为单次能力（仍可评测/反馈/自进化，但不再占链路阶段位）。

### 11.1 双模板并存是这次变更的核心决策

存量流程的阶段序列与新流程不同，**不能套同一套序列解释**，否则存量流程的产出会因
「没带 workflow_id」被协议层拒掉，越阶段校验也会静默失效。因此：

- `capability_registry`：`WORKFLOW_STAGES`（新）/ `LEGACY_WORKFLOW_STAGES`（历史）/ `ALL_WORKFLOW_STAGES`（并集）。
- `WorkflowGateService.stage_order_for(project_id, workflow_id, *, stage="")`：按**流程自身留痕**
  （版本锁 ∪ 门禁记录 ∪ 产出 `metadata.protocol.workflow_id`）判定该流程用哪套序列；
  第三份证据 `stage` 参数用于"流程无留痕 + 问的恰好是历史独有阶段"的场景。
- 所有"这是不是链路阶段"的判断一律读 `ALL_WORKFLOW_STAGES`（并集），不再写死四元组。
- `protocol.WORKFLOW_STAGES`（要求带 `workflow_id` 的集合）同样是**并集**。

### 11.2 收口契约按阶段参数化

`report_gates.CLOSING_CONTRACTS`：`report_generation` 仍要 4 项统计
（覆盖率/通过率/失败分布/未闭环问题）；`issue_tracking` **只要 2 项**
（失败分布/未闭环问题）——问题跟踪没有覆盖率与通过率概念，硬要求只会逼出编造数字。
`ReportGateService.contract_for()` / `is_closing_stage()` 是唯一入口。

### 11.3 向导逐阶段选包（`pins`）

- `stage_catalog` 端点按阶段给出候选与默认包；**不按 manifest 声明硬筛候选**——
  主链路刚换阶段名，现存包声明的还是旧阶段，硬筛会让向导一个候选都给不出来。
- `start_workflow(pins={stage: skill_id})`：人选到"声明的是别的阶段"的包允许（`allow_stage_mismatch=True`），
  但必须把 `pinned` / `declared_stage` / `stage_mismatch` 写进流程锁 `detail` 留痕，并在返回里给 `mismatched_stages`。
- 仍必须 `active` 版本（R13 不放宽）。

### 11.4 前端两栏 + 向导两步

- **发起流程 = 两步向导**：① 四阶段各一个 Skill 搜索框（数据源 `workflow-stage-catalog/`），
  四阶段都选到**可运行**的包才解锁「下一步」（`:ok-button-props` 真 disabled，不是靠提示文案）；
  ② 填 `workflow_id` 并「发起并锁定版本」。`on-before-ok` 返回 `false` 让第一步原地换内容，
  不关弹窗（关掉再开会让已选的包全部重置）。
- **全链路测试面板 = 左（流程版本列表）+ 中（阶段状态条 01–04 + 四张 AI/人工时间线卡片）**，
  外层已有的右侧团队栏同时承载「测试团队」与「AI 专家团队」（后者列出各阶段 Skill 与版本）。
  合计三栏。
- 左栏元信息（`stage_order` / `stage_template` / `locked_version_count` / `passed_count` /
  `completed` / `created_at` / `updated_at`）**由后端算**：发起时间不在 `stages` 里，前端推不出来。
- 卡片里的「退回修改」**没有对应后端动作**，因此不摆这个按钮；在不通过时给一行说明
  （改产出后重跑门禁，或由负责人放行），而不是留一个按了没反应的按钮。

### 11.5 顺带收敛的写死点

- `workflow_models.WorkflowStageGate.STAGE_CHOICES`：改读 `ALL_WORKFLOW_STAGES` + `STAGE_LABELS`，
  配套手写迁移 `0030_workflowstagegate_stage_choices`（只改 `choices`，库层面空操作）。
- `orchestrator_integration/agent_loop_view.py`：四处写死阶段集合改为
  `FLYWHEEL_MODULE_KEYS`（`ALL_TASK_TYPES`）与 `WORKFLOW_MODULE_KEYS`（`ALL_WORKFLOW_STAGES`）。
  其中 `workflow_id` 拼装那处若不改，新链路的 `risk_identification` / `issue_tracking`
  产出会因缺 `workflow_id` 被协议层 `validate()` 拒掉，**且异常被 `except` 吃掉、只留一条日志**。
- `evolution.DEFAULT_STAGE_WEIGHTS`：是"两套模板的并集先验表"，加总不为 1 是**可以**的
  （最终分 `weighted_score/total_weight` 按实际出现的阶段归一化）。补注释防止后人误改。

### 11.6 验收

**后端**（全部 `--noinput`，按模块拆分避免 OOM）：

| 模块 | 结果 |
| --- | --- |
| `tests_t22_stage_order_templates`（新建 38 项） | ✓ |
| `tests_t15` / `tests_t16` / `tests_t21` / `tests_t22`（127 项） | ✓ |
| `tests_t09_t13` / `test_capability_evolution`（70 项） | ✓ |
| `test_capabilities_protocol` / `test_project_quality_cockpit` / `test_attribution`（25 项） | ✓ |
| `tests_t18` / `test_capability_evolution`（39 项） | ✓ |
| `test_evaluation` / `test_evaluators_v2` / `test_feedback` / `test_gold`（24 项） | ✓ |
| `test_optimization` / `test_experiments` / `test_retrieval` / `test_distillation` / `test_eval_review_bridge`（27 项） | ✓ |
| `test_extractors` / `test_graph` / `test_graph_adapters` / `test_graph_client` / `test_knowledge_models` / `test_llm_judges` / `test_projection`（78 项） | ✓ |
| `tests` / `tests_t14` / `test_seed_flywheel_demo`（29 项） | ✓ |
| `orchestrator_integration`（35 项） | ✓ |
| `skills` + `testcases`（191 项） | ✓ |

`makemigrations --check` → `No changes detected`（含手写迁移 0029 / 0030）。

**真实浏览器**（项目 7「test」，账号 `admin`）：

| 验收项 | 实测 |
| --- | --- |
| 向导第一步 | 标题「发起全链路测试 · 1/2 选择 Skill 包」，四阶段 4 个搜索框，每阶段 10 个候选 / 4 个可运行 |
| 未选满时「下一步」 | **真 disabled**（`arco-btn-disabled`），弹窗不关、不进第二步；缺的是「风险识别」「问题跟踪」 |
| 选满后 | `okDisabled=false`；第二步显示四阶段选定摘要 + `workflow_id` 输入 |
| 发起结果 | 4 个 binding 全 `locked`（带版本与 sha）；跨声明提示「跨声明选用 2 个阶段（风险识别、问题跟踪）」 |
| 左栏 | 2 条流程；发起后自动选中新流程；分别标注「当前链路四阶段」/「历史链路四阶段」；锁定 4/4 |
| 中栏 | 阶段状态条 01–04（风险识别/测试用例/测试执行/问题跟踪）；4 张卡片各含「AI 生成/处理」与「人工确认/复核」两栏；未轮到 `todo` 灰 |
| 右栏 | 「AI 专家团队 4/4 已锁定」，逐阶段列出 Skill 与版本 |
| 左栏切换 | 高亮唯一且与中栏一致；切换后卡片数仍 4 |
| 旧版选择器 | `.workflow-list` / `.stage-stepper` 已不存在 |
| 页面报错 | `pageerror` + `console.error` = **0** |

**证据**：`/tmp/shot-wf-panel.png`、`/tmp/shot-wf-wizard-step1.png`、`/tmp/shot-wf-wizard-filled.png`、
`/tmp/shot-wf-wizard-step2.png`、`/tmp/shot-wf-after-start.png`、`/tmp/shot-wf-switch2.png`
**脚本**：`/tmp/wf_three_column_check.js`、`/tmp/wf_highlight_check.js`

### 11.7 仍未做

- `cockpit.workflows[].stages[]` 与 `workflow-status.stages[]` 两套 schema **仍未收敛**（B4）。
- `test_execution` 之外三个新链路阶段的平台内执行器仍缺（同 §10.8）。
- 未 commit / 未 push。

