# 代码审查内置公开测评集：设计

状态：待确认实施。对应 [需求](requirements.md) 和 [任务](tasks.md)。

## 现状与目标结构

现有 `knowledge_evolution` 通过 `EvaluationSuiteViewSet.import_code_review` 接收标准 JSON，`EvaluationRunViewSet.run_code_review` 用 `ProjectRepository` 创建 `AnalysisTask`，`code_review_benchmark.py` 汇总人工核验结果。`CodeReviewBenchmark.vue` 将公开/内部都作为 JSON 导入。公开集不能只替换 UI 文案：当前运行器不认识外部仓库，也没有官方原始格式适配器。

新增公共的 **BenchmarkCatalog → DatasetVersion → BenchmarkCase → RepositorySnapshot → EvaluationRun** 链路。内部集仍走现有 EvaluationSuite；公开集的归属是全局只读资产，运行结果归属于当前项目。优先新增独立公开数据模型或表，不用 `EvaluationSuite.project` 伪造「全局项目」。运行可持有公开数据版本与所选样本清单，报告复用现有人工核验语义。

## 数据与版本

- 目录是代码中固定的三个 ID：`swe_care`、`swr_bench`、`aacr_bench`；显示名、官方 URL、默认分片、语言说明、许可信息和适配器版本由服务端下发。
- 数据版本记录上游 Git commit/Hugging Face revision、原始文件 SHA256、导入时间、适配器版本、样本总数/有效数/拒绝数、准备状态。版本发布后不可原地改写；更新产生新版本，旧运行仍引用旧版本。
- 统一样本至少保存 `benchmark_id`、`upstream_instance_id`、`repo`、`base_sha`、`head_sha`、`language`、`gold_issues`、`is_clean`、`source_url`、原始金标定位、转换状态。以 `(benchmark_id, version, upstream_instance_id)` 去重。不得把空金标自动推断为干净 PR，必须由原集显式标签或适配器确定。
- 三个适配器各自实现原始字段映射和校验，保留原始 ID 与字段摘要。若 AACR 的某语言没有样本，目录和报告显示实际数量 0。

## 仓库与执行隔离

- 公开仓库快照由独立准备作业取得，使用允许列表中的 `github.com` 仓库标识和精确 Commit；禁止客户端提交任意 URL/本地路径。路径限定在专用持久化目录，镜像内进程仅授予所需读写权限；审查容器使用只读快照。
- 预检校验 base/head 均存在、能生成 Diff、语言一致、资源配额和凭据就绪。下载、解包、路径解析和 Git 操作设超时/大小上限；不在 Web 请求内同步 clone 或转换。
- 运行器为公开样本引入受控 `benchmark_snapshot` 来源或等价内部适配层，不借用用户的 `ProjectRepository` 和 GitLab 凭据。保持 `AnalysisTask` 的标准审查、全部 Diff 覆盖、审查结果存储与状态机。若该改造触及其非空外键，设计数据库迁移与旧数据兼容，不以虚构项目仓库绕过约束。
- 数据及快照可以跨项目只读复用；运行实例、审查结果、人工核验和报告始终按项目隔离。缓存提供引用计数/租约，避免运行中被清理。

## API 与前端

建议接口：

| 接口 | 用途 |
| --- | --- |
| `GET /evaluation-benchmarks/` | 三个固定目录项、版本、语言覆盖、准备状态 |
| `POST /evaluation-benchmarks/{id}/prepare/` | 授权维护角色异步准备固定版本 |
| `GET /evaluation-benchmarks/{id}/versions/{version}/preflight/` | 样本数量、快照可用率、失败明细 |
| `POST /evaluation-runs/run-code-review-public/` | 项目、基准、版本、样本 ID/范围、运行名 |
| 现有报告/核验接口 | 增加基准来源与分组，保持旧运行兼容 |

前端在代码审查测评区域设「内置公开集 / 内部集」标签。公开集卡片只显示实际可用状态，操作顺序为选择 → 准备/预检 → 试跑/全量 → 报告；准备需要维护角色时给出明确提示。内部集沿用 JSON 导入和旧运行列表。首次默认建议固定的小样本试跑，用户明确确认后才提交全量，避免意外消耗大量 Token。

## 评分与报告

公共指标沿用现有 `code_review_benchmark.py` 的候选匹配、人工核验、Token、耗时定义，但修正统计分母为「成功且已核验」对应子集；同时显示核验覆盖率、成功率、失败/排除数。干净 PR 误报率只对原集认证的干净样本计算。原基准评价方法另设 `native_metrics` 命名空间，并标注是否已实现及口径；未实现显示「暂不可用」，不可填 0。

报告元数据锁定数据/转换器/快照/模型/代码版本、样本 ID 列表和过滤条件。导出的 JSON 中保留逐样本证据，但不包含访问令牌或未授权的仓库内容。报告页面需支持按语言、正负例、状态筛选。

## 测试与交付

适配器使用固定官方样例做契约测试：字段变动、重复 ID、缺失 Commit、空金标真假判别、路径风险。再做仓库快照安全测试、项目权限测试、老内部集兼容测试、同一版本复跑一致性、异常/断网/离线包测试。容器环境先跑每集少量样本，再跑完整集；记录资源与成本，并验证容器健康及回退。不得以 Django `check` 或前端构建通过代替真实样本运行。
