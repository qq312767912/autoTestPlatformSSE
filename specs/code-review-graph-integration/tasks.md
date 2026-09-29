# CRG PoC 实施计划

- [x] 1. 建立 CRG 独立服务
  - 创建 `WHartTest_CRG` 容器目录、锁定依赖和非 root 镜像
  - 实现健康检查、图谱准备、上下文查询和取消 API
  - 限制仓库路径、任务 UUID、仓库标识和 Commit 身份
  - 用独立子进程和 `CRG_DATA_DIR` 隔离每个任务图谱
  - _Requirements: R1, R3, R6, R7_

- [x] 2. 配置本地 Compose 与持久化目录
  - 增加 `crg-service` 服务、健康检查和内部认证
  - 将任务浅仓库只读挂载到 CRG，将图谱目录独立可写挂载
  - 处理 Backend/CRG 共享组读权限，不将整个数据目录暴露给 CRG
  - 默认关闭 Backend CRG 功能开关
  - _Requirements: R1, R6, R7_

- [x] 3. 实现 Backend CRG 适配层
  - 新增 `CodeReviewGraphClient`，统一认证、超时、重试、schema 校验和错误映射
  - 将影响文件、调用方、已有测试和受影响流程写入 `CodeReviewMCP.collect_context()`
  - 保留现有 AST/grep 上下文作为补充和降级
  - _Requirements: R2, R3, R4_

- [x] 4. 接入审查任务生命周期
  - 在浅仓库准备后执行 CRG prepare/context
  - 保证 `analyzable_diffs` 和 LLM 分批范围不被 CRG 缩减
  - 实现 CRG 超时、部分解析、不可用的降级日志
  - 接入任务取消和仓库/任务删除清理
  - 将图谱摘要写入报告，不持久化完整原始图响应
  - _Requirements: R2, R4, R5, R6_

- [x] 5. 完成 PoC 自动化验证
  - 增加 CRG Service 路径安全、认证、构建、查询、截断、超时和取消测试
  - 增加 Backend client、上下文合并、全量 Diff 覆盖和降级测试
  - 执行 Django 相关测试与 Compose 健康检查
  - 增加图谱摘要预览、支持/反证字段和真实浏览器回看
  - _Requirements: R2, R6, R7_

- [x] 6. 在本机启动 PoC 服务
  - 构建并启动 CRG Service
  - 验证只读仓库、可写图谱、健康检查和 Backend 连通性
  - 对 CRG 故障注入并确认原审查链路仍可执行
  - _Requirements: R1, R6, R7_

- [x] 7. 复用历史 Commit 执行 A/B 效果对比
  - 从现有任务数据或任务浅仓库解析之前使用的 base/head Commit，不猜测 SHA
  - 对同一仓库、同一 base/head 运行 CRG 关闭与开启两组分析
  - 采集 Token、总耗时、图谱构建/查询耗时、结论数和覆盖数据
  - 使用基准工作副本测量真实增量更新时间
  - 输出本机 PoC 对比报告；采纳率、漏报率和误报率依赖人工金标的部分明确标记待评审
  - 已完成 Python/Vue 混合仓库与独立 Java 仓库对比；更大规模人工金标盲评待后续决策
  - _Requirements: R2, R3, R4, R5_

- [ ] 8. 准备离线与 ARM64 交付（本机 PoC 通过后）
  - [x] 锁定 CRG 2.3.9，生成 linux/arm64 Alpine/musl 镜像、分卷、SHA256 和版本清单
  - [x] 纳入现有离线预检、导入、部署、健康校验和回滚脚本
  - [x] 在本机原生 ARM64 Docker 验证 Alpine/musl、非 root、CRG 版本和离线分卷完整性
  - [ ] 在麒麟 ARM64 64KB 页目标机执行 `01-precheck.sh` 至 `05-verify.sh`
  - _Requirements: R1, R6, R7_

- [x] 9. 完成影响链与反证核验展示
  - 审查抽屉展示图谱状态、计数、Commit、建图/查询耗时和六类影响链
  - 风险项展示核验状态、目标 Commit、触发可达性、支持证据和反证/保护逻辑
  - 历史报告保持兼容，新任务保存结构化核验字段
  - _Requirements: R3, R4, R8_

- [ ] 10. PoC 通过后决策跨任务长期图谱缓存
  - 改为仓库级稳定 checkout 路径，避免 CRG 绝对路径随任务 UUID 变化
  - 设计仓库锁、世代/版本兼容、容量上限、LRU 清理和损坏重建
  - 在 Java、Python、Vue 重复 Commit 样本上验证复用收益后再启用
  - _Requirements: R1, R6, R7_
