# WHartTest 内网 ARM64 增量升级包

应用版本：Backend `dev@1ed4e374-code-review-layout-r4`，Frontend `dev@1ed4e374-code-review-layout-r4`

Vision MCP 继续复用 `01339484` 版本镜像；Actuator 使用包含动态页面导航修复的 R4 镜像。

代码审查现使用独立 LLM 配置。系统管理员进入“代码审查”页面，点击右上角
“审查模型配置”，填写 OpenAI 兼容 API 地址、模型名称和 API Key。密钥在数据库中
加密保存且接口永不回显；该配置仅供 OpenCodeReview、AI 风险分析和测试影响分析使用，
不会改变平台对话模型。升级迁移会一次性复制当前启用的通用 LLM，避免首次升级中断，
复制后两套配置互不影响。
配置弹窗也可直接选择平台已有 LLM，并点击“复制并使用”。复制由 Backend 在服务端完成，
已有 API Key 不会发送到浏览器；之后仍可单独调整代码审查模型参数。
“代码仓库”页面支持删除未被项目仓库引用的 GitLab 连接；仍被引用时会明确提示关联仓库数量，
避免误删正在使用的连接。

本次需要新增传输并替换：

- Backend
- Frontend

Vision MCP 继续复用带 `-r2` 后缀的 Alpine 修正版；三个执行器继续复用 R4 ARM64
镜像。`03-import-images.sh` 和 `05-verify.sh` 仍会检查这些复用镜像与容器，确保依赖完整。

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

本次 `04-deploy.sh` 同时替换 Backend 和 Frontend，不重启 Vision MCP。Backend 网络恢复后，
脚本会自动重新拉起并检查三个执行器，避免执行器在 Backend 短暂不可达期间退出后无法恢复。
如需单独恢复执行器，也可以执行 `07-start-actuators.sh`。

如果用例审查长时间停在某个分片，执行：

```bash
bash 17-diagnose-testcase-review.sh
```

脚本只读取 Backend、Celery 和最近审查记录，日志写入 `deploy_env/logs`，不会修改任务或数据库。

针对小文件也超时的情况，可以进一步执行：

```bash
bash 18-diagnose-small-testcase-review.sh
```

默认检查最新一条审查记录；也可通过 `REVIEW_ID=8` 指定记录。脚本会统计 Excel 实际非空行，
按**当前挂载代码里的真实常量**估算批次数、并发度、顺跑耗时与最坏耗时，并标出顺跑是否已经
超过 Celery 55 分钟软时限（`happy_path_exceeds_celery_soft_limit`），同时报告当前代码属于
「修复前（小分片串行）」还是「修复后（可配置重试 + 并发分片）」。脚本还会对当前激活模型发起
一次60秒、最多256输出 Token、零重试的最小 `OK` 调用，并报告推理内容长度。脚本不会输出 API Key。

## 用例审查免构建热修复

当前 Backend 镜像不变时，可直接应用 `hotfix/` 中的只读代码覆盖层：

```bash
bash 19-apply-testcase-review-hotfix.sh
bash 05-verify.sh
```

脚本只重新创建 Backend，并在其恢复后重新拉起三个执行器；不会重启数据库、Frontend 或
Vision MCP，不会修改数据卷。热修复已写入 `docker-compose.update.yml`，后续使用这组 Compose
文件重建 Backend 时仍会生效。

针对大 Excel 在内网模型网关超时的增量修复（本文简称「用例审查重试与并发修复」），
也可使用语义更明确的入口：

```bash
bash 20-apply-large-testcase-review-hotfix.sh
bash 05-verify.sh
```

该脚本会调用 `19-apply-testcase-review-hotfix.sh`。`hotfix/` 覆盖层包含以下四个文件，
全部只读挂载，覆盖后仅重建 Backend：

| 覆盖文件 | 作用 |
| --- | --- |
| `testcases/review_service.py` | 分片调度、重试退避、失败降级、熔断、预算、断点续审 |
| `requirements/services.py` | 空响应不再丢失真实错误原因 |
| `testcases/serializers.py` | 列表接口不再回传运行中的 `_checkpoint`，只回传进度计数 |
| `testcases/views.py`、`management/commands/recover_stale_testcase_reviews.py` | 既有能力，保持不变 |

> 本次修复还改动了前端两个文件（`TestCaseReviewView.vue` 的断点续审提示与覆盖率告警、
> `service.ts` 的 `summary` 类型），它们属于**纯展示增强**，不影响后端行为，因此不在这套
> 热修复里。前端需要在下一次 Frontend 镜像重建时一并带上；在此之前页面照旧可用，
> 只是看不到「已完成 N/M 批」提示与覆盖率告警。

#### 分片与并发（修复 480 条用例跑不完的问题）

| 项 | 修复前 | 修复后 |
| --- | --- | --- |
| 每批用例数 | 10 行 | **20 行** |
| 批次调度 | 串行 | **2 路并发** |
| 480 条用例批次 | 48 批 | 24 批 |
| 顺跑预估（单批 120 秒） | 96 分钟（超 Celery 55 分钟软时限） | **24 分钟** |

#### 重试、降级与熔断（修复 502 直接整项失败的问题）

- 单批最多尝试 `LLMConfig.max_retries + 1` 次，下限 3 次、上限 5 次；界面上把
  `max_retries` 设为 0 也不会退化成零容错。退避为 2/4/8/16… 秒并封顶 30 秒。
- 底层 SDK 重试保持关闭（`max_retries=0`），重试统一由外层管理，避免两层重试叠加。
- **单批最终失败只降级、不中断整项**：该批记为「未覆盖」，其余批次照常出报告，报告首页汇总
  中给出 `uncovered_chunks` / `uncovered_rows`，并新增「未覆盖用例行」工作表逐行列出。
- 连续 3 批失败且迄今无任何成功批次时判定网关整体不可用，**提前终止**，不再逐批耗尽重试。
- 整项总时间预算 45 分钟（小于 Celery 55 分钟软时限），预算耗尽后剩余批次标记为未送审。
- 只有**所有**批次都失败时才整项失败，错误信息为
  `共 N/M 批模型调用失败，未生成报告。首个错误：…`。

#### 内网操作步骤

```bash
cd /projects/ai-test-platform/update_platform_version/deploy_env

# 0) 先取现场证据（可选，但建议保留）：确认是不是「顺跑即超时」，并记录当前代码版本
bash 18-diagnose-small-testcase-review.sh

# 1) 备份回滚点：覆盖层目录 + 叠加用的 Compose（两者必须一起备份）
tar czf /tmp/testcase-review-hotfix-backup-$(date +%Y%m%d-%H%M%S).tgz hotfix docker-compose.update.yml

# 2) 用升级包替换 deploy_env（含新的 hotfix/ 与 docker-compose.update.yml）

# 3) 应用（会重建 Backend，并在其恢复后拉起三个执行器）
bash 20-apply-large-testcase-review-hotfix.sh

# 4) 校验：常量、挂载、Skill、Celery 任务注册
bash 05-verify.sh
```

脚本最后的 `[验证]` 会打印实际生效参数，形如：

```
review_service 生效参数：CHUNK_SIZE=20 WORKERS=2 ATTEMPTS=3(上限5) BACKOFF=2~30s BUDGET=45min BREAKER=3
testcase review hotfix OK
```

#### 生效后的预期行为

- 对历史失败记录点击「重试」会从断点继续：已完成批次不再重跑，页面提示
  `已完成 N/M 批，点击「重试」将从断点继续`。
- 少数批次仍失败时任务显示为「已完成」，但列表会出现覆盖率告警，报告里能查到未覆盖的行。
- 需要放宽或收紧重试时，在「模型配置」里改 `max_retries` 即可，无需改代码；分片大小与并发度
  是代码常量，如需调整要改 `hotfix/testcases/review_service.py` 后重跑 19/20 脚本。

> 注意：`review_service.py` 里的 `TESTCASE_REVIEW_*` 常量一旦调整，断点续审的签名
> （`signature`）会变化，历史 `_checkpoint` 将不再复用，任务会从头跑。

#### 回滚

热修复是只读挂载，回滚只需还原「覆盖层目录 + Compose」并重建 Backend：

```bash
cd /projects/ai-test-platform/update_platform_version/deploy_env
tar xzf /tmp/testcase-review-hotfix-backup-<时间戳>.tgz

compose=(docker compose -p offline-images \
  -f /projects/ai-test-platform/offline-images/docker-compose.offline.yml \
  -f docker-compose.update.yml)
"${compose[@]}" config --quiet
"${compose[@]}" up -d --no-deps --force-recreate backend
"${compose[@]}" up -d --no-deps actuator-01 actuator-02 actuator-03
```

> 回滚必须**同时**还原 `docker-compose.update.yml`。新版 Compose 里多了一条
> `hotfix/testcases/serializers.py` 的挂载；若只还原 `hotfix/` 而不还原 Compose，
> 该文件不存在，Docker 会把挂载点创建成目录，Backend 会启动失败。
>
> 回滚后 `bash 05-verify.sh` 中的用例审查断言会失败（断言已按修复后的常量与函数更新），
> 这是**预期结果**，不代表回滚失败。

若多个代码审查任务连续显示“OpenCodeReview 平台调用失败”，执行：

```bash
bash 21-diagnose-code-analysis-ocr.sh
# 指定某条任务时：
TASK_ID=<代码审查任务UUID> bash 21-diagnose-code-analysis-ocr.sh
```

脚本会采集任务保存的 OCR 失败原因、执行记录、结果文件结构、Celery/OCR 进程、
相关 Worker 日志和一次 60 秒最小模型调用，不输出 GitLab Token 或模型密钥。

确认内网低算力导致 OpenCodeReview 多并发超时后，执行单并发热修复：

```bash
bash 22-apply-code-analysis-ocr-hotfix.sh
bash 05-verify.sh
```

该脚本不更换镜像和数据卷，只重新创建 Backend。OpenCodeReview 首轮与续审均改为
单并发；若达到动态时间上限仍未生成 JSON，任务会保存超时分钟数、退出码、结果文件
状态和最后错误，不再只记录“返回状态 empty”。热修复同时改造取消/删除流程：
先停止指定任务的 OCR 进程组，再终止 Celery 运行任务；即使任务记录已删除，
后台也会按取消处理，不再遗留孤儿 OCR 进程。代码审查在全平台严格串行：
前一个任务未结束时，后续任务显示“排队中”；OCR 失败但平台 AI 降级分析完成时
显示“降级完成”，并允许只重试 OCR，不重跑机器规则和已完成的降级分析。

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
../images/backend-1ed4e374-code-review-layout-r4-arm64.tar.gz.part000 ...（以实际分卷数为准）
../images/frontend-1ed4e374-code-review-layout-r4-arm64.tar.gz.part000
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
