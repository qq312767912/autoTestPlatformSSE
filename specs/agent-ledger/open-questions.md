# 质量飞轮：待决策问题清单

> 依据：`specs/agent-ledger/requirements.md`（已定口径）+ 2026-10-02 本地真实库实测。
> 目的：把"想得不够清楚"的地方变成**一条条可以拍板的问题**。每条给出我的倾向，仅供参考。
> 版本：v2（2026-10-02，已并入三级评分框架与 code_review 去掉自进化的决定）

## 0. 已定，不再问

**目的与身份**
- 质量飞轮的**唯一目的**：为 Skill 自进化服务。
- **Agent 判据**：能否通过 Skill 直接迭代升级。因此 Agent = 5 个 Skill 型阶段
  （用例审查、测试方案生成、测试用例生成、测试执行、报告生成），粒度 = `(项目, task_type, skill_version)`。
- 四阶段 = **multi-agent**，每阶段**单独评分**。

**不计入 Agent（平台基础能力）**
- `knowledge_query`、`risk_identification`、`issue_tracking`（后两者平台本就没有能力，见 §1.4）
- `code_review`：**不需要 skill 自进化，直接去掉**

**明确不做**：TTFT、对话数。

**评分框架**
- 人工评分 = **三级评分框架的第三轨**：第一轨代码规则（确定性硬判断）/ 第二轨 LLM 裁判（模糊任务评分）/
  第三轨人工校准（质量金标准）。**每类 Agent 的评分标准不同**，需先留出框架。
- **流程不阻塞**：没评分也能往下走；**需要自进化 skill 时才需要评分**。

## 1. 已核实的事实（提问的依据，不必再答）

### 1.1 三轨：前两轨已跑，第三轨零实现

| 轨 | 职责 | 现状 | 实测判分数 |
| --- | --- | --- | --- |
| 第一轨 代码规则 | 确定性硬判断 | **已实现** | L0 六项各 3 条 = 18 |
| 第二轨 LLM 裁判 | 模糊任务评分 | **已实现** | `llm_judge` 6 / `rubric_rule` 3 / `jury_aggregate` 3 = 12 |
| 第三轨 人工校准 | 质量金标准 | **零实现** | 无任何 `human` 型 `evaluator_type` |

**好消息：容器已经存在，不必新造。**
- `EvaluationRubric`：`task_type` + `version` + `dimensions` + `required_items` + `forbidden_items` + `jury_config`
  ——天然就是"每类 Agent 一套量表"，且**量表可版本化**（对应"校准数据回流优化评分 Prompt"）。
- `JudgeResult`：`level` + `evaluator_type` + `score` + `passed` + `confidence` + `dimensions`
  + `evidence` + `rationale` + `raw_output` ——**三轨可统一落这一张表**，`dimensions` 正好承载"部分得分"。

**但量表几乎没建**：库里 `EvaluationRubric` 只有 **2 条**，且是**同一个** ——
都叫「用例审查误报检测」、`task_type=case_review`、**`version` 为空**、重复录入。
**其余 4 个 Agent 阶段一条量表都没有。**

### 1.2 人工评分入口只有"读"，没有"写"
前端 `service.ts:119` 有 `createFeedbackEvent()`，但**全项目没有任何 UI 调用它**；
只有 `listFeedbackEvents()` 被两个页面用来"看"。库里 24 条 `actor_type='user'` 的反馈全部来自脚本/服务端。

### 1.3 现有反馈是"标签"不是"分数"
`FeedbackEvent.signal` 共 10 种：`accepted`(11) / `rejected`(6) / `false_positive`(3) /
`test_passed`(2) / `test_failed`(2) / `edited`(2) / `missed`(1) / `reverted`(1) /
`defect_confirmed`(1) / `merged`(1)。**没有任何分值字段。**

### 1.4 `issue_tracking` / `risk_identification` 是纯枚举残留
全后端只有枚举、常量、权重表（`evolution.py` 各 0.15）与允许集，**无 service、无 view**。
库中 5 条产出全是 `drill-` 前缀演练假数据（`model_version='drill-model'`、`prompt_version='drill-prompt'`），
另有 1 个同源 `risk_identification` 评测集。**平台本就没有 issue_tracking 能力。**

### 1.5 三套口径并存

| # | 口径 | 项数 | 真值来源 |
| --- | --- | --- | --- |
| ① | 业务能力 | 8 类 | `capability_registry.BUSINESS_CAPABILITY_STAGES` |
| ② | 评测集可选类型 | **9 项** | `EvaluationSuite.TASK_TYPE_CHOICES` |
| ③ | **Agent** | **5 个** | 本次已定 |

评测集库里已实际用到 8 种（含 `knowledge_query`、`code_review`、`risk_identification`）。

### 1.6 `code_review` 去掉自进化的代价很小
- 数据面：产出 4、评测集 2、反馈 3。
- 复合进化通路**试过但没成**：2 条 `kind='prompt'` 的 release 状态都是 **`rolled_back`**，无 active。

### 1.7 产出→版本的写入能力已具备，只是没人传
`services.record_task_output(..., skill_version=)` 逻辑正确（写 `skill_version_id` + `skill_package_sha256`），
但实测 **0/32 条有值**，原因是**调用方从不传**——上游是"阶段↔Skill 包没有显式绑定"。

---

## 2. 待决策问题

### A 组：口径收敛与清理

**A1. `code_review` "去掉"的具体范围？**
- 从 `Agent` 口径去掉 —— **已定**
- 是否同时：① `is_evolvable('code_review')` 改为 `False`？② 从 `COMPOSITE_CAPABILITY_STAGES` 移除？
  ③ 从 `EvaluationSuite.TASK_TYPE_CHOICES` 移除？④ 2 条 `rolled_back` 的 prompt release 保留还是清？
- **我倾向**：①②③ 都做，④ 保留（已是终态、有审计价值）。

**A2. 评测集（`EvaluationSuite`）口径收敛为几项？**
- 选项 ①：5 个 Agent
- 选项 ②：**5 个 Agent + `code_review` = 6 项**（code_review 不自进化，但仍需回归评测）
- 选项 ③：保留 9 项，仅前端不展示（不推荐，脏数据会持续长）
- **我倾向**：取决于你——若 code_review 彻底退出飞轮，选 ①；若还留着做回归，选 ②。

**A3. 历史脏数据清不清？**
5 条 `drill-` 演练产出 + 1 个 `risk_identification` 评测集 + 2 条重复的 `case_review` 量表。
**建议先确认 T15/T16 演练脚本是否还依赖这些数据**（演练会重建，直接删可能导致测试失败）。

### B 组：三级评分框架（本轮重点）

**B1. 第三轨"人工"的职责到底是什么？**
图片写的是"**人工抽检 LLM 评分**，一致率不低于 85%"，但你上轮说的是"每个中间阶段 skill 的产物都要开放人工评分入口"。
- 选项 ①：人工**只做抽检核对**（判断 LLM 的分对不对），不独立打分 —— 完全按图片
- 选项 ②：人工**独立打分**（作为金标准），LLM 向它对齐
- 选项 ③：两者都要 —— 常态抽检核对；遇到争议样本时人工独立打分定案
- **这条不定，第三轨没法设计。** 我倾向 ③，但需要你确认。

**B2. 抽检比例怎么定？**（图片未给）
- 按产出数固定百分比（如 10%）
- 按版本：新版本首批全检，稳定后降比例
- 按风险：只抽 L1 分接近阈值 / 陪审团分歧大的样本
- 我倾向**按风险抽样**（`JudgeResult.confidence` 与 jury 分歧度已有字段支撑），成本最低且命中最准。

**B3. "一致率 ≥ 85%" 怎么算？**
- 人工分与 L1 分在**容差内**算一致（容差多少？0.1？）
- 或**分档一致**（都判通过 / 都判不通过）即算一致
- 分母是抽检样本数，还是该版本全部样本数？
- 我倾向**分档一致**（避免容差争议），分母 = 抽检样本数。

**B4. 一致率不达标有什么后果？**
- 阻断该 skill 版本的 release（与"需要自进化时才评分"衔接，见 D 组）
- 仅告警并留痕
- 自动触发量表修订工单
- 我倾向：**阻断 release + 留痕**，因为图片说它是"确保结论可信的最后防线"。

**B5. 每类 Agent 的评分标准谁来写、何时写？**
现状只有 `case_review` 一套量表（还重复录了 2 条、`version` 为空），其余 4 个 Agent 阶段为零。
- 选项 ①：先补齐 5 套量表，再做人评分入口
- 选项 ②：入口先做（通用框架），量表陆续补
- **我倾向 ①**：没有量表，人工抽检没有"对照物"，入口做出来也是空转。

**B6. 一套量表要同时喂给三轨，切分规则是什么？**
`EvaluationRubric` 里 `required_items` / `forbidden_items` 天然对应第一轨（可形式化），
`dimensions` 天然对应第二轨（需语义理解）。
- 问：谁来判定某一项"可形式化"？是量表作者手工标注，还是平台自动判定？
- 现有 `rubric_rule` 的 `required_items` 是**字面匹配** `content.lower()`（已知缺陷），
  量表词汇必须与产出键完全一致，否则固定低分 —— **第一轨与第二轨的衔接要不要重新设计？**

**B7. "校准数据回流优化评分 Prompt" 怎么落地？**
现在 `EvaluationRubric.version` 是版本化的，但**为空**。
- 回流指什么：把人工的修正结果作为 few-shot 喂回 LLM 裁判的 Prompt？
- 谁改 Prompt、改完怎么验证不退化（要不要"新量量表先影子跑"）？
- 这块目前**完全没有实现**，是新增能力，需要你给方向。

**B8. 人工评分要不要分值？还是只要"一致/不一致"？**
若要分值，量纲需与 L1 对齐（0–1？0–100？）；若只做核对，则不需要分值字段。
（与 B1 联动）

### C 组：Agent–项目–Skill 版本关联

**C1. 版本绑定在哪一步完成？**
- 选项 ①：产出生成时由调用方传 `skill_version`（能力已具备，需改各业务模块调用点）→ **我倾向这个**
- 选项 ②：入库后后台任务按"当前活跃版本"补绑（**有风险**：补的是事后版本，跨版本链路溯源就错了）
- 选项 ③：上传/激活时人工指定

**C2. 业务阶段 ↔ Skill 包要不要显式绑定？**
现在**完全没有**——33 条 `CapabilityRelease` 无一条名字对应业务阶段，现有 26 个 Skill 全是工具型。
- 建议建显式映射（阶段 → 默认包），否则 C1 的"传哪个版本"无从谈起。

**C3. 四阶段 Skill 包的发布顺序**
现状：`test-case-clarity-review`（用例审查）**active**；`webtest-case-generator`、
`webtest-execution-runner` **shadow**；`webtest-plan-generator`、`webtest-report-generator` **未发布**。
- 问：`shadow → active` 的门禁条件是什么？（现在评测数据基本为空，靠什么判它够格上线？）

**C4. 一个阶段能否同时存在多个可用版本？**
现有约束 `uniq_active_release_per_target` 限制每 target 一个 `active`。
灰度与 A/B 靠 `shadow` 并行，还是需要放开约束？

### D 组：自进化触发与门禁（"需要自进化时才评分"）

**D1. 触发点是哪个动作？**
- 申请 `CapabilityRelease`（promote）时
- 发起优化候选（`OptimizationProposalService.generate`）时
- 两者都要

**D2. 触发时校验什么？**
例如"该 skill 版本有 ≥N 条人工校准且一致率 ≥85%"。**N 是多少？** 一致率是看阶段整体还是该版本？

**D3. 现有硬约束要不要放宽？**
现在 `GoldVersionService.freeze` 要求**全部 case 为 confirmed**；
`CapabilityReleaseService.promote` 要求 `awaiting_approval` + 门禁 `passed`。
既然"没评分也能往下走"，这些约束里哪些属于"评分"、需要改成软条件？

**D4. 四阶段每阶段单独评分——那"整条流程合格"还判不判？**
- 全部阶段都合格才算流程合格
- 加权（各阶段权重，现有 `evolution.py` 里就有权重表）
- 不做流程级判定，只看阶段

### E 组：优先级

**E1. 先做哪件？** 我的建议顺序：
1. **定 A1/A2/B1/B3/B5**（纯口径与量表，零成本；不定就没法动手）
2. **补齐 5 套 `EvaluationRubric` 量表**（第三轨的对照物，也是第一、二轨的输入）
3. **C2 阶段↔包映射 + C1 产出记版本**（不做这两步，Agent 台账永远是空的）
4. **第三轨人工校准入口 + 一致率计算**（前后端都要新做）
5. 最后 C3/C4 发布与灰度策略

---

## 3. 当前最大的风险

**Agent 版本实例台账依赖三样现在都不存在的东西**：
① 阶段与 Skill 包的显式绑定、② 产出记录所用版本、③ 人工校准（第三轨）。
**外加一个常被忽略的前置**：5 套评分量表目前只有 1 套（还重复录了），
没有量表，三轨里有两轨都无处对照。
