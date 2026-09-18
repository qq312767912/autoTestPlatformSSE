# WHartTest 内网 ARM64 增量升级包

应用版本：Backend `dev@d595a628-review-fix-r5`，Frontend `dev@d595a628-review-fix-r5`

源码基线：`dev@d595a628`（相对上一版 `1ed4e374` 共 3 个提交）

Vision MCP 继续复用 `01339484` 版本镜像；Actuator 使用包含动态页面导航修复的 R4 镜像。

## 本版本相对上一版 `1ed4e374` 的变化

本次是**前端、后端镜像的完整重建**（不是只改几个文件的挂载层），把此前只能靠
`hotfix/` 只读挂载临时交付的后端修复全部烤进镜像；前端则补上了上一版镜像构建之后
才提交的代码审查 HTML 报告导出功能。

| 类别 | 内容 |
| --- | --- |
| 用例审查稳定性 | 分片 10 → 20 行/批并恢复 2 路并发；重试次数取 `LLMConfig.max_retries` 且带上下限、退避改指数封顶 30 秒；单批失败只降级不中断整项；连续失败熔断；45 分钟总时间预算；报告新增「未覆盖用例行」 |
| 用例审查接口 | 列表接口不再回传运行中的 `_checkpoint`，改回传进度计数，避免前端 4 秒轮询产生 MB 级响应 |
| 代码审查正确性 | 差异范围改用**共同祖先**比较（本地 `git diff merge_base..head`、GitLab `straight=false`），不再把基准分支上已修好的改动误报成新增风险 |
| 代码审查报告 | 前端新增**导出 HTML 报告**与测试分析报告（上一版前端镜像构建于该功能提交之前，内网此前没有此入口） |
| 代码审查界面 | 长模块名概览单列布局与安全换行 |
| 用例审查界面 | 断点续审提示（`已完成 N/M 批，点击「重试」将从断点继续`）与覆盖率告警 |
| 用例审查技能 | `test-case-clarity-review` 统一为**全量版**（`SKILL.md` 5815 B + `review-rules.md` 4247 B），补齐商店 zip 与 manifest 条目；内网用 `24-sync-bundled-skills.sh` 落地并刷新 DB，见下文 |

> ⚠️ 由此带来一处**部署方式变更**：代码挂载层已从 `docker-compose.update.yml` 移出，
> 见下文「代码覆盖层（hotfix）的启用方式」。正常升级不再挂载任何后端代码文件。

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

## 用例审查重试与并发修复（本版本已内置镜像）

原先这套修复通过 `hotfix/` 只读代码覆盖层交付，**从本版本起已完整编译进 Backend 镜像**，
正常升级（`04-deploy.sh`）后即生效，不需要再执行任何 `*-apply-*-hotfix.sh`。

涉及的后端文件与作用：

| 文件 | 作用 |
| --- | --- |
| `testcases/review_service.py` | 分片调度、重试退避、失败降级、熔断、预算、断点续审 |
| `requirements/services.py` | 空响应不再丢失真实错误原因 |
| `testcases/serializers.py` | 列表接口不再回传运行中的 `_checkpoint`，只回传进度计数 |
| `testcases/views.py`、`management/commands/recover_stale_testcase_reviews.py` | 既有能力，保持不变 |

前端两个文件（`TestCaseReviewView.vue` 的断点续审提示与覆盖率告警、`service.ts` 的
`summary` 类型）随本次 Frontend 镜像一并上线。

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

#### 升级后的预期行为

- 450 条以上的用例审查按 20 行/批、2 路并发送审，480 条用例约 24 批 12 波，不会再顶到
  Celery 55 分钟软时限。
- 上游网关偶发 502 时，单批会自动重试（2/4/8/16… 秒退避，封顶 30 秒）而不是立刻整项失败。
- 少数批次最终仍失败时任务显示为「已完成」，页面出现覆盖率告警，报告首页给出
  `uncovered_chunks` / `uncovered_rows`，并可查看「未覆盖用例行」工作表。
- 对历史失败记录点击「重试」会从断点继续：已完成批次不再重跑，页面提示
  `已完成 N/M 批，点击「重试」将从断点继续`。
- 需要放宽或收紧重试时，在「模型配置」里改 `max_retries` 即可，无需改代码。
  分片大小与并发度是代码常量（`TESTCASE_REVIEW_CHUNK_SIZE` / `TESTCASE_REVIEW_MAX_WORKERS`），
  如需调整要改源码后重建 Backend 镜像。

> 注意：`review_service.py` 里的 `TESTCASE_REVIEW_*` 常量一旦调整，断点续审的签名
> （`signature`）会变化，历史 `_checkpoint` 将不再复用，任务会从头跑。

## 代码覆盖层（hotfix）的启用方式

从本版本起，`docker-compose.update.yml` **不再挂载任何后端代码文件**，唯一保留的挂载是
`supervisord.single-worker.conf`。原因：Backend 镜像已内置全部代码修复，再叠加旧副本会
盖掉镜像内容，并且让 `05-verify.sh` 失去“镜像是否真的更新”的判别力。

代码挂载层被移到了独立文件 `docker-compose.hotfix.yml`，**默认不叠加**。只有在“必须改后端
代码、又来不及重建镜像”时才启用，`19/20/22/23-apply-*-hotfix.sh` 会自动叠加它：

```bash
# 紧急热修复（临时启用挂载层）
bash 22-apply-code-analysis-ocr-hotfix.sh
bash 05-verify.sh      # 此时会失败：提示正处于挂载层模式

# 回到镜像模式（卸下挂载层）
docker compose -p offline-images \
  -f /projects/ai-test-platform/offline-images/docker-compose.offline.yml \
  -f docker-compose.update.yml \
  up -d --no-deps --force-recreate backend
bash 05-verify.sh      # 应全部通过
```

`05-verify.sh` 会检查 Backend 上是否存在 `/app/{testcases,code_analysis,requirements,orchestrator_integration,bundled_skills}`
的挂载点；存在即直接失败，提醒你当前生效的是挂载副本而非镜像。这是有意设计：

> 两个模式的语义要分清 ——
> **镜像模式**（默认）：生效代码来自镜像，`05-verify.sh` 的代码断言真正验证了镜像内容。
> **挂载模式**（紧急）：生效代码来自 `hotfix/`，镜像更新尚未得到验证。

## 代码审查诊断与单并发（21/22）

若多个代码审查任务连续显示“OpenCodeReview 平台调用失败”，执行：

```bash
bash 21-diagnose-code-analysis-ocr.sh
# 指定某条任务时：
TASK_ID=<代码审查任务UUID> bash 21-diagnose-code-analysis-ocr.sh
```

脚本会采集任务保存的 OCR 失败原因、执行记录、结果文件结构、Celery/OCR 进程、
相关 Worker 日志和一次 60 秒最小模型调用，不输出 GitLab Token 或模型密钥。

OpenCodeReview 单并发、超时诊断、全平台代码审查串行、OCR 单独重试等能力，
**本版本已全部内置在 Backend 镜像**，无需再执行 22 号脚本。相关内容如下（备查）：

- OpenCodeReview 首轮与续审均为单并发。
- 若达到动态时间上限仍未生成 JSON，任务会保存超时分钟数、退出码、结果文件状态和最后错误，
  不再只记录“返回状态 empty”。
- 取消/删除流程先停止指定任务的 OCR 进程组，再终止 Celery 运行任务；即使任务记录已删除，
  后台也会按取消处理，不再遗留孤儿 OCR 进程。
- 代码审查在全平台严格串行：前一个任务未结束时，后续任务显示“排队中”；OCR 失败但平台 AI
  降级分析完成时显示“降级完成”，并允许只重试 OCR，不重跑机器规则和已完成的降级分析。

## 代码审查差异范围修复（本版本已内置镜像）

代码审查此前把差异范围算成**两点比较**（本地 `git diff A B`、GitLab `straight=true`）。
两点比较会把「基准分支独有、目标分支尚未合并」的改动渲染成目标分支的**删除**，
而机器规则只扫删除行，于是基准分支上**已经修好的问题会被当成新增风险重复报出**
（典型误报：“删除 `@PreAuthorize` 注解”“删除关键配置：`enabled`”）。

正确语义是按**共同祖先**比较，即 `merge_base(A,B)..B`。修复落在两个后端文件上，
**本版本已编译进 Backend 镜像**，正常升级即生效，无需任何挂载操作：

| 文件 | 变化 |
| --- | --- |
| `code_analysis/services.py` | GitLab `compare` 的 `straight` 由 `True` 改为 `False`；新增 `GitLabClient.merge_base` / `LocalGitClient.merge_base`；`LocalGitClient.compare` 以共同祖先为起点，并拦下同 Commit 与基准/目标颠倒；`_managed_gitlab_repository` 把共同祖先交给 OCR 作 `--from`，机器规则与 OCR 看到同一段差异；`run_analysis` 把实际比较基准写回 `task.base_sha` |
| `code_analysis/views.py` | `validate-refs` 增加方向校验（目标是基准的祖先时返回 400 并提示“基准与目标疑似颠倒”），新增 `compare_base_sha` 与 `notice` 字段 |

如需临时热修复（不重建镜像），执行 `23-apply-code-analysis-mergebase-hotfix.sh`：它不换镜像、
**不改数据库、不需要 migrate**，只叠加 `docker-compose.hotfix.yml` 里的两个只读挂载文件并重建
Backend，随后拉起三个执行器。脚本内置守门断言：若覆盖层还是 `straight=true` 的两点比较旧版，
或者同步过程中挤掉了既有的 OCR 单并发、超时诊断、OCR 单独重试、LLM 配置复制等能力，
脚本会在重建之前直接失败，不会把旧版或残缺版推上内网。

#### 使用注意

- 修复只影响**新发起**的分析任务。**历史已有结论的任务不会自动重算**；需要刷新结论时请重新执行任务。
- Merge Request 模式行为不变（`diff_refs.base_sha` 本身即共同祖先）。
- “基准是目标的祖先”的相邻 Commit 对比行为不变（此时 `merge_base == base`）。
- GitLab 侧依赖 `/repository/merge_base` 接口。该接口不可用时校验接口按降级处理
  （仍返回 `compare_base_sha = base_sha`），不会阻断提交任务。

#### 回滚

本版本可回滚到上一版镜像 `update-1ed4e374-code-review-layout-r4-arm64`：

```bash
bash 06-rollback-app.sh
```

若之前叠加过 `docker-compose.hotfix.yml`，重建时不再传该 `-f` 即可卸下挂载层。

`05-verify.sh` 会验证容器和 HTTP 状态、**Backend 无代码覆盖层**、数据库迁移、Celery Worker
及代码审查/用例审查任务注册、OpenCodeReview、前端 HTML 报告导出、共享媒体文件实际下载、
Vision OCR、三个执行器的测试域名解析与 HTTP 访问，以及 Backend WebSocket 注册表中的
在线执行器数量。默认还会执行一次最小真实 OpenCodeReview 模型调用：临时构造 Java 导出 DTO
新增字段却遗漏 Excel 注解的变更，并验证审查结果确实识别该问题（不强制风险等级）。
该检查会消耗少量 Token，最长 4 分钟；仅在排查其他基础设施时可跳过：

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
../images/backend-d595a628-review-fix-r5-arm64.tar.gz.part000 ...（以实际分卷数为准）
../images/frontend-d595a628-review-fix-r5-arm64.tar.gz.part000
../images/actuator-update-178fb3ed-arm64-r4.tar.gz.partaa ... partab
```

`SHA256SUMS` 覆盖本次需传输的全部分卷，`03-import-images.sh` 会先校验再导入。
本次需传输 Backend 分卷和 Frontend 镜像；Vision MCP、Actuator 继续复用内网已导入镜像。

新版 Backend 为完整 Alpine/musl ARM64 构建，适配麒麟 ARM64 64KB 页环境：基于
`WHartTest_Django/Dockerfile.alpine` 两阶段构建，构建阶段自带 `build-base`/`musl-dev`/`cargo`，
运行阶段全局安装 `@alibaba-group/open-code-review` 并保留 `ocr` CLI（`ocr --version`
在镜像内可直接执行），Celery 并发为 8。Supervisor 的日志和 PID 分别写入 `/app/data/logs`
与 `/app/data/run`，通过已有数据卷落在 `/projects/ai-test-platform/offline-images/data`，
不再写入 `/var`。

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

## 内置技能统一为全量版（用例审查质量）

`test-case-clarity-review` 此前在仓库里有**三处不一致副本**：`WHartTest_Skills/`（技能商店源）
与镜像内的 `bundled_skills/` 是早期**精简兜底版**（`SKILL.md` 1127 B、`references/review-rules.md`
986 B，规则只是若干条一句话），而技能库交付的全量版是 `SKILL.md` 5815 B + `review-rules.md`
4247 B（9 类规则的判定要点与改写示例、输入处理、交付物、完成条件、质量边界）。本次已把各处
副本统一为全量版，并补齐商店分发件。

| 位置 | 角色 | 本次变化 |
| --- | --- | --- |
| `WHartTest_Skills/` | 技能商店源 | 目录改为全量版；新增 `test-case-clarity-review.zip` 与 `manifest.json` 条目 |
| `WHartTest_Django/bundled_skills/` | Backend 镜像构建源 | 改为全量版（下次重建镜像即带上） |
| `update_platform_version/deploy_env/hotfix/bundled_skills/` | 应急覆盖层 + 本同步脚本的源 | 改为全量版 |
| `<内网>/offline-images/skills/` | 容器内 `/app/bundled_skills` 的真实来源 | 由 `24-sync-bundled-skills.sh` 写入全量版 |

> ⚠️ 容器里的 `/app/bundled_skills` 来自宿主机 `offline-images/skills`（基础 compose 的外置挂载，
> 用于技能热插拔），**不是镜像内副本**——所以「把技能烤进镜像」对内网正常部署不生效。

### 改了目录还要刷新数据库

审查时真正送进模型的是**数据库里 Skill 的 `skill_content`**：
`review_service.build_skill_snapshot()` 优先使用 DB 内容，并把 media 目录下的
`references/*.md` 内联进去；只有在未选择 Skill 时才回退读 `/app/bundled_skills`。
宿主机目录只是「技能来源」，DB 内容由容器启动时 entrypoint 的 `init_skills` 从该目录同步。
**只改目录不刷 DB，界面与审查用到的仍是旧的精简版。**

`24-sync-bundled-skills.sh --apply` 会在容器内执行一次 `init_skills` 完成这一步，
等价于重启 Backend 所做的事，但不中断服务。

### 内网执行

```bash
cd /projects/ai-test-platform/update_platform_version/deploy_env

bash 24-sync-bundled-skills.sh            # 只诊断：分别列出缺失 / 内容不同 / 完全一致
bash 24-sync-bundled-skills.sh --apply    # 补齐技能文件 + 商店 zip + manifest 条目，并刷新 DB
bash 05-verify.sh
```

若诊断显示目标是「精简兜底版」，说明内网已有旧内容（默认不覆盖，避免顶掉手工调整过的技能），
升级时加 `--force`：

```bash
bash 24-sync-bundled-skills.sh --apply --force
```

写入前会把目标技能目录整体备份到 `deploy_env/backups/`，并单独备份 `manifest.json`；
写入后逐文件复查，同时校验容器内已可见且**确实是全量版**。

`05-verify.sh` 的技能断言也从「文件是否存在」升级为「是否为全量版」（校验
`## 9. 严重程度参考`、`## 1. 模糊和不可判定表述`、`## 完成条件`、`## 质量边界`、`## 交付物`
等章节及规则字符数），避免内网留着精简版也能通过。

商店安装（可选，与上面两条互不影响）：全量版同时以 `test-case-clarity-review.zip` 形式放在
`offline-images/skills/`，可在平台「Skill 商店 / 上传 Skill」中直接选择该 zip 安装。

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
