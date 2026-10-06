---
name: 上证e投票测试方案生成
description: 面向上证e投票平台，将需求文档、原型和页面探索结果转化为矩阵式测试方案，明确覆盖范围、策略、资源、风险和待确认项。
version: 1.1.0
stage: test_plan_generation
entrypoint: SKILL.md
input_schema: schemas/input.json
output_schema: schemas/output.json
# 本 Skill 对平台产出协议的**声明等级**（stage-result/v1）。平台会拿它与实际产出
# 实测出的等级取较低的一档使用：声明了 L3 但产出只有 L2 的内容，平台按 L2 用，
# 不会拿它做归因与补丁派生。
stage_result_level: L3
stage_result_schema: schemas/stage_result.json
permissions:
  - filesystem_read
  - filesystem_write
  - browser_read
---

# 上证e投票平台测试方案生成

本 Skill 承担四阶段测试链路中的**方案生成阶段**：分析需求与既有资产，必要时探索页面，再生成团队矩阵格式的测试方案。

## 输出协议（L3）

除业务方案外，本 Skill **必须**额外提交 `stage_result.json`，遵循平台协议 `stage-result/v1`，
并按本包的 `schemas/stage_result.json` 收紧。平台凭它做差异比对、证据反查与归因派生；
没有它，或它不合规，本次产出在平台上会被标记为「结构化协议失败」——
**业务方案仍然保留可下载**，但不会进入进化链路。

`stage_result.json` 的定位是**机器可读的索引**，不是方案的替代品：方案正文照旧写在
`test_plan.xlsx` 里，信封只回答"这份方案包含哪些项、每项依据什么、为什么这么定"。

必填与口径：

- `primary_artifacts`：至少含 `test_plan.xlsx`，并给出其 `sha256`。
- `requirements`：本次方案依据的需求清单（`REQ-xxx`）。
- `items`：**每个末级方案项一条**，`id` 形如 `TP-001`、同一次产出内唯一且稳定；
  每项必须带 `requirement_ids`、`evidence_ids`、`module`、`scenario_type`、
  `priority`、`expected`。
- `evidence`：知识原句引用，`id` 形如 `EV-001`；每条必须带 `document_id`、
  `document_version`、`chunk_id`、`quote`，能回到具体文档版本的具体片段。
- `decision_summary`：显式结构化决策摘要（覆盖取舍、策略选择、回归裁剪理由）。
- `assumptions` / `uncertainties` / `counterevidence`：假设、待确认项、反证检查。
  查过但没有反证也要写 `[]`，不要省略——省略无法区分"没查"和"查了没有"。

不写的内容：模型隐藏思维链、工具调用流水（由 Runtime 记录）、任何人工结论字段
（人工结论、修改类型、修改内容、备注一律留空，归平台确认报告）。

## 输入与前置条件

- 至少提供需求文档/原型，或已探索的页面结构；有历史测试方案和用例时必须一并参考。
- 明确迭代标识、需求简称、目标子系统、测试环境、里程碑日期、执行人和数据准备信息；缺失内容列入待确认，不编造。
- URL 登录凭据只用平台托管引用；不写入文件、日志或输出。

## 执行流程

1. 阅读 `references/RequirementAnalysis.md`，解析需求、业务规则、验收标准、影响子系统、数据链路及待确认项。
   解析结果要为每条需求分配 `REQ-xxx` 标识，供 `stage_result.json` 引用。
2. 如需求需要页面核验，按 `references/PageExplorer.md` 使用 `templates/page_explorer.py` 探索；先读 `websiteFeature/` 复用特征。小程序、现场工具、无界面服务不可用浏览器探索时，明确标注 UI 覆盖限制。
3. 对照 `references/投票业务规则库.md`、`references/回归清单.md` 和历史测试资产，建立需求到测试范围的追溯。重点检查股权处理白名单反向验证、采编到下游数据链路、七大通道、开关/状态组合、多端差异。
4. 按 `references/TestPlan.md` 使用 `templates/测试方案_template.xlsx` 填充矩阵式测试方案，保留模板表结构、字段、合并单元格与格式。
5. 覆盖各功能点的正向与反向/边界场景；涉及数据、消息或文件时写明可观察表/字段/消息/文件名判据；末尾写按本次变更裁剪后的回归范围。
6. 对执行资源与工时不确定项标记待确认；未覆盖的浏览器、设备、系统链路和外部依赖写入风险，不得写"无"掩盖缺口。
7. **按 `references/TestPlan.md` 的「稳定 ID 与证据引用」一节生成 `stage_result.json`**：
   为每个末级方案项编号、挂需求与证据、写可判定预期，并记录决策摘要、假设、
   待确认项与反证检查。
8. **运行自检并据结果修正**（自检不过就不要声称已完成）：

   ```bash
   python scripts/validate_stage_result.py stage_result.json \
       --corpus corpus.json --artifacts-dir .
   ```

   `corpus.json` 是本次实际用到的知识文档索引（`document_id` / `document_version` /
   `chunk_id` / 片段正文）。退出码非 0 时逐条修掉报出的字段问题再重跑。
9. 输出 `test_plan.xlsx`、`stage_result.json`、需求分析结构化结果、页面探索结构（若执行），以及供后续阶段消费的方案项列表和待确认问题。

## 产出要求

- 方案表包含测试里程碑、需求内容、测试策略、测试范围、测试资源、测试风险六大模块。
- 测试范围按需求/模块/测试点分层，层级不超过四级；可直接映射到后续用例模块。
- 每个末级方案项需有稳定 ID、需求来源、模块、场景类型、优先级和可判定说明。
- 明确覆盖率、未覆盖项与阻塞条件；方案质量门禁未通过时不得假装已完成或驱动下一阶段。
- 业务规则与素材冲突时保留证据并列出待确认问题。

## 文件

- `test_plan.xlsx`：团队矩阵式测试方案（业务主产物）。
- `stage_result.json`：平台产出协议信封（L3）。
- `corpus.json`：本次引用的知识文档索引，供 `scripts/validate_stage_result.py` 定位原句。
- `analysis/requirements_parsed.json`：需求解析结果。
- `analysis/page_structure.json`：页面探索结果（如适用）。
- 结构化方案项、覆盖统计和待确认项。
