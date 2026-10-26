# T01 官方版本与许可核验记录

核验时间：2026-10-25。核验对象为本机已下载的原始文件，全部数值来自文件实测，不引用论文摘要。
代码侧唯一事实来源：`WHartTest_Django/knowledge_evolution/benchmark_catalog.py`。
离线校验方式：`python manage.py benchmark_verify --local-dir data/code-review-benchmarks/_upstream`（无网络即可执行）。

## 一、版本与校验和

| 基准 | 上游 | 固定 revision | 原始文件 | 字节数 | SHA256 |
|---|---|---|---|---|---|
| SWE-CARE | `inclusionAI/SWE-CARE`（HF） | `3b3a625ef26bd497a3a13485c0dc9ece537f24d0` | `data/test-00000-of-00001.parquet` | 12,126,296 | `be45381d…35d26b07` |
| SWR-Bench | `ZZR0/SWRench` | `67ae1d4395ac05f800d62b0e13678eadeb9fa5c7` | `data/swr_datasets_d5c5.jsonl` | 84,826,523 | `7048e7a9…71aff95d` |
| AACR-Bench | `alibaba/aacr-bench` | `ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df` | `dataset/positive_samples.json` | 1,117,816 | `02c8561f…a2cb2097` |
| AACR-Bench | 同上 | 同上 | `dataset/negative_samples.json`（**排除**） | 508,475 | `d4e9f5b6…bfa77e2b` |

完整 SHA256 见 `benchmark_catalog.py` 中 `CATALOG[*]["files"]`。

## 二、许可

| 基准 | SPDX | 依据 | 可再分发 | 约束 |
|---|---|---|---|---|
| SWE-CARE | Apache-2.0 | HF dataset card `cardData.license` | 是 | 保留来源与许可声明 |
| SWR-Bench | MIT | 仓库 `LICENSE` | 是 | 保留版权声明 |
| AACR-Bench | Apache-2.0 | GitHub API `license.spdx_id` | 是 | 保留来源与许可声明 |

三者均可随平台内网分发。**对外交付**时报告须带来源、版本与许可字段，由报告模板强制输出，不依赖撰写人。

## 三、语言覆盖（实测）

| 基准 | 覆盖 | 未覆盖 | 证据 |
|---|---|---|---|
| SWE-CARE | Python 671 | Java、C++ | HF `cardData.splits`：test 671 / dev 7086；parquet 明文仓库名抽样（sqlglot、airflow、mypy、pylint…）全部为 Python 仓库 |
| SWR-Bench | Python 1000 | Java、C++ | 1000 条来自 12 个仓库：scikit-learn 255、sympy 231、matplotlib 153、astropy 120、xarray 79、pylint 50、pytest 35、requests 28、sphinx 22、django 11、seaborn 9、flask 7 |
| AACR-Bench | Python 21、Java 30、C++ 33 | 无（平台三语言全覆盖） | `positive_samples.json` 按 `project_main_language` 计数；84 PR / 631 条评论 / 15 仓库；评论文件后缀与项目主语言逐条比对无不一致 |

**结论：只有 AACR-Bench 同时覆盖平台三语言；Java 与 C++ 的公开样本全部来自它，且只有 30 与 33 条。**

## 四、正负例与金标

| 基准 | 样本 | 金标 | 干净样本 | 说明 |
|---|---|---|---|---|
| SWE-CARE | 671 | 逐条统计（准备阶段） | 无显式标签 | `reference_review_comments` 含 path/line/diff_hunk；无干净标签 → `is_clean=None`，不进误报分母 |
| SWR-Bench | 1000 | 949 条 change | **500** | `change_introduced=false` 且 `changes` 为空者 500 条，是唯一成规模负例 |
| AACR-Bench | 84 | 631 条评论 | 0 | 无干净标签 → 不参与误报率 |

SWR-Bench 交叉表实测：`(change_introduced=True, changes 空) = 0`，`(False, changes 空) = 500`，切分完全干净，949 条金标均带 `commit_sha` 与 `code_snippet`。

## 五、AACR `negative_samples.json` 为何排除

1. 官方 `evaluation/converters/aacr_bench.py`、evaluator、`docs/metrics.md` 均未引用该分片。
2. 实测：与 positive 分片按 PR URL 求交集 155 条，同一 PR 的 `source_commit`/`target_commit` 完全相同，而评论集合交集为 **0**；negative 分片内的评论同样是真实缺陷意见（`is_ai_comment` 分布与 positive 同类）。因此它是按评论级切分的另一批数据，**不是干净负例**。
3. 依据 R5「空金标或语义未定义不得当作负例」，本平台不使用该分片，并在目录中登记排除原因。

## 六、部署约束

- SWE-CARE 为 parquet，读取需 `pyarrow`；当前后端镜像未安装。适配器在缺依赖时应**准备失败**并给出明确原因，不得静默跳过样本。离线包建议由运维在具备 `pyarrow` 的环境预转换为 JSONL（见 T04 离线包格式）。
- 下载域名可达性实测：`raw.githubusercontent.com` 被拦；`codeload.github.com`、`api.github.com`、`hf-mirror.com` 可达。manifest 已按多源顺序登记，失败自动回退下一个源。
