# 设计

新增项目级 SkillAssessmentSession，锁定 source_version、包哈希、测试集、测评模式与九维量表。原包复制到 MEDIA_ROOT 下独立目录，候选以私有路径和哈希存储，不提前创建公开 SkillVersion。使用现有 GeneralSkillEvaluator（增加显式结构体检模式）、预算与成对评委；不要求 business_metrics。

模式：structure（八项可评，实测表现待执行，最高可评77分，无百分制总分）；text（真实模型文本任务，声明工具/脚本依赖则拒绝模拟）；external（人工确认真实产出，记录操作人、来源、对应精确包哈希，不将其宣称为平台自动执行）。外部模式候选必须重新提交对应候选哈希的产出，不能复用基线产出冒充候选。

候选生成只编辑 SKILL.md 与 references 下 Markdown；路径白名单、体积与静态校验沿用现有包服务。每个候选记录父基线、diff、测评、成对验证与人工选择理由。只有显式提交时才从副本生成新的 SkillVersion，并进入既有验证审批，源 Hub 包不变。

网络调用在短事务领取执行权后进行，失败落库；同一会话串行，预算累计，原始输入不可修改。API 独立于 case-review-evolution。UI 为 Hub 模态工作台，可只测评或进一步生成、重测、选择、提交。

评测和生成通过 Celery 后台任务执行；API 短事务登记唯一 job_id，重复点击不重复派发，工作进程按租约领取。失败在会话中可查，关闭页面不影响执行。耗时请求不占浏览器长连接。原材料私有副本在复制时按包标准反向脱敏，哈希仍与原始标准化包一致。只有提交 Hub 时才推进公开版本号。


## Agent 任务交接修订

新增 `SkillAgentTaskBinding`，OneToOne 绑定 ChatSession，保存 source_version 与 source_sha256。POST `/skill-agent-tasks/` 创建新会话和绑定；GET 按项目、当前用户及 session_id 返回绑定。AgentLoop 在加载工具前以服务端绑定解析版本并校验完整性；旧 ChatAPI 不支持此工具版本约束，拒绝绑定任务。页面即使配置非流式也将绑定任务送往 AgentLoop。Hub external API 与 worker 已关闭，旧数据仅查看，保留底层历史结构以兼容读取。
