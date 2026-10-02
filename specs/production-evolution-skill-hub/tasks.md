# 生产级质量自进化与 Skill Hub - 实施任务

## 0. 执行规则

- 状态：`[ ]` 未开始、`[~]` 进行中、`[x]` 已完成、`[!]` 阻塞。
- 每个任务只有在代码、迁移、自动化测试和文档证据均完成后才能标记为 `[x]`。
- 所有接口必须强制项目隔离；所有发布动作必须审计；任何候选版本不得绕过评测和测试负责人审批直接进入生产。
- 每完成一个任务同步更新本文件的“实施记录”，不得仅凭页面可见或接口返回 200 判定完成。
- 四阶段唯一顺序：`测试方案 -> 测试用例 -> 测试执行 -> 报告生成`。
- 代码审查属于复合能力发布，不得包装成 Skill；全部 Diff 文件审查策略不得被候选配置关闭。

## 1. 基础模型与安全边界

- [x] **T01 修复 Skill 项目隔离与角色授权**（**读侧口径已于 2026-10-02 / T27 修订，见本节末"修订"**）
  - 依赖：无。
  - 修改：`WHartTest_Django/skills/views.py`、权限类和序列化器；列表、详情、导入、上传和后续版本接口按 URL 中的 `project_id` 过滤（**"列表、详情按 `project_id` 过滤"这半句已于 2026-10-02 / T27 修订**：读侧改为公共目录，写侧不变）。
  - 角色：测试负责人可以审批、激活、回滚和隔离；测试执行人员可以上传、预检、评测和反馈；基础 Owner/Admin/Member 不在业务界面直接展示。
  - 验收：跨项目**写**操作（上传、预检、候选、审批、激活、回滚、隔离、下载）、猜测 UUID、状态变更返回 403/404；**跨项目读取（`list`/`retrieve`）改为公共目录，不再返回 403**（2026-10-02 / T27 修订）；同项目授权矩阵测试通过。
  - 对应需求：R10、R12。
  - **修订（2026-10-02 / T27）**：Skill Hub 是**平台级公共目录**，不分项目 —— `list`/`retrieve` 改为 `IsAuthenticated` + 全量查询集。同名 Skill 在多个项目各存一份时，列表**按名称归并**只展示"正本"（优先有活跃版本，其次版本数多，最后 id 小，保证可复现），以 `copies` 暴露副本数、`stage_source` 暴露阶段来源；`Skill.declared_stage` 供管理员补填 manifest 未声明的阶段（写入口 `POST .../stage/`，权限 `IsTestLeadAnywhere`）。**写侧边界未放宽**：`destroy`/`upload`/`toggle`/`preflight`/`candidate`/`activate`/`rollback`/`quarantine`/`download` 仍锚定 URL 项目。用例：`skills/tests_public_catalogue.py`（22 项）+ `skills/tests_isolation.py`（读侧 4 条改写为公共目录口径，写侧 10 条不变）。

- [x] **T02 建立 SkillVersion 不可变版本模型**
  - 依赖：T01。
  - 修改：`WHartTest_Django/skills/models.py`、序列化器、管理后台和迁移。
  - 内容：新增 `SkillVersion`；为 `Skill` 增加 `capability`、`active_version`；建立版本、哈希、来源、父版本、manifest、校验报告和审计字段。
  - 验收：`Unique(skill, version)`、`Unique(skill, package_sha256)` 生效；已入库版本内容不能原地覆盖。
  - 对应需求：R2、R4。

- [x] **T03 统一发布状态机和并发唯一激活约束**
  - 依赖：T02。
  - 修改：`WHartTest_Django/knowledge_evolution/capability_models.py`、迁移和发布服务。
  - 内容：扩充 `CapabilityRelease` 的 `skill/composite` 类型及生命周期；通过事务行锁和数据库约束保证一个 Skill 只有一个 active 版本。
  - 验收：非法状态跳转被拒绝；两个并发激活请求最多一个成功；失败事务不留下半激活状态。
  - 对应需求：R2、R7、R9。

- [x] **T04 迁移存量 Skill 为初始版本**
  - 依赖：T02、T03。
  - 修改：数据迁移和可重复执行的管理命令。
  - 内容：为现有 Skill 生成 `0.0.0-migrated` 版本、manifest、包哈希和 active 绑定，保留原文件与调用兼容。
  - 验收：迁移前后存量业务输出一致；重复执行不产生重复版本；无可用文件的记录生成明确异常报告。
  - 对应需求：R13。

## 2. Skill 包管理与运行时

- [x] **T05 实现 Skill 包预检与安全扫描**
  - 依赖：T02。
  - 新增：包校验服务及短期预检令牌。
  - 内容：Zip Slip、符号/硬链接、文件数、体积、路径、UTF-8、manifest、Schema、入口、权限声明、密钥和高熵凭据扫描。
  - 验收：恶意 ZIP、超限包、缺失 manifest、明文密钥和哈希篡改均被自动化测试拦截；预检不写正式版本库。
  - 对应需求：R1。

- [x] **T06 实现候选创建、版本 diff 与来源追踪**
  - 依赖：T03、T05。
  - 修改：上传、Git 导入、商店导入服务统一调用 `preflight -> create_candidate`。
  - 内容：保存不可变包、文件级 diff、来源提交号/校验和、父版本、变更原因和回滚目标；重复包幂等返回。
  - 验收：三种来源创建行为一致；包内容变化必然生成新哈希；重复请求不重复落库。
  - 对应需求：R1、R2、R6。

- [x] **T07 实现确定性脱敏下载与隔离**
  - 依赖：T05、T06。
  - 内容：稳定排序和时间戳的 ZIP、流式响应、下载前二次敏感扫描、审计日志；风险包转 `quarantined` 并禁止下载和执行。
  - 验收：相同版本重复导出字节和 SHA-256 一致；重新上传可恢复相同定义；凭据、缓存、日志和运行产物不进入包。
  - 对应需求：R3、R12。

- [x] **T08 实现 SkillRuntimeResolver 与运行锁**
  - 依赖：T03、T04。
  - 修改：新增 `WorkflowSkillLock`，为统一产出补充 `skill_version` 和包哈希元数据。
  - 内容：按项目和能力解析 active 版本；任务创建时固化版本，拒绝跨项目、非 active、隔离或哈希不一致版本。
  - 验收：新版本激活不改变运行中任务；解析 P95 小于 50ms；解析失败阻止新任务但不污染原业务状态。
  - 对应需求：R4、R8、非功能需求。

## 3. 评测、审批与自进化内核

- [x] **T09 统一能力评测分区与硬门禁**
  - 依赖：T03。
  - 修改：现有 `EvaluationSuite/Run/Result` 服务。
  - 内容：金标、回归、新鲜、挑战、隐藏和影子分区；统一质量、漏报、误报、Token、耗时及稳定性指标；保存不可变门禁快照。
  - 验收：缺少评测集、硬门禁失败或基线不可比时不能提交审批；重复评测可追溯且不覆盖历史。
  - 对应需求：R5、R7。

- [x] **T10 完成反馈、Badcase 与金标分类闭环**
  - 依赖：T02、T09。
  - 内容：反馈精确绑定产出、能力发布和 SkillVersion；按八类业务能力和评测分区组织金标；证据不完整时禁止升级。
  - 验收：采纳、驳回、人工编辑、确认缺陷、误报、漏报、执行结果均能回流；金标详情可追溯到原任务和审核人。
  - 对应需求：R5。

- [x] **T11 完成多层失败归因与反证确认**
  - 依赖：T10。
  - 内容：意图、规划、Prompt、知识、检索、Skill/工具、执行环境、下游结果分层归因；记录支持证据、反证、置信度和人工确认。
  - 验收：未经确认的归因不能生成可发布候选；下游失败可以通过父产出追溯上游版本。
  - 对应需求：R6、R8。

- [x] **T12 实现 Skill 候选自进化服务**
  - 依赖：T06、T09、T11。
  - 内容：仅对确认属于指令、脚本、模板或允许配置的问题派生候选；执行静态校验、独立评测、影子对比并输出可读 diff、收益和风险。
  - 验收：不会修改 active 包；不允许从无证据反馈直接产包；候选失败不影响生产版本。
  - 对应需求：R6、R7。

- [x] **T13 实现审批、激活、观察和自动回滚**
  - 依赖：T03、T09、T12。
  - 内容：提交审批、负责人批准/驳回、原子激活、生产观察、阈值告警和自动回滚；全链路幂等及审计。
  - 验收：执行人员不能激活；审批引用的评测快照变化后必须重新审批；越过观察阈值能恢复上一版本。
  - 对应需求：R7、R12。

## 4. 真实业务接入

- [x] **T14 接入用例审查单次自进化闭环**
  - 依赖：T08、T10、T13。
  - 内容：任务启动锁定用例审查 SkillVersion；产出、反馈、金标、评测和候选均绑定该版本。
  - 验收：能从一条真实用例审查完成“反馈 -> 金标 -> 归因 -> 候选 -> 评测 -> 审批 -> 激活/回滚”。
  - 对应需求：R4-R7。

- [x] **T15 接入四阶段 Skill 质量门禁主链路**
  - 依赖：T08、T09、T13。
  - 内容：测试方案启动时锁定四阶段版本；方案、用例、执行、报告逐阶段检查上一门禁，支持负责人留痕放行。
  - 验收：门禁未通过不能进入下一步；链路中途激活新版本不改变锁；页面和 API 状态一致。
  - 对应需求：R8。

- [x] **T16 完成报告生成和端到端归因评测**
  - 依赖：T11、T15。
  - 内容：报告引用方案、用例、执行产出；校验覆盖率、通过率、失败分布和未闭环问题；同时生成阶段评测与端到端评测。
  - 验收：缺失上游引用或 Schema 不合法时报告门禁失败；下游 Badcase 能定位责任阶段和 SkillVersion。
  - 对应需求：R8、完成定义 1-2。

- [ ] **T17 完成代码审查复合能力自进化**
  - 依赖：T03、T09、T10、T11、T13。
  - 内容：版本化 Prompt、机器规则、CRG/检索、反证核验和工具配置；按归因只生成对应子单元候选；任务锁定复合 Release。
  - 验收：同一仓库/commit/全部 Diff 下完成基线与候选对比；覆盖策略不可关闭；支持审批、激活、观察和回滚且不生成 Skill ZIP。
  - 对应需求：R9。

## 5. Skill Hub 与生产验收

- [x] **T18 开发 Skill Hub 生产控制台**
  - 依赖：T01、T06、T07、T09、T13。
  - 修改：`WHartTest_Vue/src/features/skills/` 及路由、API 服务。
  - 布局：左侧 300px 能力目录，中部版本/diff/评测，右侧 360px 治理操作；沿用平台浅色蓝、灰、绿、橙 token。
  - 功能：上传预检、Git/商店导入、下载、版本对比、指标对比、审批、激活、隔离、回滚、审计和能力绑定。
  - 验收：只显示测试负责人和测试执行人员；禁用操作明确说明缺失条件；响应式和空/错/加载状态完整。
  - 对应需求：R10、R11。

- [ ] **T19 完成自动化测试、安全回归与性能验证**
  - 依赖：T14-T18。
  - 内容：Django 单元/接口/迁移/并发/安全测试，Vue 单元与端到端测试，四阶段和代码审查真实链路回归。
  - 验收：覆盖跨项目越权、恶意包、并发激活、回滚、失败补偿、版本锁、全部 Diff；记录 Token、耗时、质量与版本解析 P95。
  - 对应需求：非功能需求、完成定义 5-7。

- [ ] **T20 完成生产演练、文档、迁移与发布**
  - 依赖：T19。
  - 内容：备份和迁移演练、回滚演练、权限核对、监控告警、操作手册、验收记录；提交中文 Git commit 并推送当前分支。
  - 验收：使用真实项目完成一次用例审查 Skill 闭环、一次四阶段闭环和一次代码审查复合闭环；所有证据写回本文件；无未说明失败项。
  - 对应需求：完成定义全部条目。

## 6. 依赖路径

```text
T01 -> T02 -> T03 -> T04
          |      |      \
          v      v       -> T08 -------------------> T14
         T05 -> T06 -> T07                           |
                  |                                  v
T03 ------------> T09 -> T10 -> T11 -> T12 -> T13 -> T15 -> T16
                               \                \
                                --------------------> T17
T01 + T06 + T07 + T09 + T13 ----------------------> T18
T14 + T15 + T16 + T17 + T18 ----------------------> T19 -> T20
```

## 7. 实施记录

| 日期 | 任务 | 状态 | 验证证据 | 备注 |
|---|---|---|---|---|
| 2026-10-01 | T01 修复 Skill 项目隔离与角色授权 | [x] | `skills/tests_isolation.py` 14 项通过；真实库 DRF 栈复核：项目成员本项目 200 / 他项目 403 / 在自有项目路径猜他项目 UUID 404；非成员全 403；超管可跨项目；未认证 401。**（读侧 4 条已于 2026-10-02 / T27 改写为公共目录口径；写侧 10 条不变）** | 新增 `projects/roles.py` 作为业务角色唯一真值；`skills/views.py` 的 `get_queryset()` 由 `Skill.objects.all()` 改为强制按 `project_pk` 过滤。**（2026-10-02 / T27 修订：`get_queryset()` 拆为读侧全量 + 写侧按 `project_pk`）** |
| 2026-10-02 | T27 Skill 公共目录与阶段补填 | [x] | `skills/tests_public_catalogue.py` 22 项通过；`skills` + `knowledge_evolution` + `testcases` 合并回归通过；`makemigrations --check` → `No changes detected` | 读侧公共化（`list`/`retrieve` 全量 + 按名称归并正本）；新增 `skills/canonical.py`、`Skill.declared_stage`（迁移 `skills/0005`）、`POST /projects/{id}/skills/{id}/stage/`（`IsTestLeadAnywhere`）；`knowledge_evolution/task_binding.py` 两处阶段查找改为公共池 + 正本 |
| 2026-10-01 | T02 建立 SkillVersion 不可变版本模型 | [x] | `skills/tests_versioning.py`：`uniq_skill_version` 与 `uniq_skill_version_package` 约束生效；换版本号复投同内容被拒；无 release 时状态回落 draft 且不可运行（**"不可运行"这半句已于 2026-10-02 / T25 修订**：`draft` 现在**可运行**，见 requirements §R13.1） | 迁移 `skills/0003`；`Skill` 新增 `capability`、`active_version`；包哈希权威实现落在 `skills/packaging.py` |
| 2026-10-01 | T03 统一发布状态机与并发唯一激活约束 | [x] | 非法跳转/可达性/并发唯一激活（数据库部分唯一约束）/回滚/隔离/指针一致性测试通过；`knowledge_evolution` 173 项回归仅剩 1 项既有失败（与任务无关，见第 8 节第 4 条） | 迁移 `knowledge_evolution/0023`；`kind` 增 `composite`，新增状态 `validating`/`quarantined`；新增 `uniq_active_release_per_target` 兜底并发激活 |
| 2026-10-01 | T04 迁移存量 Skill 为初始版本 | [x] | 本地库迁移输出：16 条 Skill 全部生成 `0.0.0-migrated` 版本（包完整 13 条 `active`、仅内联 3 条 `draft`）；重跑幂等（复用 16 条，总数不变）；`migrate skills 0003` 回滚后原 Skill 记录与文件完好 | 迁移 `skills/0004`；迁移前备份 `backups/wharttest_pre_skillhub_20261001_162935.dump` |
| 2026-10-01 | T05 实现 Skill 包预检与安全扫描 | [x] | `skills/tests_validation.py`（`ValidPackageTests`/`ArchiveSecurityTests`/`ManifestValidationTests`/`SecretScanningTests`/`PlaceholderTests`/`PreflightTokenTests`/`PreflightMediaRootTests`）通过；真实库验收：合法包预检 200、Zip Slip 包 400、明文密钥包 400、非项目成员预检 403、预检后正式版本库 `versions=0` | 新增 `skills/validation.py`；限额 2000 文件 / 50MB / 单文件 20MB / 路径 200 字符 / 12 层 / 压缩比 200；`preflight` 与 `create_candidate` 两个接口落在 `skills/views.py` 的 `detail=True` 路由上 |
| 2026-10-01 | T06 实现候选创建、版本 diff 与来源追踪 | [x] | `skills/tests_versions.py`（`CandidateCreationTests`/`VersionDiffTests`/`VersionLifecycleTests`）通过；真实库验收：令牌创建候选 201、新候选一律 `draft`、未激活时 `active_version` 为空、重复包幂等命中同一版本（同一 UUID）、版本列表与 diff 可读、diff 正确识别 `SKILL.md` 修改 1 未变 1 | 新增 `skills/versions.py`；上传 / Git 导入 / 商店导入三来源统一收敛到 `SkillVersionService.create_candidate_from_dir` |
| 2026-10-01 | T07 实现确定性脱敏下载与隔离 | [x] | `skills/tests_exporter.py`（`DeterminismTests`/`ContentExclusionTests`/`RedactionTests`/`ReimportTests`/`LeakQuarantineTests`/`ResponseAndAuditTests`）通过；真实库验收：下载 200、导出字节与 `X-Export-Sha256` 自洽、同版本两次导出字节完全一致、导出包重新上传幂等命中同一版本、非成员下载 403、测试执行人员隔离 403 | 新增 `skills/exporter.py`；`quarantine_version` 已加入 `LEAD_ONLY_ACTIONS` |
| 2026-10-01 | T08 实现 SkillRuntimeResolver 与运行锁 | [x] | `skills/tests_runtime.py`（`ResolveActiveVersionTests`/`TaskLockTests`/`PerformanceTests`/`OutputProtocolTests`）通过；真实库验收：按项目+名称解析到活跃版本、任务创建固化版本、已启动任务取回锁定版本、隔离后不再解析出新任务可用版本、隔离后锁定任务被拒、跨项目解析返回 `None` 不串包 | 新增 `skills/runtime.py` 与 `KnowledgeEvolution/workflow_models.py::WorkflowSkillLock`；`GenerationOutput` 增 `skill_version` + `skill_package_sha256`（迁移 `knowledge_evolution/0024`） |
| 2026-10-01 | T05–T08 合并回归 | [x] | `python manage.py test skills` → **166 项全部通过（OK，33.4s）**；`python manage.py test knowledge_evolution` → 173 项仅剩 1 项既有失败（`test_testcase_generation_agent_output_is_traceable`，见第 8 节第 4 条，经 `git diff HEAD` 确认属工作区既有未提交改动，非本批引入）；`makemigrations --check` → `No changes detected`；`showmigrations` 确认 `skills 0001–0004`、`knowledge_evolution 0001–0024` 全部已应用 | 真实验收脚本 `WHartTest_Django/scripts/verify_t05_t08_live.py`，**25 项全通过**；迁移前备份 `backups/wharttest_pre_t05t08_20261001_165034.dump`（36MB） |
| 2026-10-01 | T09 统一能力评测分区与硬门禁 | [x] | `knowledge_evolution/tests_t09_t13.py` 的 `PartitionRegistryTests` / `MetricsAndPartitionTests` / `GateServiceTests` / `UpstreamTracingTests` 通过；迁移 `knowledge_evolution/0025`（含 `EvaluationGateSnapshot`）已应用；`makemigrations --check` → `No changes detected` | 新增 `evaluation_gates.py` + `gate_models.py`；五分区 + shadow 派生项；门禁快照 `save()` 拒绝二次写入，`Unique(candidate_run, kind, content_hash)` 保证重复门禁可追溯不覆盖 |
| 2026-10-01 | T10 完成反馈、Badcase 与金标分类闭环 | [x] | `FeedbackBindingTests` / `GoldClosureTests` 通过；`BUSINESS_CAPABILITY_STAGES` 实测恰为 8 类：`('case_review','test_plan_generation','testcase_generation','test_execution','report_generation','code_review','risk_identification','issue_tracking')`，`knowledge_query` 显式排除在业务能力之外 | 新增 `capability_registry.py`；`FeedbackEvent` 增 `capability`/`release`/`skill_version`/`evidence`/`capability_kind`（迁移 `0025`）；`from_feedback` 对证据、能力一致性做硬校验，`GoldCatalogService.overview` 分能力级/数据集级两层报缺口 |
| 2026-10-01 | T11 完成多层失败归因与反证确认 | [x] | `AttributionLayerTests` / `UpstreamTracingTests` 通过；8 层 `LAYER_ORDER` 与 `CATEGORY_CHOICES` 全集一致；`trace_upstream_versions()` 可从下游产出回溯上游版本；`NON_OPTIMIZABLE_CATEGORIES` 对 `environment_error` 显式报错而非 `KeyError` | `FailureAttribution` 增 `layer`（迁移 `0027`）与 `CATEGORY_LAYER_MAP` 唯一真值；`decide()` 留痕；`layering_overview()` 供页面聚合 |
| 2026-10-01 | T12 实现 Skill 候选自进化服务 | [x] | `DerivationGuardTests` / `DerivationTests` 通过：未确认归因被拒、非可修类别被拒并给出应走通道、派生不动 active 包（基线哈希与路径逐字节不变）、候选内确实写入受管护栏、同批归因重复派生被拒、派生候选版本号自动推进 | 新增 `knowledge_evolution/skill_evolution.py`；`SKILL_ADDRESSABLE_CATEGORIES` 由 `TYPE_BY_CATEGORY` 派生（实测 6 类：`downstream_execution_error`/`generation_error`/`intent_error`/`planning_error`/`prompt_error`/`tool_error`）；派生补丁确定性、版本推进对工作副本执行 |
| 2026-10-01 | T13 实现审批、激活、观察和自动回滚 | [x] | `ApprovalFlowTests` / `ObservationRollbackTests` / `AuditTrailTests` 通过：门禁未通过不能提交审批、审批快照变化后必须重新审批、越过阈值自动回滚并留审计、自动回滚失败转 `quarantined` | `capabilities.py` 新增 `submit_for_approval` / `assert_approval_evidence_current` / `reject` / `observe` / `reject_for_observations` / `approval_view`；`CapabilityRelease` 增 `approval_snapshot_hash`、`observation_state`（迁移 `0026`）；`KnowledgeAuditLog` 增 `submit_approval`/`approve`/`reject` 动作 |
| 2026-10-01 | T09–T13 合并回归 | [x] | `python manage.py test knowledge_evolution.tests_t09_t13` → **63 项全部通过（OK，27.9s）**；`python manage.py test knowledge_evolution` → **236 项仅剩 1 项既有失败**（`test_testcase_generation_agent_output_is_traceable`，经文件时间戳证据确认 `protocol.py`/`operations.py`/`agent_loop_view.py` 均为 2026-10-01 14:12–14:31 的**本次会话开始前**工作区改动，非本批引入，见第 10 节第 8 条）；`makemigrations --check` → `No changes detected`；`showmigrations` 确认 `knowledge_evolution 0001–0027`、`skills 0001–0004` 全部已应用；真实库复核 Skill 16 条 / SkillVersion 16 条 / active 13 + draft 3，与 T04 迁移后完全一致 | 迁移前备份 `backups/wharttest_pre_t09t13_20261001_172555.dump`（36MB）；门禁快照、运行锁、阶段门禁在真实库均为 0 条，确认无演练残留 |
| 2026-10-01 | T14 接入用例审查单次自进化闭环 | [x] | `knowledge_evolution/tests_t14.py` **16 项全部通过**；`testcases` + `orchestrator_integration` + `knowledge_evolution` 三应用回归 **437 项仅 1 项既有失败**；真实库 `scripts/real_badcase_closed_loop.py` 闭环复跑通过 | 新增 `knowledge_evolution/task_binding.py`（`TaskSkillBindingService.bind_case_review` 三分支策略）与 `lineage.py`（`OutputLineageService.trace` 八段溯源 + `broken_at`）；`testcases/review_service.py` 改为**先锁版本再改状态**，拒绝时任务停在 `pending`、不产生 running 孤儿 |
| 2026-10-01 | T15 接入四阶段 Skill 质量门禁主链路 | [x] | `knowledge_evolution/tests_t15.py` **22 项全部通过**；`knowledge_evolution.operations.WorkflowGateService.start_workflow/workflow_status` 与 `workflow-status/`、`start-workflow/` 接口实测一致；`start-workflow/` 负责人 201、执行人员 403；`workflow-status/` 成员可读 200、非成员 403 | `operations.py` 新增 `start_workflow`（启动即锁四阶段）、`workflow_status`（门禁+锁定版本唯一真值）、`ensure_stage_lock`、`STAGE_LABELS`；`protocol.OutputEnvelope` 增 `skill_version`/`skill_descriptor`；`skills/runtime.py` 支持按指定 Skill 解析。**整改**：`register_output` 对同一产出重复登记改为**幂等**（原会无条件把已通过门禁打回 `pending`）；驾驶舱版本来源由"当前活跃版本"改为"任务锁定版本" |
| 2026-10-01 | T16 完成报告生成和端到端归因评测 | [x] | `knowledge_evolution/tests_t16.py` **44 项全部通过**；四应用全量回归 `knowledge_evolution skills testcases orchestrator_integration` → **538 项全部通过（OK，79.4s，failures=0 errors=0）**；`makemigrations --check` → `No changes detected`；迁移 `knowledge_evolution/0028` 已应用，`psql \d knowledge_evolution_workflowstagegate` 确认 `detail jsonb NOT NULL`；真实库只读核验 `output/t16_real_check.py`：端到端评测对 4 条真实流水线正确报出 `coverage=0.25/0.75`、`missing_stages`、`open_gates`；责任定位对最近 5 条真实产出返回 `resolved=True/False` 与责任阶段（有已确认归因者定位到 `case_review`，无归因者返回 `resolved=False`、不给可用阶段名） | 新增 `knowledge_evolution/report_gates.py`（`ReportGateService` 契约校验 + `WorkflowEvaluationService` 阶段/端到端双评测）；`lineage.py` 新增 `ResponsibilityService.locate`；`WorkflowStageGate` 增 `detail` JSONField（迁移 `0028`）；`WorkflowGateService.evaluate` 对报告阶段**先过契约再谈分数**；新增接口 `evaluate-workflow/`、`responsibility/`。迁移前备份 `backups/wharttest_pre_t16_20261001_233003.dump`（38MB） |
| 2026-10-01 | T14–T16 合并回归 | [x] | `python manage.py test knowledge_evolution skills testcases orchestrator_integration` → **538 项全部通过（OK，79.4s）**，**failures=0 errors=0**——T09–T13 遗留的既有失败 `test_testcase_generation_agent_output_is_traceable` 与本批自身的 2 项口径回归均已修复，四项应用首次全绿 | 修复详情见第 11 节第 4、5 条；`knowledge_evolution` 单应用 44 + 22 + 63 + 既有用例全通过 |
| 2026-10-02 | T18 开发 Skill Hub 生产控制台 | [x] | `vue-tsc -b` 0 错误；`npm run build` ✓；后端 `python manage.py test knowledge_evolution.tests_t18 skills --noinput` → **205 项全部通过（OK，51.5s）**；真实浏览器三路径验收（容器内 chromium，项目=演示项目 id=1，截图见 `output/t18-console-shots/`）：① **测试负责人**（admin）三栏 `grid=1`、目录 15 项、版本轨道 1、治理 5 行、缺失条件 1 行，角色标签「测试负责人」，提交审批禁用「还缺：评测门禁」、激活禁用「当前状态「生产生效」不可直接激活」，隔离/回滚可点；② **测试执行人员**（t18-executor，零模型权限）角色标签「测试执行人员」、5 个治理动作**全部**禁用并给出「仅测试负责人可…」、提交审批禁用「还缺：评测门禁」、目录 15 项照常可读；③ **非本项目成员**（t18-nonmember）`grid=0`、`blocked=1`、`blockedError=0`，渲染「该控制台只对项目成员开放…」说明文案，无越权。三路径 `pageErrors=[]`、`console.error=[]`，且**网络日志中 `/members/` 调用次数为 0** | 新增后端自服务端点 `GET /projects/{project_pk}/skills/hub-access/`（`IsAuthenticated` + `IsProjectScoped`，返回调用者 `business_role`/`is_test_lead`/`is_test_executor`）；新增 `SkillVersionSerializer.release_id`；前端新增 `SkillHubConsole.vue` 及 `hub/` 三面板、`types/hub.ts`、`services/skillHubService.ts`、`composables/useSkillHubAccess.ts`；`SkillsManagementView.vue` 改为「生产控制台 / 存量管理」双页签。决策与偏离见第 12 节 |


## 8. 关键决策与偏离说明（T01–T04）

1. **`shadow` 承载双重语义**：平台既有灰度流程是 `awaiting_approval -> shadow -> active`
   （审批后进入灰度观察再激活），而设计文档状态图描述的是
   `validating -> shadow -> awaiting_approval`（校验后进入影子评测）。实现取**并集**，
   两条入边都合法，`approve_for_canary` / `complete_canary` 既有链路未被破坏。
   后续若要把两种语义拆成独立状态，需单独评估兼容性。
2. **`CapabilityRelease` 唯一约束粒度调整**：原 `(project, kind, version)` 会让同一项目下
   第二个 Skill 无法发布相同版本号（例如两个 Skill 都要发 `1.0.0`）。已改为
   `(project, kind, name, version)`，并新增 `uniq_active_release_per_target`
   （`condition = state='active'`）作为并发激活的数据库级兜底。
   退役范围同步由 `(project, kind)` 收窄为 `(project, kind, name)`，避免跨 Skill 误退役。
3. **内联型 Skill 处置**：现有 3 条 Skill（`webtest-generator`、`xingqihang-keji-pingjia-test`、
   `xingqihang-system-test`，均属「演示项目」）的包目录从未落盘（`data/media/skills/1/` 缺
   10/12/13），只有内联 `SKILL.md`。迁移为其生成 `draft` 占位版本并写入
   `manifest.needs_package_rebuild` 与 `validation_report`，**不创建任何文件、不激活**。
   需人工重建包目录后才能进入影子评测与激活。
4. **遗留既有失败（非本批引入）**：
   `knowledge_evolution.tests.KnowledgeEvolutionTestCase.test_testcase_generation_agent_output_is_traceable`
   在本次改动前即失败——`_record_module_output` 依赖 `request._flywheel_module_key`，
   而测试构造的 request 没有该属性。该文件在本次任务开始前已是工作区未提交改动状态。
5. **尚未接入的部分**（T05/T06 已完成，本节保留为 T01–T04 时点的快照）：
   Skills 的 `upload` / `import-git` / `import-zip-url` 当时的"直接建 Skill"路径已由
   T06 统一收敛到 `SkillVersionService.create_candidate_from_dir`；   运行时解析器（T08）已实现，
   无活跃版本的 Skill 现由 `SkillRuntimeResolver.require_version()` 拒绝执行。详见第 9 节。

## 9. 关键决策与偏离说明（T05–T08）

1. **manifest 必填字段分级，而非一律必填**：设计文档要求"缺失 manifest 被拦截"，
   但平台存量 Skill 绝大多数是**纯指令型**（只有一份 `SKILL.md`，无 `version`/`stage`/
   `entrypoint`/`input_schema`）。若按文档把 6 个字段一律设为必填，**全部历史包都将无法回传**，
   与 T04 的存量兼容目标直接冲突。实现按风险分级：
   - `REQUIRED_MANIFEST_FIELDS = ("name", "description")` —— 缺失即拒绝；
   - `RECOMMENDED_MANIFEST_FIELDS = ("version", "stage", "entrypoint", "input_schema",
     "output_schema", "permissions")` —— 缺失只记 `manifest_recommended_missing` **warning**。
   `version` 缺失时由 `_auto_version()` 在已有版本号里取 `max` 后 `patch+1` 补一个 `X.Y.Z`。
   这样"恶意/残缺包被拦"与"历史指令型包可回传"两个目标同时成立。

2. **API Key 与包哈希解耦（本批最关键的语义设计）**：内部 Skill 必须注入平台 API Key 才能执行，
   但如果把 Key 算进 `package_sha256`，会出现"同一份用户内容因注入 Key 不同而每次都是新版本"，
   并且"下载（脱敏）→ 重新上传"永远无法幂等命中。实现把 `package_sha256` 定义为
   **"用户提交内容"的指纹**，在**注入 Key 之前**计算：
   - 创建：`scan_package_dir`（含取哈希）→ `_inject_api_key` → 落盘；
   - 导出：反向脱敏回占位符 `PUT_YOUR_WHARTTEST_API_KEY_HERE`；
   - 两边统一用 `redact_secrets=True` 归一化口径，因此「注入 → 导出 → 重传」三方哈希一致。
   真实验收已验证：导出包重新上传命中**同一版本 UUID**（T07 幂等）。

3. **凭据文件必须"被拒绝"而不是"被忽略"**：`iter_package_files()` 会静默排除 `.env`、`*.pem`
   等敏感文件，导致这些文件**既不落入包也不报错**——看起来安全，实际是"上传者以为扫过了"。
   但"被排除"≠"被接受"。实现新增 `_iter_all_files()` 全量清单 + `CREDENTIAL_FILE_NAMES` /
   `CREDENTIAL_FILE_SUFFIXES` / `_credential_file_issue()`，`scan_package()` 对**全量**文件判定，
   命中即拒绝。这是本批修掉的一个真实安全语义缺陷。

4. **导出泄漏检测必须"先扫原文、再脱敏、再扫结果"，不能只扫脱敏后**：最初的实现只有一道
   "脱敏后扫描"，而脱敏会把任何 `API_KEY = "<真值>"` 一律替换为占位符——于是**真泄漏的包被
   悄悄脱敏后放行**，泄漏检测形同虚设（测试 `test_credential_leak_blocks_download_and_quarantines`
   暴露了这一点）。正确顺序是：
   ```text
   ① scan_contents(原始内容, skip_codes={"assigned_secret"})   # 平台自己就注入 KEY，跳过它
   ② redact 脱敏
   ③ scan_contents(脱敏结果)                                    # 确认脱敏没有遗漏
   ④ build_zip(脱敏结果)
   ```
   任一道命中即 `_reject_and_isolate()`（转 `quarantined` + 审计）并抛错，禁止下载。

5. **`skill_path` 语义变更：上传不再直接改活跃指针**。新语义下"上传只落版本目录，激活才设
   `Skill.skill_path`"，因此上传后 `skill.get_full_path() is None` 是**预期行为**而非缺陷。
   `refresh_active_pointers()` 同步把 `Skill.skill_path` 指向活跃版本的 `package_path`；
   **无活跃版本时置空**——这样历史执行路径也会因找不到目录而拒绝执行，与 T08 的
   `require_version()` 形成双保险。既有测试 `test_create_from_zip_supports_*` 的两处断言
   已按新语义更新（改读 `skill.versions.get().get_full_path()`，并显式断言
   `skill.get_full_path() is None`）。

6. **`resolve_locked()` 刻意重新查库，不复用 ORM 缓存**：初版直接读 `lock.skill_version`，
   若 `lock.skill_version` 已被前序代码加载过，`select_related` 之外的状态字段会命中 ORM
   身份映射里的**过期对象**，于是一个已被隔离（`quarantined`）的版本仍会被判定为 `active` 并放行。
   现改为按 `lock.skill_version_id` **重新查库** + `select_related("release", "skill")`。
   真实验收的"隔离后锁定任务被拒绝"正是靠这一改动才成立。

7. **`SkillViewSet.get_permissions()` 去掉全局模型权限叠加（T01 授权矩阵的真实缺口）**：
   `BaseModelViewSet.get_permissions()` 返回 `[IsAuthenticated(), HasModelPermission()]`。
   初版在其上追加业务角色权限类，结果**项目成员一律 403**——因为项目成员通常不持有全局
   `skills.add_skill` 权限位。Skill Hub 的授权维度是"项目内业务角色"，粒度比全局模型权限更细，
   叠加会让 T01 要求的"测试执行人员可以上传、预检"完全失效。现只返回：
   ```python
   [IsAuthenticated(),
    IsTestLead() if action in LEAD_ONLY_ACTIONS else IsTestExecutor()]
   ```
   项目边界与角色分别由这两个权限类负责。修复后真实验收从"全线 403"变为 25 项全通过；
   `skills/tests_isolation.py` 的 14 项隔离测试同步回归通过。

8. **`WorkflowSkillLock` 用 `lock_key` 区分阶段**：PostgreSQL 唯一约束**不约束 NULL**，
   若只按 `(project, workflow_id)` 建唯一键，四阶段（T15）里 `stage` 为空的锁会互相冲突或被
   静默放过。实现引入 `lock_key`（阶段/能力维度参与哈希）并以
   `UniqueConstraint(project, workflow_id, lock_key)` 收敛，为 T15 预留正确语义。

9. **`consume_preflight()` 重校验哈希防 TOCTOU**：预检与正式落盘之间存在时间窗，
   期间上传者可能改动暂存区内容。实现要求在 `consume_preflight()` 中以
   `compute_package_sha256(..., redact_secrets=True)` **重算并比对**令牌里记录的哈希，
   不一致即拒绝，暂存区不落正式版本库。

## 10. 关键决策与偏离说明（T09–T13）

1. **"没有信号"与"信号为 0"必须分开表达**：漏报率、误报率的分母来自人工反馈信号，
   而新上线能力、冷启动分区常常**一条信号都没有**。若把分母为 0 直接算成 `0.0`，
   门禁会把"完全没被验证过"判成"零缺陷通过"。实现让 `_rate()` 在分母为 0 时返回
   `None`，门禁对 `None` 走 `needs_review` 而不是 `passed`；同时用
   `min_sample_count` / `min_partition_cases` 两个阈值把样本量要求写成可配置项。

2. **门禁快照必须不可变，且幂等**：`EvaluationGateSnapshot.save()` 对已存在记录直接
   抛 `ValidationError`，并加 `Unique(candidate_run, kind, content_hash)`。二者分工不同：
   前者挡"改历史"，后者让"同一条件下重复跑门禁"复用同一条快照而不是堆出一串副本。
   审批时记录 `approval_snapshot_hash`，激活前 `assert_approval_evidence_current()`
   比对当前最新快照哈希——**评测数据变了就必须重新审批**，这是 R7 里最容易做漏的一条。

3. **漏报/误报的分母口径在本批被固定下来**：`TP = defect_confirmed + accepted + test_passed`。
   不这么定的话，"人工编辑"这类信号会被同时算进正向和负向，两个率互相抵消，
   门禁看上去永远"还行"。口径写在 `evaluation_gates.py` 的 `POSITIVE_SIGNALS` /
   `FALSE_POSITIVE_SIGNALS` / `MISSED_SIGNALS` 三个常量里，不散落在调用方。

4. **反馈的证据不能只看 `FeedbackEvent.evidence` 一个字段**：产出协议
   （`output.metadata["protocol"]["evidence"]`）里可能已经带了证据，若只认 feedback 表字段，
   会把"产出自带证据、反馈只是确认"的场景误判成"无证据"。`effective_evidence()` 同时认两处，
   `assert_evidence_complete()` 才是真正的门槛。这一条直接决定误报/漏报能否进入金标。

5. **八类业务能力是"业务能力"而非"平台工具"**：`BUSINESS_CAPABILITY_STAGES` 恰为
   5 个 Skill 型阶段（`case_review`/`test_plan_generation`/`testcase_generation`/
   `test_execution`/`report_generation`）+ `code_review` + `risk_identification` +
   `issue_tracking`。`knowledge_query` 属平台工具，**不进业务能力口径**——否则金标覆盖率
   会被一个"知识问答"阶段稀释，门禁的分区要求也会被迫为它放宽。`required_partitions()`
   按能力形态推分区，避免各页面各写一套。

6. **`FailureAttribution.layer` 的默认值必须为空**：初版给 `layer` 设了
   `default="skill_tool"`，结果 `save()` 里"若 layer 为空则按 category 派生"的分支
   **永远为假**——Django 在构造实例时就把默认值填上了。字段改为 `default=""` + `blank=True`，
   派生逻辑才真正生效。这是个"看代码很对、跑起来静默失效"的典型缺陷，靠
   `test_layer_is_persisted_from_category` 这类断言才暴露出来，仅看接口 200 是发现不了的。

7. **派生候选必须换版本号，但"重复派生"必须靠补丁指纹判**：这两件事看似矛盾，实则是同一
   约束的两面。
   - 换号：`SkillVersion` 唯一约束是 `(skill, version)`，派生内容与基线必然不同，
     沿用旧号会被"版本号已存在且内容不同"直接拒绝；更关键的是**包内 manifest 与库内
     版本号必须一致**，否则导出候选包再传回来，manifest 声明的旧号会与库里旧号撞车。
   - 指纹：换号之后，重复派生产出的**包哈希天然不同**，因此"事后比对新包哈希"
     永远判不出重复。真正的判据只能是"改动本身是不是同一批"，所以补丁保持确定性
     （不夹带版本号），版本推进作为机械动作放在工作副本上执行，重复判定落在
     `patch_fingerprint` + `SkillVersion.source_metadata.derivation_fingerprint` 上。
   若不换号却想拦重复，会退化成"拦不住"；若换号又只比对哈希，会退化成"每次都是新候选"，
   调用方能拿同一批归因反复刷候选去走审批——两条退化路径都会让自进化链失去意义。

8. **既有失败 `test_testcase_generation_agent_output_is_traceable` 的归属证据**：
   该用例经 `_record_module_output` → `publish_output`，而 `publish_output` 中新增的
   `WorkflowGateService.assert_can_enter(...)` 会因"上一阶段 `test_plan_generation`
   尚未通过质量门禁"抛错，导致 `ids` 为 `None`。经文件时间戳取证：
   `protocol.py` = 2026-10-01 14:12、`operations.py` = 2026-10-01 14:31、
   `agent_loop_view.py` = 2026-10-01 14:12、`tests.py` = 2026-09-30 22:03，
   **全部早于本批 T09–T13 改动（17:23 起）**，且本批未触碰这四个文件中的任何一个，
   属工作区既有未提交改动，非本批引入。该用例的修复归属 **T15**（四阶段门禁主链路的
   正主）：门禁成为强制链路后，用例必须先登记并放行 `test_plan_generation` 阶段门禁，
   再断言产出可追溯，而不是让门禁对单阶段用例静默失效。


## 11. 关键决策与偏离说明（T14–T16）

1. **任务启动时一次性锁定四阶段版本，而不是"走到哪锁到哪"**：
   链路跑起来之后随时可能有人激活新版本。若每阶段等它自己开始时才解析 active 版本，
   同一条流水线的四个阶段可能分别用到四份不同的包，"这条链路的产出对应哪个版本"
   就无法回答，回滚也界定不了影响范围。因此 `start_workflow()` 在入口就把四个阶段全部锁掉，
   并把"某个阶段的能力包没准备好"在**入口处**暴露——否则要等跑了两小时之后的报告阶段才炸。
   对项目**未登记** Skill 的阶段，如实返回 `unmanaged_stages` 而不是假装锁上了。

2. **`register_output` 对同一产出必须幂等（本批修掉的真实故障）**：
   早期实现无条件 `update_or_create(defaults={"status": "pending"})`，
   于是"同一批结果重发一次"会把一个**已通过**的门禁打回待测评，后一阶段随即被挡住——
   而系统里并没有任何新信息。修正后：**同一产出的重复登记保持原状态**；
   **换了产出**才重置为 `pending`（新内容必须重新过门禁，旧结论不能顺延）。

3. **驾驶舱读"任务锁定版本"而非"当前活跃版本"（本批修掉的显示错误）**：
   `ProjectQualityCockpitService` 原读 `output.capability.active_release.version`。
   链路跑到一半有人激活新版本后，历史流水线在页面上会显示成用了新包，
   而它实际跑的是旧包。这既是显示错误，也让回滚无法界定影响范围。
   现统一改读 `WorkflowSkillLock`，并给 `version_locked` / `package_sha256` 两个显式字段；
   无锁时回退产出自身版本并标 `source="output"`，不冒充"锁住了"。

4. **报告阶段必须"先过确定性契约、再谈分层评分"（T16 核心语义）**：
   分层评测（l0–l3）衡量的是"文本像不像一份好报告"，它**看不出引用是不是真的指向了
   本项目本链路的产出**——一份引用了不存在产出 ID、或压根没写"未闭环问题"的报告，
   在文案层面同样可以写得像样。因此 `WorkflowGateService.evaluate()` 对报告阶段
   先跑 `ReportGateService.validate()`，契约不过就**直接判失败并清空 scores**，
   不走评分路径。这是"缺失上游引用或 Schema 不合法时报告门禁失败"这条验收的实现方式。

5. **"未闭环问题为空"是合法答案，缺字段不是**：报告正文四项统计
   （覆盖率 / 通过率 / 失败分布 / 未闭环问题）必须**显式给出**，空列表要通过。
   不这么定的话，"确实没有未闭环问题"和"忘了写这一项"在报告里长得一模一样，
   而后者恰恰是最需要被发现的——它意味着报告没有对问题闭环情况做任何交代。

6. **契约结论不参与 `workflow_status.completed` 判定**：契约只在
   `evaluate()` 里拦住 `passed`；负责人留痕放行（`overridden`）是人的决定，
   不能让契约反过来否决，否则"留痕放行"会变成一句空话。
   契约结果随 `report_contract` 字段一并回给页面，用于解释"为什么还没过"。

7. **`parent_output_ids` 在协议入口归一为字符串（本批修掉的真实缺陷）**：
   调用方很自然会直接传 `output.pk`（UUID 对象），而 UUID 不是 JSON 可序列化类型——
   `metadata` 落库前会被 `_json_safe()` 整体退化成 `str(dict)`。后果不是"丢一个字段"，
   而是下游读 `metadata["protocol"]` 时直接抛 `AttributeError`，
   **门禁登记当场失败、产出也拿不到版本溯源**。现于 `OutputEnvelope.validate()`
   统一 `str()` 收口，比要求每个业务方都记得转换可靠。

8. **阶段评测与端到端评测必须同时产出，且都不能伪造分数**：
   只看阶段评测无法回答"这一段变好了，整条链路是不是还通"；只看端到端无法回答
   "这次变化到底改善了哪一段"，端到端掉分时不知道是谁的锅。
   两者都是**确定性**计算：阶段评测取该产出已完成的分层评测均分，**没有结果就如实记
   `judged=False`、`passed=None`**；端到端评测取四阶段覆盖、门禁状态、版本锁与报告契约。
   不调用模型，因此可以在门禁里反复执行；`evaluate_and_record()` 也**刻意不改变门禁状态**
   ——评测是放行决定的输入，不是决定本身。

9. **端到端评测里的版本锁"分来源标注"**：有 `WorkflowSkillLock` 时 `source="lock"`；
   只有产出自身携带版本时 `source="output"`；两者都没有则留空。
   这避免把"产出记了版本"渲染成"任务锁过版本"——前者事后无法防住链路中途换包。

10. **下游责任定位：无已确认归因时不给可用结论**：
    `ResponsibilityService.locate()` 复用 `trace_upstream_versions()` 的
    `parent_output_ids` 反查链，输出责任阶段、SkillVersion、包哈希三件具体的事。
    硬约束是 **`resolved=False` 时 `responsible_stage` 必须为空**，
    只给一个 `hint_stage` 作参考线索。若此时返回一个看起来可用的阶段名，
    调用方会拿着它去派生候选，而 `assert_confirmed_attributions()` 才在更后面拦住——
    错误发现得越晚，代价越大。

11. **既有失败 `test_testcase_generation_agent_output_is_traceable` 的最终处置**：
    第 10 节第 8 条已取证该失败属工作区既有改动（非 T09–T13 引入），归属 T15。
    T16 一并修复：用例生成是全链路测试的第二段，平台自 T15 起强制
    "上一阶段门禁通过才能进入下一阶段"，因此该用例必须先**用真实协议发一份方案产出再放行门禁**，
    而不是直接捏一条门禁记录——这样这条用例同时覆盖了
    "方案阶段产出 → 门禁 → 用例阶段产出"的真实顺序。修复后四应用 538 项首次全绿。

## 12. 关键决策与偏离说明（T18）

1. **控制台的角色真值走 skills 域自服务端点，不再读 `/projects/{id}/members/`**：
   T18 要求"只显示测试负责人和测试执行人员"，而业务角色的唯一真值在
   `projects/roles.py`。原实现让前端拉 `/projects/{id}/members/` 再按当前用户
   匹配自己那一行，有两个硬伤：① 该接口是**成员管理**接口，要求
   `projects.view_projectmember` 这一全局模型权限位，而业务角色本来与全局模型权限
   无关——一个持有 `member` 角色、本该能进控制台的项目成员会因为缺权限位拿到 403，
   被前端判成"非本项目成员"，验收直接落空；② 为了知道自己是谁，要把整个项目的
   成员列表拉回来。故新增 `GET /projects/{project_pk}/skills/hub-access/`
   （`IsAuthenticated` + `IsProjectScoped`），只回答调用者自己的
   `business_role` / `is_test_lead` / `is_test_executor`。
   验收证据：执行人员 t18-executor 的 `get_all_permissions()` 为空集，仍读到
   `business_role="test_executor"`；三路径网络日志中 `/members/` 调用次数为 **0**。
   `skills/tests_isolation.py::SkillHubAccessTests` 刻意不给任何账号授予模型权限，
   用来锁住这条约束。

2. **非成员的 403 是"门控结果"，不是"读取故障"**：`hub-access` 对非本项目成员返回
   403（文案「无权访问该项目」），前端**刻意不把它置为 error**，而是渲染
   「该控制台只对项目成员开放（测试负责人、测试执行人员）…」的说明文案。
   把正常门控渲染成报错会让人以为"重试一下就能进"。验收实测：非成员路径
   `blocked=1`、`blockedError=0`。

3. **`hub-access` 不叠加业务角色要求**：该端点不读任何 Skill 数据，只需要
   "是本项目成员"。若沿用默认的 `IsTestExecutor()`，非成员会被告知"该操作需要项目
   测试执行人员权限"，暗示"再申请一下权限就能进"——实际是压根不在这个项目里。
   因此单独用 `IsProjectScoped`，让语义停在"你不是这个项目的人"。

4. **`_publish` 面的 diff：无基线不发请求**：首版 Skill 打 `versions/{id}/diff` 时，
   后端在缺 `base` 参数时回落到 `previous_version`，而首版没有父版本 → **400
   「缺少对比基线」**。修复为前端先判基线：显式选择优先，否则取
   `versionDetail.previous_version`（权威父版本），仍为空则**不发请求**，直接渲染
   「该版本没有可比对的基线（首个版本）。可在上方版本轨道点「设为基线」再对比。」
   验收实测：diff 错误态计数为 0。

5. **上传预检必须保留 400 里的校验报告**：R11 要求"先展示包校验结果再允许保存为
   候选"，而平台统一的 `request()` 在错误分支只保留 `errors` 字段，会把 400 响应体
   `data` 里的逐条问题丢掉，页面上只剩一句"包校验未通过"。因此 `preflight` 改走
   axios 实例 + `validateStatus: () => true` 自行判定成败，把报告完整取回。

6. **治理按钮"能不能点"由后端 `can_*` 决定，前端状态机副本只负责解释原因**：
   `actions` 的 `enabled` 只取 `approval-view` 的 `can_submit_approval` /
   `can_activate`，再与本地 `canReachReleaseState()` 取交；`hint` 按
   「角色不足 → 状态不可达 → 审批依据失效 → 缺什么条件」的优先级出文案。
   优先级不是随手定的：对处于 `active` 的版本说"还缺：评测门禁"是误导——
   它已经是生效版本，真正的原因是"生效版本不能再被激活"。故先判状态可达性。

7. **刻意不提供"触发影子评测"按钮**：触发影子评测需要 `baseline_run` 与
   `candidate_run` 两个评测运行，属评测域入口。控制台只做"看状态、做治理决定"，
   把发起评测留在评测页面，避免同一个动作出现两个出处。

8. **`release_id` 由后端下发，前端不猜关联**：控制台要拿发布单元主键去调
   `capability-releases/{id}/approval-view/` 与 `bindings/`。关联本来就在库里，
   若由前端按 `(name, version)` 反查，等于在前端重建一份关联规则。故
   `SkillVersionSerializer` 增 `release_id`（`read_only`，无关联时为 `null`）。

9. **平台级缺口（已发现，本任务未修，见待确认项）**：T18 的控制台本身已达标，
   但一个**零模型权限、零分组的项目成员**在当前平台配置下**到不了 `/skills` 页面**，
   三处都在 skills 域之外：
   ① 导航栏项目选择器读 `GET /api/projects/`，而 `ProjectViewSet.get_permissions()`
   对 `list` 叠加了 `HasModelPermission`（`projects.view_project`）→ 403；
   ② `MainLayout` 的 `hasSkillsPermission` 取 `skills.view_skill` → 菜单项不显示；
   ③ `checkProjectAndNavigate()` 在 `currentProjectId` 为空时**阻断跳转**。
   三者叠加后执行人员既看不到入口、也建立不起项目上下文。
   本次浏览器验收用**路由桩**为 `/api/projects/` 列表补一个项目上下文，把这条
   平台级缺陷隔离掉，从而单独验证 skills 域的角色链路（`hub-access` 走真实后端，不拦截）。
   是否需要一并修正平台侧权限口径，属跨模块行为变更，按约定**先确认再执行**。
