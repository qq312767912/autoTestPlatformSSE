# WHartTest 内网离线升级 — 20261119-89ffd9ef-r2

源码基线：dev / 89ffd9ef。目标：麒麟 ARM64、64KB 页。

## 镜像策略

完整构建 Backend、Frontend、Semgrep、CRG、Actuator。Backend 必须使用 WHartTest_Django/Dockerfile.alpine，从 Python Alpine/musl 多阶段构建，安装 @alibaba-group/open-code-review 并保留 ocr CLI。Semgrep、CRG、Actuator 均为 Alpine/musl ARM64。Alpine 软件源使用 https://mirrors.aliyun.com/alpine，不回退官方源。

复用内网已有 PostgreSQL、Redis、qdrant-kylin-arm64:v1.16.0-page64k、MCP、Playwright MCP、Vision MCP、域名同步服务。微信插件宿主保持原状态，不启动。Skill Runner 为可选功能，内网旧配置未启用时继续使用 inprocess；不强制引入本地可选服务。

## 文件布局与执行

在内网建立本次独立发布目录，不要覆盖旧 deploy_env（原凭据和回滚挂载仍需要它）：

```
release-20261119-89ffd9ef-r2/
  deploy_env/  # 解压 deploy_env.zip
  images/      # 放入本次全部镜像分卷，保持文件名
```

暂停提交任务，等待后台任务和所有队列排空，然后执行：

```bash
cd deploy_env
sudo bash 00-preflight.sh
sudo bash 03-import-images.sh
sudo bash 02-backup.sh
sudo bash 04-deploy.sh 2>&1 | tee deploy-result.log
sudo bash 05-verify.sh
```

03 先导入并在麒麟目标内核验证 CLI，再停服务备份，减少停机时间。02 会保存 PostgreSQL 和 /app/data 完整备份，成功后应用保持停止；执行04继续升级，或执行06恢复。若备份失败会尝试启动已停止的容器。

端口、环境变量、网络、持久化目录、执行器凭据、资源限制从现有容器生成配置。保留现有前端80端口映射；8001只用于容器内部。保留现有网络为 external，不重建数据库/Redis/Qdrant。禁止 docker compose down -v 或清理旧镜像。

会移除遮蔽新代码的旧后端源码/启动配置挂载及旧前端根配置/静态页挂载；不会修改这些旧宿主文件。新镜像内置技能从镜像加载，旧外置技能目录不删除、不覆盖；数据库内用户技能及业务数据保持原持久化挂载。不会修改宿主机 /etc/hosts。

## 进程与业务规则

基础 HTTP 三进程（8001）；聊天、Agent和执行器连接保留在单个异步Web进程（8000），同时服务多个会话，不是聊天串行。Web事件循环卡死120秒后写线程栈并重启故障Web进程。

代码审查、用例审查各自solo单并发排队；普通任务、任务中心、UI调度各threads并发4，知识库threads并发2。UI实际执行容量由执行器控制。线程池不提供prefork硬超时，不意味着35个重型任务或模型调用能无限并行。

## 验收和回滚

05 检查镜像版本、健康、基础接口代理、六类消费者并发，以及三个执行器已通过WebSocket连接后端。继续人工验收登录、项目选择、多人聊天流式响应、审查排队、三个执行器执行/停止、文件下载。

04部署失败会尝试恢复原镜像和完整容器配置。手工回滚：

```bash
sudo bash 06-rollback-app.sh
```

回滚恢复容器配置/镜像，不自动撤销数据库迁移和新业务写入。完整数据库、文件备份在同级 backups；数据恢复须先停写并单独核对，不在自动回滚中盲目覆盖。升级成功后重建容器应使用本次 backups/时间/compose.runtime.json，它包含完整实际配置；不要再叠加旧单worker配置或热修复源码。

所有备份、inspect.json、compose配置可能包含密码，只保留在服务器，禁止上传Git或公开发送。

本地ARM64容器CLI验证不替代目标麒麟64KB内核验收；03导入步骤必须在目标机通过后才能部署。当前离线包不表示已远程完成部署。

## 已执行过02但发布失败：继续发布

保留当前 `.latest-backup`、`.images-validated` 和同级 backups/secrets/deploy_env.bak，不删除或重新执行02备份。将本完整ZIP解压到临时目录后，把文件覆盖复制到当前deploy_env（不要先删除当前目录），执行 `sudo bash 04-deploy.sh 2>&1 | tee /tmp/deploy-result.log`，然后执行05。

正式流程已集成prepare_assets.py和07-start-actuators.sh：在停服务之前备份原秘密文件和回滚配置为独立挂载，缺失时从备份/旧部署目录/仍可读取的现有容器恢复；存在冲突则停止，不随机生成密码。后端就绪后实际换取Token验证三个执行器的原凭据，再逐个重建。回滚同样使用固化文件，不依赖被覆盖的旧deploy_env路径。
