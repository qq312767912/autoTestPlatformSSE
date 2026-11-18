# 后台任务队列隔离

知识库入库、代码审查、用例审查不再与通用任务竞争一个执行槽位。

| 队列 | Worker / Supervisor program | 任务 |
|---|---|---|
| knowledge | celery_knowledge | knowledge.process_document |
| code_analysis | celery_code_analysis | code_analysis.tasks.*（含重试） |
| testcase_review | celery_testcase_review | testcases.review_tasks.* |
| task_center | celery_task_center | task_center.tasks.* |
| celery | celery_worker | 其余默认任务 |

各 worker 独立进程、独立节点名、solo、并发1。不使用 prefork，保持麒麟 ARM64 Alpine 运行方式。代码审查内部的全局业务锁保持原样。

## 部署与验收

- 同一发布中更新 Django settings.py 和 /app/supervisord.conf；新镜像 Dockerfile 已复制两者。新增四个常驻 Django/Celery 进程，需要监测实际内存与模型端并发承载能力；任务隔离不代表模型服务也已隔离。
- 内网旧镜像热修复必须先取得容器实际文件、备份并基于该版本做最小补丁。不能拿本地 settings.py 整文件覆盖内网。先准备新增专用消费者，再启用任务新路由。
- 选择无运行中任务的窗口更新，避免重启中断长任务。已入旧 celery 队列的消息不会自动迁移，保留默认 worker 自然处理；不要清空 Redis 或手工挪动未确认任务。
- 验证五个 Celery worker 正常运行且名字唯一，每个只订阅表中对应队列。再同时提交审查任务与小文档，确认知识库专用日志出现文档处理和完成，不等待审查结束。
- 新增日志为 /app/data/logs/{queue}_worker_{err,out}.log，单文件10MB，保留3份轮转。

## 回退

先恢复旧路由，让新增任务回到原消费者；新队列中的任务自然执行完后，再撤掉专用消费者并恢复旧 Supervisor 配置。不要只停新 worker，否则遗留消息会无人处理。
