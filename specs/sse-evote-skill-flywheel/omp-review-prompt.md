# oh-my-pi 方案审查任务

你是本方案的独立架构审查者。请只读审查，不修改工作区文件。

## 目标

审查 `specs/sse-evote-skill-flywheel/requirements.md` 是否足以指导“上证 e 投票全流程 Skill 数据飞轮”的后续设计。方案需要同时支持：

1. 历史需求 + 历史真实方案/用例的回放对照；
2. 当前四阶段产出经人工标注或修改后上传，形成反馈并优化 Skill；
3. 需求管理、LLM 对话、测试管理和数据飞轮共用同一条可追溯链路。

## 必须核对的代码事实

- 四阶段真值与能力分类：`WHartTest_Django/knowledge_evolution/capability_registry.py`
- 产出与反馈模型：`WHartTest_Django/knowledge_evolution/models.py`
- 金标模型：`WHartTest_Django/knowledge_evolution/gold_models.py`
- 版本锁与阶段门禁：`WHartTest_Django/knowledge_evolution/workflow_models.py`
- 阶段启动与执行参数：`WHartTest_Django/knowledge_evolution/operations.py`
- Agent 对话产出回写：`WHartTest_Django/orchestrator_integration/agent_loop_view.py`
- 测试执行的 workflow/source_output 接入：`WHartTest_Django/testcases/models.py`、`views.py`、`tasks.py`
- 已有人工反馈闭环：`WHartTest_Django/knowledge_evolution/case_review_evolution.py`
- 已有阶段报告反馈：`WHartTest_Django/knowledge_evolution/workflow_feedback.py`
- 既有公共规格：`specs/platform-flywheel-integration/requirements.md`、`specs/evolution-assisted-loop/requirements.md`

## 审查优先级

请重点攻击以下失败模式：

1. 历史真实产物被错误视为绝对金标，导致错误知识强化；
2. 优化样本泄漏进隐藏评测集，使候选版本虚假提升；
3. 普通聊天草稿被误记为正式阶段产出；
4. 入口未传 `workflow_id/module_key/source_output`，产出无法归因；
5. Skill、Prompt、模型或模板版本未锁定，历史任务不可复现；
6. 单阶段指标提升但端到端质量退化；
7. 飞轮写入失败只记日志，形成永久数据缺口；
8. 人工上传动作顺带派生或激活版本，越过治理边界；
9. 业务专属字段污染公共协议，后续无法复用；
10. 重跑、替换产出、并行分支和跨阶段回退语义不清。

## 输出格式

请输出中文 Markdown，严格按以下结构：

1. `结论`：可进入设计 / 补充后进入设计 / 不可进入设计。
2. `阻断项`：每项给出涉及的需求章节、代码事实和建议改写；没有则写“无”。
3. `重要风险`：不阻断需求确认，但必须在设计阶段解决。
4. `可延期项`：明确可放到第二期的内容及延期条件。
5. `建议的最小一期`：给出最小闭环，不超过 6 个能力点。
6. `建议改写`：提供可直接合入 requirements.md 的精确条款。
7. `待用户裁决`：只列真正改变产品行为、无法从代码事实推导的决策。

不要输出通用 AI 建议；每项必须能落到本项目的模型、接口、阶段或验收标准上。
