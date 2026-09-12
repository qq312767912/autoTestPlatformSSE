# WHartTest 内网 ARM64 增量升级包

应用版本：Backend `dev@347a4e12-alpine-r1`，Frontend `dev@ecff56e4-r1`

Vision MCP 继续复用 `01339484` 版本镜像；Actuator 使用包含动态页面导航修复的 R4 镜像。

本升级包替换或新增：

- Backend
- Frontend
- Vision MCP（新增）
- Actuator（新增一个 ARM64 镜像，运行三个相互隔离的执行器容器）

Vision MCP 必须使用带 `-r2` 后缀的 Alpine 修正版；不再使用最初的 Debian 镜像。

继续复用内网当前的 PostgreSQL、Redis、Qdrant、MCP、Playwright MCP 和微信插件宿主镜像及数据卷。

## 放置位置

将整个目录复制到：

```text
/projects/ai-test-platform/update_platform_version
```

当前正在使用的 Compose 文件默认应位于：

```text
/projects/ai-test-platform/offline-images/docker-compose.offline.yml
```

如果实际位置不同，执行脚本前设置：

```bash
export BASE_COMPOSE=/实际路径/docker-compose.offline.yml
```

## 执行顺序

```bash
cd /projects/ai-test-platform/update_platform_version/deploy_env

bash 01-precheck.sh
bash 02-backup.sh
bash 03-import-images.sh
bash 04-deploy.sh
bash 05-verify.sh
```

本次 `04-deploy.sh` 同时替换 Backend 和 Frontend，不重启 Vision MCP 或三个 Actuator。
Backend 重启后执行器会按自身重连机制重新注册；如未恢复，再单独执行
`07-start-actuators.sh`。

`05-verify.sh` 会验证容器和 HTTP 状态、数据库迁移、Celery Worker 及代码审查/用例审查
任务注册、OpenCodeReview、共享媒体文件实际下载、Vision OCR、三个执行器的测试域名解析与
HTTP 访问，以及 Backend WebSocket 注册表中的在线执行器数量。默认还会执行一次最小真实
OpenCodeReview 模型调用：临时构造 Java 导出 DTO 新增字段却遗漏 Excel 注解的变更，并验证
审查结果确实识别该问题（不强制风险等级）。该检查会消耗少量 Token，最长 4 分钟；
仅在排查其他基础设施时可跳过：

```bash
VERIFY_OCR_LIVE=0 bash 05-verify.sh
```

`07-start-actuators.sh` 在密钥文件缺失时会优先从仍在运行的执行器恢复；
无法恢复时才隐藏提示输入平台当前密码。密码仅写入权限为 `600` 的
`secrets/actuator_api_password`，不会写进镜像、YAML 或 Git。三个容器分别为：

- `wharttest-actuator-01`
- `wharttest-actuator-02`
- `wharttest-actuator-03`

三个容器均配置 `restart: unless-stopped`，宿主机或 Docker 重启后会自动恢复。
默认每个容器最多使用 1 核 CPU、2 GiB 内存和 1 GiB `/dev/shm`。如需调整，
可在启动前设置 `ACTUATOR_CPU_LIMIT`、`ACTUATOR_MEMORY_LIMIT`、
`ACTUATOR_SHM_SIZE`。

镜像文件位于与 `deploy_env` 同级的 `images` 目录。为满足单文件
300 MB 的传输限制，大镜像使用 `.part000` 起的分卷。

```text
../images/backend-347a4e12-alpine-r1-arm64.tar.gz.part000 ...（以实际分卷数为准）
../images/frontend-ecff56e4-r1-arm64.tar.gz.part000
../images/actuator-update-178fb3ed-arm64-r4.tar.gz.partaa ... partab
```

`03-import-images.sh` 可以自动按顺序合并并导入，无需手工生成 520 MB 的完整文件。
新版 Backend 为完整 Alpine/musl ARM64 构建，适配麒麟 ARM64 64KB 页环境，已包含
OpenCodeReview，并将 Celery 并发调整为 8。Supervisor 的日志和 PID 分别写入
`/app/data/logs` 与 `/app/data/run`，通过已有数据卷落在
`/projects/ai-test-platform/offline-images/data`，不再写入 `/var`。
本次需传输 Backend 分卷和 Frontend 镜像；Vision MCP、Actuator 继续复用内网已导入镜像。
如果只补充执行器镜像，可直接执行：

```bash
cat ../images/actuator-update-178fb3ed-arm64-r4.tar.gz.part* | gzip -dc | docker load
```

## 执行器列表反复消失的修复

后端的执行器 WebSocket 注册表为进程内状态，必须使用单个 Uvicorn worker：

```bash
bash 13-enable-single-worker.sh
```

该脚本只重建 backend 容器，不会重建其他服务或删除数据卷。

## Playwright MCP 离线浏览器修复

如果 `browser_evaluate` 提示 `chrome-for-testing is not installed`，执行：

```bash
bash 14-fix-playwright-mcp-browser.sh
```

脚本会自动使用 Playwright MCP 镜像中已有的 Chromium/Chrome，不会联网安装浏览器，且只重建 `playwright-mcp` 容器。

## Skill 生成文件下载修复

`docker-compose.update.yml` 已将 Backend 的持久化媒体目录以只读方式挂载到 Frontend：

```text
/projects/ai-test-platform/offline-images/data/media -> /app/data/media:ro
```

如果平台生成的 Excel、报告等文件提示“无法从网站提取文件”，执行：

```bash
bash 15-fix-artifact-download-volume.sh
```

该脚本只重建 Frontend，不会重启 Backend、数据库或执行器；`05-verify.sh` 会自动检查此挂载。

只停止执行器而不影响平台其他服务：

```bash
bash 08-stop-actuators.sh
```

出现应用故障时回退旧 Backend 和 Frontend：

```bash
bash 06-rollback-app.sh
```

如果新版已执行了与旧版不兼容的数据库迁移，不能只回退镜像，需要人工确认后恢复 `backups/` 下的 PostgreSQL 备份。本包不会自动覆盖数据库。

Backend 出现 `restarting` 或 8912 无法连接时，不要反复部署，先采集诊断日志：

```bash
bash 16-diagnose-backend-restart.sh
```

## 禁止操作

升级和回退过程中不要执行：

```bash
docker compose down -v
docker volume rm ...
```
## 执行器 R4 导航超时修复

将 `deploy_env.zip` 解压到 `/projects/ai-test-platform/update_platform_version/`，并将两个
`actuator-update-178fb3ed-arm64-r4.tar.gz.part*` 镜像分卷放到
`/projects/ai-test-platform/update_platform_version/images/`。

执行：

```bash
cd /projects/ai-test-platform
chmod +x update_platform_version/deploy_env/deploy-actuator-r4.sh
./update_platform_version/deploy_env/deploy-actuator-r4.sh
```

该脚本仅更新三个执行器，并同时保留内网域名映射；不会重启前端、后端、数据库和其他服务。
部署包不包含真实密码；密钥文件缺失时，脚本会优先从仍在运行的执行器恢复，无法恢复时才隐藏提示输入。
