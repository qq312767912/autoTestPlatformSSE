# 代码审查证据链与续审：技术设计

## 架构与数据边界

沿用 `AnalysisTask.change_report`、现有反证函数、Celery 任务和 HTML 导出；不建第二套审查引擎。新增字段均为 JSON 可选字段，历史报告按缺省值渲染。版本号上调，避免新执行任务复用旧版缓存。续审使用已保存的候选及目标 Commit，不重新抓取 Diff，也不运行规则/OCR/全部 AI 语义审查。

每条 finding 增加 `verification_trace`：`target_sha`、`source_status`、`steps`（步骤名、目标、结果状态、有限定位）、`files_read`、`queries`、`counter_evidence`、`missing_steps`、`evidence_status`（complete/partial/unverified）。只保存路径、行号、短片段和失败分类；不保存 Token、完整工具输出或仓库副本。`verification_status` 与 `evidence_status` 分离：前者是裁决执行状态，后者是证据链完整度。

## 定向查证与校验

`_verify_findings_against_target` 先用 `file + subject + change_kind + 注解/配置键` 生成有限只读计划：读取目标文件 → 查询同一元素新约束 → 搜索定义与调用方 → 读取最多 N 个相关源码片段 → 比对反证。描述抽词仅用于结构化信息缺失时的兜底，记录兜底原因。每一步返回结构化状态；预算不足或工具异常加入 `missing_steps`，不等于未发生缺陷。

验证批次按候选 key 做输入/输出一一对应检查；裁决与严重度必须是允许枚举，布尔值和证据列表必须类型正确，confirmed 还需非空理由、目标版证据、可达触发及明确错误结果。对不合格批次有限重试；若返回混合有效/无效项，仅成功项裁决，失败项标记未核验，禁止复用 AI 初判为确定缺陷。保持原有真实静态扫描/直接证据例外。

## 手动续审

在 `AnalysisTaskViewSet` 增加 `POST /tasks/{id}/retry-verification/`，复用 `change_analysistask` 权限及项目成员检查。仅允许已完成、部分完成或降级完成任务；正在运行、缺少未核验项、无可用模型、目标 Commit 无法访问时返回 409。与全量重跑及 OCR 重试互斥，使用现有全局任务锁；Celery 新任务独立执行，记录尝试事件、开始/结束时间、原候选数、成功/失败数和耗时。

续审筛选 `verification_status in {verification_failed, not_verified}`；按 key 读取当前报告快照，调用相同反证函数，将返回结果仅合并到这些 key。其他 finding、已成功的核验、人工处置、需求测试点和文件覆盖结果保持原样。若发生并发改写或目标 SHA 改变，中止合并并保留原报告。候选被 rejected 时从可交付列表移至排除统计，并保留续审审计记录；更新风险计数与 HTML 数据。再次失败保留原项及原因。任务状态恢复为实际报告的 completed/degraded/partial，不因续审失败抹掉已有报告。

## 前端与 HTML

在现有任务详情中仅对有未核验项的已完成任务显示“继续核验未完成项”；点击后二次确认本次只核验候选，不重跑全文。排队和执行期间禁用重复点击，刷新后可按原任务查看结果。风险卡片及 HTML 增加证据链状态、目标 Commit、已检查路径/查询、未完成步骤、重试时间；已有 `verification_status` 继续独立展示。旧报告没有新字段时显示“历史报告未记录”，不伪造完整性。

## 安全与测试

查证只读；外部代码与业务文档按不可信数据处理。日志和报告不得含凭据、完整仓库代码或工具内部推理。续审执行人的 GitLab 凭据按现有 `executor` 规则获取，不能隐式使用创建人凭据。提交前跑后端单测、前端类型检查/构建、HTML 输出断言；用真实缺陷与正常迁移夹具对照，并记录覆盖、误报、未核验、耗时及 Token。真实内网仓库重跑另列运行验收，不把本地通过等同于内网已验证。
