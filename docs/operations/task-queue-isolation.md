# 后台任务队列隔离

知识库入库、代码审查、用例审查不再与通用任务竞争一个执行槽位。

| 队列 | Worker / Supervisor program | 任务 |
|---|---|---|
| knowledge | celery_knowledge | knowledge.process_document |
| code_analysis | celery_code_analysis | code_analysis.tasks.*（含重试） |
| testcase_review | celery_testcase_review | testcases.review_tasks.* |
| task_center | celery_task_center | task_center.tasks.* |
| ui_execution | celery_ui_execution | ui_automation.run_ui_script_batch |
| celery | celery_worker | 其余默认任务 |

各 worker 独立节点与队列。代码审查、用例审查各用 solo 单并发；普通任务、任务中心、UI 调度各使用 threads 并发4，知识库使用 threads 并发2。采用线程池避免 prefork 复制 OCR/ONNX 等重型依赖；UI 真正执行容量由执行器控制。代码审查内部的全局业务锁保持原样。

## 部署与验收

- 同一发布中更新 Django settings.py 和 /app/supervisord.conf；新镜像 Dockerfile 已复制两者。新增四个常驻 Django/Celery 进程，需要监测实际内存与模型端并发承载能力；任务隔离不代表模型服务也已隔离。
- 内网旧镜像热修复必须先取得容器实际文件、备份并基于该版本做最小补丁。不能拿本地 settings.py 整文件覆盖内网。先准备新增专用消费者，再启用任务新路由。
- 选择无运行中任务的窗口更新，避免重启中断长任务。已入旧 celery 队列的消息不会自动迁移，保留默认 worker 自然处理；不要清空 Redis 或手工挪动未确认任务。
- 验证六个 Celery worker 正常运行且名字唯一，每个只订阅表中对应队列。再同时提交审查任务与小文档，确认知识库专用日志出现文档处理和完成，不等待审查结束。
- 新增日志为 /app/data/logs/{queue}_worker_{err,out}.log，单文件10MB，保留3份轮转。

## 回退

先恢复旧路由，让新增任务回到原消费者；新队列中的任务自然执行完后，再撤掉专用消费者并恢复旧 Supervisor 配置。不要只停新 worker，否则遗留消息会无人处理。

## 35 人使用场景的 Web 故障隔离（2026-11-19）

- 8000：保留单进程长会话、UI HTTP 与 WebSocket，维持进程内执行器连接和停止信号的归属。
- 8001：新增 3 个基础接口 worker；前端仅将 `/api/accounts/`、`/api/auth/`、`/api/token/` 以及项目根列表/数字 ID 详情代理到此端口，嵌套项目业务仍走 8000。端口仅 Docker 网络内使用，不新增宿主机映射。
- Web 进程事件循环超过 120 秒无法推进时，后台线程将所有 Python 线程栈追加到 `/app/data/logs/web-stall.log`，随后只退出故障 Web 进程（退出码70），由 Uvicorn master 或 Supervisor 拉起。正常异步等待和流式响应不会触发。可用 `WEB_STALL_TIMEOUT_SECONDS=0` 关闭。
- 这是一种可用性保护，不是对历史卡死根因的证明；线程无法获得 GIL、进程被外部暂停等情况不能靠进程内 watchdog 恢复。模型网关120秒超时仍须在模型服务排查；不要据此继续无界增加模型调用并发。
- Celery 保持六类独立消费者，仅代码审查/用例审查为 solo，其余为 threads 多并发，补齐 UI healing 路由和用例审查显式注册；`solo` 和 `threads` 模式不提供 prefork 的软/硬时限强制终止能力，不能把任务装饰器中的 time_limit 当作可靠熔断。
- 消费者数量增加不等于已证明35人全部运行重型任务的容量；需要观察模型限流、数据库连接、主机内存和实际任务等待时间。

### 验证记录

配置/任务注册、事件循环生命周期、线程栈保存和真实阻塞进程退出测试通过。独立临时容器中暂停8000进程后，聊天接口超时；35路基础接口请求全部收到预期200/401/403响应，最慢5.661秒。该测试验证故障隔离和未认证基础接口并发，不代表35个真实审查任务吞吐量。

内网使用 `update_platform_version/worker-runtime-20261119/` 增量包，替代早期仅拆队列的 worker-isolation 包。目标镜像和 Supervisor 精确匹配后，备份后端原配置、ASGI、settings和前端配置，在目标Python运行时预检，先启动新端口再切换Nginx；失败自动回退单消费者兼容模式。不要用本地dev整文件覆盖内网旧镜像。容器热修复在重建时会丢失，稳定后应固化到下一次离线镜像。

### 业务并发规则修正

| 队列 | 池 | 同时执行任务数 |
|---|---|---:|
| code_analysis | solo | 1 |
| testcase_review | solo | 1 |
| celery | threads | 4 |
| task_center | threads | 4 |
| ui_execution | threads | 4 |
| knowledge | threads | 2 |

代码审查与用例审查各自排队，不占用对方的消费者。UI 的4个槽位用于并行调度不同批次，不覆盖执行器自身的容量与互斥规则。线程并发是有界的，超出槽位的任务仍排队；不是无限并发。聊天仍走异步 Web，并非 Celery solo 队列。

新增隔离的真实 Celery 内存 Broker 测试：4个受控任务在 threads 池能同时进入执行，在 solo 池峰值始终为1。测试不调用真实模型、不触碰生产Redis。
