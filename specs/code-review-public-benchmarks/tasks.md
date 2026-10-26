# 代码审查内置公开测评集：实施任务

状态更新：2026-10-26。依据 [需求](requirements.md) 与 [设计](design.md)。每项完成后勾选并附测试证据，不因 UI 出现条目就标记整集可运行。

进度：T01–T14 已完成。T12 的本特性专项测试全部通过；`knowledge_evolution` 全量套件仍有 37 个与本特性无关的存量回归，已单独记录，不伪写为全仓通过。

## 第一阶段：数据资产与目录

- [x] **T01 固定官方版本和许可清单**：核验三套官方数据的许可、原始文件、revision、语言/样本数；形成 `manifest`（URL、revision、SHA256、split、适配器版本），声明数据再分发和离线包边界。验收：清单可在无网络时验证，目录不含虚构语言/数量。_R1、R2_
  - 证据：[verification-t01.md](verification-t01.md)；`benchmark_catalog.py` 的 `manifest()` / `manifest_digest()` 离线可校验；`reconciliation` 字段记录对账实测值。
- [x] **T02 建立公开集模型与迁移**：新增全局目录版本、样本、准备作业与快照引用模型；定义不可变发布、去重、状态和索引，迁移/回滚不改写既有内部集。验收：迁移正反向测试通过，旧运行记录仍可读。_R1、R2、R7_
  - 证据：迁移 `0047_benchmark_catalog_models.py`、`code_analysis/0015_analysis_task_benchmark_source.py` 正反向通过；`makemigrations --check` 无变更；`VersionImmutabilityTest` 3 项通过。
- [x] **T03 实现三套独立适配器**：分别映射 SWE-CARE、SWR-Bench、AACR-Bench 的 Commit、语言、金标及干净 PR 标签；保留原始 ID 与转换错误。验收：每套固定样例及异常样例契约测试通过，转换数量与上游 manifest 对账，空金标不自动等于负例。_R2、R5_
  - 证据：真实数据实测 AACR 196→84 有效 / 112 拒 / 631 金标；SWR 1000→943 有效 / 57 拒 / 771 金标（含 500 干净）；SWE-CARE 671 / 1313 金标 / 0 拒。`AdapterContractTest` 覆盖 pyarrow 缺失、重复 ID、空金标、context hint 路径解析。
- [x] **T04 异步准备与离线预置**：固定源下载、校验、转换、原子发布；支持运维预置同版本离线包和断点/失败重试，不在 HTTP 请求内下载。验收：成功、断网、校验和不符、部分坏样本、重复准备均有确定状态和可追溯日志。_R2、R6_
  - 证据：三套 `BenchmarkDatasetVersion` 均为 `ready`；`fetch_sources()` 复用已校验文件（离线重入成功）；校验和不符、全源失败均抛确定异常；`requirements.txt` 补 `pyarrow>=16.0.0`。

## 第二阶段：仓库快照与运行

- [x] **T05 建立隔离的公开仓库快照服务**：按允许列表获取精确 base/head，限制路径、下载大小、时间、并发、磁盘和清理策略；读写准备与只读审查分离。验收：正常快照可复用；恶意路径/任意 URL/缺 Commit 被拒；运行中缓存不被删除。_R3、R6_
  - 证据：`pallets/flask` 两个真实快照（2,262,786 B/245 文件、3,507,264 B/264 文件）；`SnapshotSecurityTest` 覆盖路径越界、非法 SHA、非白名单组合；`snapshot_lease()` 租约期间不清理。
- [x] **T06 增加运行前预检 API**：校验数据版本、样本清单、快照、Diff、语言和模型，返回可用/不可用与原因及预计成本。验收：不可用样本不能无提示进入运行；「仅运行可用样本」记录明确排除清单。_R3、R4_
  - 证据：`/evaluation-benchmarks/{id}/preflight/` 返回 total/available/unavailable/excluded/language_counts/clean_counts/snapshot_cached/submittable/estimate；`PreflightTest`、`BenchmarkApiTest` 通过；快照失败样本现自动写入 `excluded_cases`。
- [x] **T07 接入公开样本执行器**：在代码审查任务中增加受控公开快照来源；兼容现有 `ProjectRepository` 任务和数据库约束，不依赖用户 GitLab 凭据。验收：同一公开样本可完成完整 Diff 标准审查；失败重试幂等；普通代码审查回归通过。_R4、R7_
  - 证据：两个 SWR 样本完成完整审查（machine/ai 覆盖均 100%，22,061 / 34,094 Token）；`code_analysis` 130 项回归全通过；`AnalysisTask` 双来源互斥约束经迁移生效。
- [x] **T08 实现公开集试跑/全量运行**：按项目创建运行，锁定样本 ID、数据/转换器/快照/模型/代码版本，设置并发与成本上限。验收：试跑和全量是不同运行；取消/重试/重复提交不产生重复审查；跨项目不可读写。_R4、R6_
  - 证据：`create_public_run()` 写入 binding（selected_case_keys / excluded_cases / preflight / model_snapshot / code_version）；`prepare_run()` 重复调用 `skipped` 不重复建任务；全失败时置 run=failed 并写 `blocked_reason`。本轮补齐了成功启动 API 契约测试，修复 `project_id`/项目实体参数错配，并将 `execute_benchmark_run` 加入 Celery 自动发现模块。

## 第三阶段：报告与界面

- [x] **T09 完善评分与报告**：公共指标按成功且已核验样本计算，单列失败、排除、未核验；按语言/正负例/仓库拆分，原生指标单独标识实现状态。验收：SWR 干净 PR 误报率、召回/准确率分母、零样本和部分失败边界测试通过；报告可复现。_R5、R6_
  - 证据：真实运行出报告，`verified_*` 与 `f1` 在无核验时为 `null` 并附原因；`native_metrics.implemented=false`；本轮修复「干净样本被误报为无干净样本」与「召回分母含未核验样本」两处口径错误，`BenchmarkReportTest` 8 项通过。
- [x] **T10 改造代码审查测评 UI**：添加「内置公开集 / 内部集」；公开集内置三个可选卡片，展示状态、语言覆盖、版本、预检、试跑/全量、报告；移除公开集上传框，内部集保留导入。验收：不可用集不能点击运行，错误提示可操作；页面与现有平台色系一致；历史内部运行可查看。_R1、R4、R7_
  - 证据：`CodeReviewBenchmark.vue` 重写；`vue-tsc -b --noEmit` EXIT=0；`vite build` 成功（`✓ built in 2m 3s`），产物含「内置公开集」文案。
- [x] **T11 收口旧 `source=public` 导入**：界面停止提供，后端拒绝新的伪官方公开导入，历史记录标识为「历史自定义集」且保持可读。验收：不能通过旧 API 冒充内置官方集；旧报告不丢失。_R1、R7_
  - 证据：`import_code_review` 对 `source=public` 返回 400（`test_manual_public_import_is_rejected`）；`_provenance()` 输出 `benchmark_catalog` / `legacy_custom` / `internal` 三态。

## 第四阶段：验证与发布

- [x] **T12 自动化验证**：跑适配器、模型迁移、权限、预检、执行、评分与旧内部集回归；前端类型检查/构建/交互测试。验收：本特性测试全部通过，全量套件的存量失败有明确归属。_R1～R7_
  - 证据：`test_benchmark_catalog + test_code_review_benchmark` 39 项通过；`code_analysis` 130 项历史回归通过；前端 `vite build` 成功（新镜像 `wharttest-new-frontend:public-benchmarks`）。
  - 说明：`knowledge_evolution` 全量 1052 项中仍有 37 项失败（23 fail / 14 error），主要为旧 fixture 未提供现协议必填的 `items[].module`，另有少量历史 Skill 绑定断言；本特性的测试文件不在失败列表。
- [x] **T13 容器端到端试跑**：在 `wharttest-250` 容器中，每套基准至少选可运行样本试跑，核对 Commit、全部 Diff 覆盖、Token/耗时、逐样本核验和导出；记录失败样本原因。验收：三套均有真实报告，不能把仅目录可见当作通过。_R2～R6_
  - 证据：SWR-Bench 运行 `4b675741-f359-4c86-9fc1-463c21d94326`（2/2 成功，56,155 Token）；AACR-Bench 运行 `876e1253-f056-43c8-8bd9-b60b006abc75`（2/2 成功、1 条快照失败排除，102,982 Token）；SWE-CARE 运行 `ba229806-6626-4b4a-8a5e-98b81e9bf079`（1/1 成功，77,183 Token）。三套 run/binding 均为 `completed`，详见 [verification-t13.md](verification-t13.md)。
- [x] **T14 全量与离线验收**：在资源预算内运行全量或明确记录未跑原因；验证数据离线预置、容器健康、磁盘上限、回退与旧审查可用。验收：附运行 ID、版本/校验和、样本数、成功/失败数、结果链接与回滚步骤。_R2～R7_
  - 证据：已验证三套固定版本/校验和、离线重入、快照清理与租约、容器健康、新前端回退步骤。未执行 1,698 条全量审查：按 5 条实测平均约 51.8k Token 估算将消耗约 8,800 万 Token，且大仓库快照受 GitHub 带宽限制，超出本次验收预算。详见 [verification-t14.md](verification-t14.md)。

## 完成定义

只有 T01～T13 全部通过，才称为「三个内置公开集可选且可运行」；T14 决定是否称为「已完成全量与离线生产验收」。若某集缺官方 Commit、许可或数据字段，应停在准备失败并记录原因，不造样本、不填假分数。

当前判定：**三套内置公开集均已准备就绪、可选且已用真实代码快照完成端到端试跑**（AACR 84 / SWE-CARE 671 / SWR 943 条）。全量长跑未执行的成本与网络原因已按 T14 要求明确留档。
