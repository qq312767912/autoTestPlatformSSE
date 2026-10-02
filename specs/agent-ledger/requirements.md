# Agent 台账与分阶段评分（口径定义）

> 状态：**口径已定，实现未开始**。本文件只固化"谁算 Agent、按什么粒度、怎么评分"，
> 不含实现方案。相关既有规格见 `../production-evolution-skill-hub/`、`../project-quality-gates/`。

## 1. 术语与两套口径的关系

本平台从此有**两套并存但不等价**的能力口径，必须显式区分，不得互相顶替：

| 口径 | 数量 | 用途 | 真值来源 |
| --- | --- | --- | --- |
| **业务能力口径** | 8 类 | 金标组织、门禁分区策略、前端能力目录 | `knowledge_evolution/capability_registry.py::BUSINESS_CAPABILITY_STAGES` |
| **Agent 口径**（本文件新增） | **5 个** | Agent 运行台账、按 Agent 聚合指标、分阶段评分 | 本文件 §2 |

两者不是包含关系：业务能力里有 3 类**不计入 Agent**（见 §3）。

> ⚠️ 三套口径并存是当前最大的混乱源：业务能力 **8 类**、`EvaluationSuite.TASK_TYPE_CHOICES` **9 项**、
> Agent **5 个**。三者互不相等，且评测集口径尚未收敛（其中 3 项已被排除出 Agent，却仍可选）。
> 收敛方案见 `open-questions.md` §A1。


## 2. Agent 身份定义（已定）

**粒度 = 版本实例**，不是能力类型。

```
agent_id = (project, task_type, skill_version)
```

理由：同一能力的两个 skill 版本是**两个可独立评分的运行实体**；
只有按版本实例聚合，"这一版比上一版好还是差"才成立，回滚也才能界定影响范围。
按 `task_type` 聚合会把不同版本的表现混成一个数，失去判据价值。

计入 Agent 的 5 个阶段（即 `SKILL_CAPABILITY_STAGES`）：

| task_type | 中文 | 形态 |
| --- | --- | --- |
| `case_review` | 用例审查 | Skill 型，单次能力 |
| `test_plan_generation` | 测试方案生成 | Skill 型，四阶段第 1 阶段 |
| `testcase_generation` | 测试用例生成 | Skill 型，四阶段第 2 阶段 |
| `test_execution` | 测试执行 | Skill 型，四阶段第 3 阶段 |
| `report_generation` | 报告生成 | Skill 型，四阶段第 4 阶段 |

### 2.1 四阶段是 multi-agent（已定）

`test_plan_generation → testcase_generation → test_execution → report_generation`
是一条 **multi-agent 编排**：

- 每个阶段是**独立的 Agent 实例**，**必须单独评分**，不合并成一个总分。
- 阶段评分落 `WorkflowStageGate.scores`（该字段已存在，见 §4 缺口 ③）。
- 阶段所用的 skill 版本必须读**任务启动时锁定的**那一份（`WorkflowSkillLock`），
  不得读"当前活跃版本"——否则历史链路会显示成用了新包，且回滚无法界定影响范围。
- 编排层（workflow）作为**第四个维度**保留，用于展示阶段进度与门禁状态，
  但它本身不产出"评分"，只是各阶段评分的容器。

## 3. 明确不计入 Agent 的能力（已定）

| task_type | 中文 | 形态 | 不计入的理由 |
| --- | --- | --- | --- |
| `code_review` | 代码审查 | `KIND_COMPOSITE` | 复合型，不打包成 Skill、无 skill 版本；**且不需要 skill 自进化，直接移出可进化能力范围** |
| `knowledge_query` | 知识问答 | `KIND_PLATFORM_UTILITY` | 平台基础能力，不作为业务能力对外呈现 |
| `risk_identification` | 风险识别 | `KIND_FEEDBACK_SOURCE` | **不是 Skill 做的**，无 skill 版本 |
| `issue_tracking` | 问题跟踪 | `KIND_FEEDBACK_SOURCE` | **不是 Skill 做的**；且**平台当前并没有这项能力**（见 §3.2） |

四类统一定位为**平台基础能力**，不进 Agent 台账、不做版本实例聚合。

### 3.1 Agent 的唯一判据

**能否通过 Skill 直接迭代升级**。符合者为 5 个 Skill 型阶段，不符合者一律不算 Agent。
该判据同时取代了原先按 `kind` 分类的判断方式——`kind` 描述"形态"，判据描述"能不能进 Agent 台账"，两者不等价。

### 3.2 `risk_identification` / `issue_tracking` 的平台现状（已核实）

二者在后端**只有枚举、常量、权重表与允许集，没有任何业务实现**（无 service、无 view：

- `models.py::TASK_TYPE_CHOICES`、`protocol.py`、`migrations/0010`、`0021`、`0022` — 枚举残留
- `evolution.py` — 权重表各 `0.15`
- `capability_registry.py`、`skills/validation.py`、`evaluation_gates.py` — 允许集
- `capability_models.py::CapabilityDefinition.stages` — help_text 示例

库中 5 条对应产出**全部是演练假数据**：`task_id` 带 `drill-` 前缀、
`model_version='drill-model'`、`prompt_version='drill-prompt'`、`content` 仅一行 workflow JSON。
另有 1 个 `risk_identification` 评测集同源。

→ 待清理项，另见 `open-questions.md` §A2。


## 4. 明确不做（已定）

- **平均 TTFT**：不做。平台走非流式调用，无首 token 时间；该指标基于流式 API，硬凑无意义。
- **对话数**：不做。不把 `ExecutionSpan` 条数当作展示指标。

## 5. 实现前置：三条已实测断裂的链路

> 实测时点 2026-10-02，本地环境真实库。**这三条不补齐，"版本实例粒度"只是空台账。**

| # | 链路 | 载体 | 实测 | 影响 |
| --- | --- | --- | --- | --- |
| ① | 产出 → skill 版本 | `GenerationOutput.skill_version` | **0 / 32 条有值**（`skill_package_sha256` 同样 0 / 32） | 无法把任一产出归到某个 Agent 版本名下 |
| ② | 阶段 → 锁定版本 | `WorkflowSkillLock` | **0 条记录** | 无版本锁，阶段用的哪一版不可追溯 |
| ③ | 阶段 → 单独评分 | `WorkflowStageGate` | **0 条记录**（`scores` 非空 0 条） | 分阶段评分无数据落点 |

作为对照，会话层是健康的：`RetrievalTrace` 34 条中 `user` 34/34、`timings` 34/34、`token_usage>0` 26/34。
即**"谁在用、花了多久、花了多少 token"有真值，"用的是哪个 Agent 版本"没有**。

### 5.1 skill 包就绪度（5 个 Agent）

| Agent 阶段 | skill 包 | CapabilityRelease 状态 |
| --- | --- | --- |
| 用例审查 | `test-case-clarity-review` | `active` |
| 测试方案生成 | `webtest-plan-generator`（在 `staged-skills/`） | **未发布** |
| 测试用例生成 | `webtest-case-generator` | `shadow` |
| 测试执行 | `webtest-execution-runner` | `shadow` |
| 报告生成 | `webtest-report-generator`（在 `staged-skills/`） | **未发布** |

**没有任何一条 `CapabilityRelease` 的名字叫 `case_review` / `test_plan_generation` / …**——
业务阶段与 skill 包之间目前**没有显式绑定**，这是 ① 断链的上游原因。

## 6. 三级评分框架（已定）

人工评分**不是"给每条产物打个分"**，而是这套框架的**第三轨**：

| 轨 | 职责 | 判据性质 | 现有实现 |
| --- | --- | --- | --- |
| **第一轨 代码规则** | 文件、路径、关键词校验；安全拦截；状态检查 | 确定性硬判断 · 毫秒级、零成本、可复现 | ✅ **L0 六项**（schema / parsability / truncation / sensitive_data / reference_validity / tool_success） |
| **第二轨 LLM 裁判** | 量表维度评分并输出**部分得分**；Temperature=0，日志全量留痕 | 模糊任务评分 · 覆盖需语义理解的判断 | ✅ **L1**（`jury_aggregate` / `rubric_rule` / `llm_judge`） |
| **第三轨 人工校准** | 人工抽检 LLM 评分，**一致率不低于 85%**；校准数据回流优化评分 Prompt | 质量金标准 · 结论可信的最后防线 | ❌ **零实现** |

**每类 Agent 的评分标准不同**，需为 5 个 Agent 各备一套量表。

### 6.1 承载模型（已存在，不得新造重复结构）

- `EvaluationRubric`：`task_type` + `version` + `dimensions` + `required_items` + `forbidden_items` + `jury_config`
  —— 天然承载"每类 Agent 一套量表"，`version` 对应"量表迭代"。
- `JudgeResult`：`level` + `evaluator_type` + `score` + `passed` + `confidence` + `dimensions`
  + `evidence` + `rationale` + `raw_output` —— **三轨统一落这一张表**，`dimensions` 承载部分得分。

> 现状缺口：`EvaluationRubric` 库里只有 **2 条**，且是**同一个**（`case_review`「用例审查误报检测」，
> `version` 为空、重复录入）。**其余 4 个 Agent 阶段一条量表都没有。**

### 6.2 流程不阻塞（已定）

- **没有评分也能往下走**；**需要自进化 skill 时才需要评分**。
- 即：评分不是流程的必过关卡，而是 **skill 自进化动作的前置校验**。具体触发点与校验条件见
  `open-questions.md` §D。

## 7. 与既有注册表的关系

- `code_review` **不做 skill 自进化**，移出可进化能力范围。
  现状 `capability_registry.is_evolvable('code_review')` 仍为 **`True`**，**待改为 `False`**
  （连同 `COMPOSITE_CAPABILITY_STAGES`、`EvaluationSuite.TASK_TYPE_CHOICES` 的收敛，
  见 `open-questions.md` §A1）。代价很小：其复合发布通路**试过但未成**——
  2 条 `kind='prompt'` 的 release 状态均为 `rolled_back`，无 active。
- 判据区分：`is_evolvable()` 回答"能不能派生候选版本"；Agent 口径回答"要不要按版本实例做运行台账"。
  两问不同源，**不得互相覆盖**。
