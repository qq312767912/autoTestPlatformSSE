# T13 容器端到端试跑：实测记录

容器：`wharttest-new-backend`（WHartTest-2.5.0，Django 5.2 / Postgres / 模型 `deepseek-v4-flash`）
项目：`12 上证路演平台`　执行账户：`admin`
执行区间：2026-10-26 01:23–11:35（UTC+8）

## 一、已跑通：SWR-Bench

运行 ID：`4b675741-f359-4c86-9fc1-463c21d94326`
模式：`pilot`（`allow_partial=true`），数据集版本 `67ae1d4395ac-a1.0.0`

| 样本 | 标签 | 语言 | 金标 | 快照 | 审查状态 | 发现数 | Token | 耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `pallets__flask-2019` | 缺陷 | python | 2 | 2,262,786 B / 245 文件 | completed | 0 | 22,061 | 78.1 s |
| `pallets__flask-2378` | 干净 | python | 0 | 3,507,264 B / 264 文件 | completed | 3（均在 `flask/cli.py:174`） | 34,094 | 106.1 s |

两样本机器覆盖 `machine_coverage=100.0`、`ai_coverage=100.0`，变更行分别 7 行 / 58 行，说明「0 条发现」不是覆盖率缺失造成的空跑，而是模型确实没报出该缺陷。

### 报告实测（`build_benchmark_report`）

| 字段 | 值 | 判定 |
| --- | --- | --- |
| `candidate_recall` | 0.0 | 自动匹配口径，仅提示候选 |
| `verified_recall` / `verified_precision` / `f1` | `null` | 无样本完成逐样本核验，按 R5 不输出质量指标 |
| `clean_cases` | 1 | 干净样本已成功执行并计入分母池 |
| `clean_pr_false_positive_rate` | `null` + `note=干净样本尚未逐样本核验，暂不计算误报率` | 口径正确：不拿未核验数据出误报率 |
| `native_metrics.implemented` | `false`，`value=null` | 官方 SWR 口径未实现，不用平台口径冒充 |
| `coverage` | selected 2 / failed 0 / unverified 2 / verified 0 | 未核验单列 |

## 二、已跑通：AACR-Bench（Python + Java，C++ 失败样本显式排除）

运行 ID：`876e1253-f056-43c8-8bd9-b60b006abc75`
模式：`pilot`，数据集版本 `ee9d7fef70ef-a1.0.0`，最终 `run.status=completed`、`binding.status=completed`

| 样本 | 语言 | 金标 | 快照 | 审查状态 | 原始发现 | 已确认 | 候选匹配 | Token | 耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `browser-use__browser-use@0b21e50` | python | 2 | 7,774,618 B / 235 文件 | completed | 2 | 0 | 0 | 46,360 | 464.8 s |
| `kestra-io__kestra@06e7c6e` | java | 3 | 22,584,469 B / 2,461 文件 | completed | 3 | 2 | 0 | 56,622 | 639.1 s |
| `FreeCAD__FreeCAD@82fef4a` | c++ | 3 | — | 排除 | — | — | — | — | — |

汇总：`n_selected=3`、`n_success=2`、`gold_issues=5`、`raw_findings=5`、`reported_findings=2`（仅 confirmed）、
`tokens=102,982`、`avg_tokens=51,491`、`candidate_recall=0.0`、
`verified_*` 全为 `null`（未核验），`precision_note=该基准无干净样本，未计算准确率与 F1`，
`coverage={selected:3, excluded:1, failed:0, unverified:2, verified:0}`。

跨语言分组已生效：`groups.language = {java: 1, python: 1}`，各自金标/发现/Token 独立统计。

C++ 样本被排除的原因（写入 `binding.excluded_cases`）：

```
FreeCAD：git fetch 300s 超时（TimeoutExpired）
改试 bitcoin/bitcoin：27 分钟仅下载 51 MB（约 32 KB/s），准备阶段中止
```

## 三、已跑通：SWE-CARE

运行 ID：`ba229806-6626-4b4a-8a5e-98b81e9bf079`，样本 `Avaiga__taipy-833@24559da`（python，1 金标）。

| 样本 | 审查状态 | 原始发现 | 已确认 | Token | 耗时 |
| --- | --- | --- | --- | --- | --- |
| `Avaiga__taipy-833@24559da` | completed | 7 | 2 | 77,183 | 7,156.3 s |

`run.status=completed`、`binding.status=completed`、报告 `failed=0`。初次快照曾因 GitHub 超时失败；在快照恢复后复用原运行和原审查任务重试，没有重复建立评测样本。

## 四、网络时间线（解释为什么三套没能同时跑完）

| 时间 | GitHub 状态 | 现象 |
| --- | --- | --- |
| 01:20 前后 | 可用 | `pallets/flask` 两个快照均成功 |
| 01:30–02:45 | 整体不可达 | 容器 `GnuTLS handshake failed`；宿主机 `github.com` / `codeload` 均超时（`http=000`），仅 `api.github.com` 可达 |
| 10:45 之后 | 恢复但极慢 | 宿主机与容器均返回 200；`browser-use`、`kestra` 快照成功，大仓库（FreeCAD、taipy）仍超时；`bitcoin` 实测约 32 KB/s |

结论：GitHub 出口波动会影响首次快照时间，但快照可复用、审查任务可幂等重试，三套数据均已产生真实报告。

### 顺带改掉的一个旧限制

`GIT_TIMEOUT` 原为 300 秒固定值，对 FreeCAD / taipy 这类仓库必然超时。已改为
`settings.BENCHMARK_SNAPSHOT_GIT_TIMEOUT`（默认 900 秒）可覆盖，并在超时后清理半截 `.git` 重试一次
（半截 pack 会导致下一次直接报 `bad pack header`）。

## 五、本次试跑暴露并修复的缺陷

| # | 现象 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | 两个样本审查完成却整体 `failed`，报 `name 'logger' is not defined` | 数据飞轮记录失败后，异常处理分支引用了 `code_analysis/services.py` 中从未定义的 `logger`，把真实错误覆盖掉 | 补 `logging` 导入与模块级 `logger`，异常不再被掩盖 |
| 2 | 飞轮记录抛 `AttributeError`（被上一条掩盖） | `record_code_review_task` 用 `task.repository.name` 拼查询串，公开样本 `repository` 为 NULL | 新增 `_review_source_label()`，公开样本退化为 `[公开集] owner/name` |
| 3 | 选中 1 个干净样本，报告却写「无干净样本」 | `clean_cases` 仅在「已核验」分支累加 | 拆成 `clean_cases`（成功即计）与 `clean_verified_cases`（核验才计），提示语按三态区分 |
| 4 | 召回率分母被放大 | 分母取全部成功样本金标，分子只来自已核验样本 | 新增 `verified_gold` / `verified_located_gold`，分母只含成功且已核验样本 |
| 5 | 全部快照失败时静默返回 `created=0`，运行停在 `running` | `prepare_run` 未处理「一条任务都没建成」 | 显式置 `run.status=failed` + `config.blocked_reason`，失败快照写入排除清单 |
| 6 | 运行结束后 `BenchmarkRunBinding.status` 停在 `running` | finalize 只更新 run | finalize 同步 binding 状态 |
| 7 | 「报了 2 条但都待确认」在报告里显示为 0 | `reported_findings` 只统计 `disposition=confirmed` | 新增 `raw_findings` 并列展示，区分「没发现问题」与「问题未被确认」 |

## 六、口径限制（不伪造）

- **C++ 快照样本**：AACR 的 FreeCAD 快照已按「仅运行可用样本」协议记入排除清单；AACR 数据适配、语言分组与预检仍覆盖 33 条 C++ 样本，但本次不宣称已获得 C++ 质量分数。
- **逐样本核验**：本次 5 条实际成功样本尚未做人工 adjudication，故 `verified_*` 全为 `null`。这不阻塞 T13 的「可运行」判定，但在人工核验完成前不能给出正式召回率、准确率或误报率。
