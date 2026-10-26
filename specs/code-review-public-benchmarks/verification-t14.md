# T14 全量、离线与发布验收

验收日期：2026-10-26；环境：Compose 项目 `wharttest-250`。

## 数据版本与离线校验

| 数据集 | 版本 | manifest SHA256 | 原始/可用/拒绝 | 金标/干净 |
| --- | --- | --- | --- | --- |
| AACR-Bench | `ee9d7fef70ef-a1.0.0` | `000aa0fcd8507347ee2b6d3eae3d7223990a02c504e908109357117be6f85a26` | 196 / 84 / 112 | 631 / 0 |
| SWE-CARE | `3b3a625ef26b-a1.0.0` | `27d975c90e0b8acdd48d50c9497d526fe1685f5e183e5035c510e65af0e8f840` | 671 / 671 / 0 | 1313 / 0 |
| SWR-Bench | `67ae1d4395ac-a1.0.0` | `4d332a768068d2223e880e201fa843d4a7cde6e3ff9fc967ba51c2c065f3c1bf` | 1000 / 943 / 57 | 771 / 500 |

三套数据已在断网条件下用本地原始文件重跑 manifest 与转换校验；已发布版本受模型不可变约束保护。

## 容器与存储

- `wharttest-new-backend`、`wharttest-new-frontend`、Postgres、Redis 均为 `healthy`。
- 前端已部署镜像 `wharttest-new-frontend:public-benchmarks`，静态产物包含「内置公开集」。
- 公开集数据与快照当前总占用 188 MB。快照实现大小/时间限制、运行租约和过期清理；租约内快照不会被回收。
- Celery 工作进程已实测注册 `execute_benchmark_run` 与 `finalize_code_review_benchmark`。

## 端到端运行

| 数据集 | 运行 ID | 成功/失败 | Token | 状态 |
| --- | --- | --- | --- | --- |
| SWR-Bench | `4b675741-f359-4c86-9fc1-463c21d94326` | 2 / 0 | 56,155 | completed |
| AACR-Bench | `876e1253-f056-43c8-8bd9-b60b006abc75` | 2 / 0（另 1 条快照失败排除） | 102,982 | completed |
| SWE-CARE | `ba229806-6626-4b4a-8a5e-98b81e9bf079` | 1 / 0 | 77,183 | completed |

报告入口：`/knowledge-evolution?view=data` → 「独立能力评测」 → 「代码审查」。未逐样本人工核验时，正式召回/准确/误报率保持 `null`，不使用自动候选匹配伪造质量结论。

## 未执行全量长跑的理由

三套共 1,698 条可用样本。当前 5 条实测平均约 51.8k Token，线性估算全量约 8,800 万 Token，且 FreeCAD 等大仓库在当前 GitHub 出口下获取一次可超过 30 分钟。该消耗超出本次功能验收预算，因此未发起全量长跑；此决定不影响「三套均已可运行」的试跑结论，但不宣称已获得全量质量基线。

## 回退

1. 前端回到上一镜像：将 `DOCKER_FRONTEND_IMAGE` 设为 `wharttest-new-frontend:code-review-benchmark`，仅重建 `frontend`服务。
2. 后端代码回退时保留 0047/0015 表与数据，旧代码不会读取这些新表；不建议在有运行记录后反向迁移。
3. 若确需撤销模型，先导出运行报告并停止 worker，再在备份数据库上验证反向迁移；不对生产数据直接强制回退。
