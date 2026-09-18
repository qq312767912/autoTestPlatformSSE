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

> ⚠️ 由此带来一处**部署方式变更**：代码挂载层已从 `docker-compose.update.yml` 移出，
> 见下文「代码覆盖层（hotfix）的启用方式」。正常升级不再挂载任何后端代码文件。

> ⚠️ **本包不含「报告导出改用代码仓库名」这个改动**（它属于前端产物，需要重建 Frontend
> 镜像才能随本包交付）。但 `05-verify.sh` 已经带上对应的产物断言，所以**只升级本包而不处理
> 前端，校验会在该断言处失败**。处理方式见下文「前端热修复通道（`../frontend-hotfix/`）」。
> 另外本包的 `04-deploy.sh` 已内置技能同步与数据库快照刷新，「内置技能」一节随之改写。

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
bash 04-deploy.sh      # 内置技能同步 + 数据库快照刷新已自动完成，无需额外步骤
bash 05-verify.sh
```

> ✅ **内置技能已内置在 `04-deploy.sh` 中，不再需要手工执行同步命令。**
> 04 会在替换容器**之前**把包内技能（`hotfix/bundled_skills/`）同步到宿主机外置技能目录
> （`<安装根目录>/offline-images/skills`，即基础 compose 挂载给容器的 `/app/bundled_skills`），
> 并在 Backend 健康后自动执行 `init_skills` 刷新数据库快照。顺序是刻意安排的：先刷好宿主机目录，
> 容器启动时读到的就是包内版本。只有在需要单独诊断、或 04 之外补救时才手工跑 24 脚本。
> 详见下文「内置技能（已内置，24 为可选）」。

本次 `04-deploy.sh` 同时替换 Backend 和 Frontend，不重启 Vision MCP。Backend 网络恢复后，
脚本会自动重新拉起并检查三个执行器，避免执行器在 Backend 短暂不可达期间退出后无法恢复。
如需单独恢复执行器，也可以执行 `07-start-actuators.sh`。

## 内置技能（已内置，24 为可选）

### 为什么必须落到宿主机目录

| 事实 | 说明 |
| --- | --- |
| 技能目录是外置的 | 基础 compose 有 `./skills:/app/bundled_skills:ro`，容器内看到的就是宿主机 `<安装根目录>/offline-images/skills` |
| 不随镜像走 | 把技能烤进 Backend 镜像对正常部署**不生效**，会被上面这条挂载盖住 |
| 不随升级包自动落地 | `deploy_env.zip` 只含 `deploy_env/`，不含 `offline-images/`，解压升级包不会更新宿主机技能目录 |
| 05-verify 校验的就是它 | 技能断言先查该宿主机目录经挂载后在容器内的可见性；但审查实际读的是 DB 快照 + 媒体目录副本，故 05 同时校验这两处（见下文） |

所以升级包必须自己把技能「送进去」。这件事现在由 `04-deploy.sh` 自动完成，**不需要人工干预**。

### 04-deploy.sh 自动做了什么

1. **替换容器之前**：以 `24-sync-bundled-skills.sh --apply --force` 把 `hotfix/bundled_skills/`
   同步到宿主机技能目录（写前自动整目录备份到 `deploy_env/backups/skills-backup-<时间戳>.tar.gz`）。
   放在容器创建之前是刻意的：Backend 启动时 `entrypoint.sh` 会跑 `init_skills`，先刷好目录，
   容器第一次起来读到的就是包内版本。
2. **Backend 健康之后**：执行 `init_skills` 刷新数据库里的技能快照。这一步不能省——Backend 只在
   容器**被重建**时才自动跑 `init_skills`，而只更新技能文件、不换镜像时容器不会重建，数据库会停在旧内容。
3. **目标目录的确定方式**：从 `BASE_COMPOSE` 推导（基础 compose 里是相对挂载 `./skills`，基准是
   compose 文件所在目录），不硬编码安装根目录。若推导结果与运行中容器的**实际挂载源**不一致，
   脚本**直接中止**，而不是把技能写进一个容器根本看不到的目录——这正是本次内网踩坑的同类隐患。

### 24-sync-bundled-skills.sh（可选：诊断 / 补救）

```bash
cd /projects/ai-test-platform/update_platform_version/deploy_env

bash 24-sync-bundled-skills.sh                    # 只诊断，列出「缺失 / 内容不同 / 一致」三类文件
bash 24-sync-bundled-skills.sh --apply            # 只补缺失文件，已存在的不同内容不动
bash 24-sync-bundled-skills.sh --apply --force    # 内容不同的也覆盖（写前自动备份整个技能目录）
```

技能源取自包内的 `hotfix/bundled_skills`（无容器依赖）。补文件后**无需重启容器**，只读挂载即时
生效。但独立运行时它**不会**替你刷数据库（04 调用时会，且会跳过这段提示），需要自己补一条：

```bash
docker exec wharttest-backend /opt/venv/bin/python /app/manage.py init_skills
```

然后重跑 `bash 05-verify.sh` 复核。本版本的技能断言分两段、共四层：

- **宿主机技能目录**（`/app/bundled_skills`，即 `offline-images/skills`）：三个文件**是否存在**、
  `references/review-rules.md` 是否含第 9 条「严重程度参考」、`SKILL.md` 是否含「完成条件」章节。
  因此「精简兜底版」和「精简 SKILL.md + 完整规则明细」的混搭都会被明确拦下。
- **审查实际读取的路径**：`testcases/review_service.py` 的 `build_skill_snapshot()` 在**选中 Skill** 时
  读的是 DB 的 `Skill.skill_content`（=SKILL.md）+ MEDIA_ROOT 下该副本的 `references/*.md`
  （`init_skills` 用 `_sync_files` 复制过去的那份）；**只有未选中 Skill** 时才回退读 `/app/bundled_skills`。
  所以还要校验数据库快照与媒体目录副本，否则「文件到位但没跑 `init_skills`」这种状态会跑绿、
  而审查仍在用旧规则（DB 里尚无该技能记录时本段会自动跳过并说明）。

媒体目录那份副本可以直接在宿主机上核对（基础 compose 是 `./data:/app/data`，所以 `MEDIA_ROOT` 就是
`<安装根目录>/offline-images/data/media`）：

```bash
ls -l /projects/ai-test-platform/offline-images/data/media/skills/*/*/references/review-rules.md
# 期望 4247 字节；若只有 986 字节说明这份副本还是精简版，即 init_skills 没跑到
```

手工用 `SKILLS_DIR=...` 指定目标目录时，脚本会跳过「与实际挂载源比对」的硬校验，只告警。

### 版本口径

包内 `hotfix/bundled_skills/test-case-clarity-review` 是**完整版**，与用户交付的技能包
`test-case-clarity-review.zip` 逐字节一致，共 3 个文件：

| 文件 | 字节数 | 作用 |
| --- | --- | --- |
| `SKILL.md` | 5815 | 审查流程主体（输入处理 / 审查方法 / 交付物 / 报告内容要求 / 完成条件 / 质量边界） |
| `references/review-rules.md` | 4247 | 9 条审查规则明细（模糊表述、前置条件与数据、步骤可执行性、预期完整性、正/异常与边界覆盖、数据一致性、证据与可追溯性、可维护性、严重程度参考） |
| `agents/openai.yaml` | 294 | 界面元数据（显示名、简介、默认提示词），无执行逻辑 |

这就是 04 自动同步时固定加 `--force` 的原因：

- `--apply` 单独使用**不覆盖**内容不同的既有文件——这是有意的保护，但会带来一个坑：如果宿主机
  目录里已经存在**精简兜底版**的 `SKILL.md`（1127 字节），脚本会把它报成「内容不同」并跳过，却
  仍然补上完整的 `references/review-rules.md`，最终出现「精简 SKILL.md + 完整规则明细」的**混搭**。
- 04 固定用 `--force`，保证三文件一次性统一为完整版。写前会把宿主机技能目录整体备份到
  `deploy_env/backups/skills-backup-<时间戳>.tar.gz`，需要回退时解开该包覆盖回去即可。

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

## 前端热修复通道（`../frontend-hotfix/`）

`deploy_env/hotfix/` 那套 Python 代码覆盖层**只能覆盖 Backend 容器内的文件**。报告导出这类
纯前端能力（在浏览器里拼 HTML 再 Blob 下载）传不过去，需要另一条通道：把已构建的前端产物
只读挂到 Nginx 站点根目录 `/usr/share/nginx/html`，再重建一次 Frontend 容器。

当前该通道承载的修复是：**报告导出标题与下载文件名改用代码仓库名**
（`代码审查报告_<仓库名>` / `测试分析报告_<仓库名>`，原为平台项目名）。

```bash
cd /projects/ai-test-platform/update_platform_version/frontend-hotfix
bash 25-apply-frontend-report-title-hotfix.sh          # 应用
bash 25-apply-frontend-report-title-hotfix.sh --revert # 回退
```

### 与本包校验脚本的交互（重要）

`05-verify.sh` 从本版本起新增一条**报告命名口径断言**：在前端产物里定位
`代码审查报告_` / `测试分析报告_` 前缀后，检查其后 200 字符窗口内是否出现 `repository_name`。
这只是「字符串存在」不够——旧版同样含 `代码审查报告_`（旧写法紧跟的是 `project_name`），
两种情况都能过；加上邻近窗口判据后，旧产物会被明确拦下。

推论：**如果内网 Frontend 镜像尚未包含本次修复，新 `05-verify.sh` 会在该断言处失败**，
并提示产物不是「报告名_代码仓库名」口径。两条出路：

| 出路 | 做法 | 代价 |
| --- | --- | --- |
| 临时（推荐先做） | 执行 `../frontend-hotfix/25-apply-frontend-report-title-hotfix.sh` | 生效的是挂载产物而非镜像本体 |
| 永久 | 重建 `wharttest-250-frontend` 镜像，走 `03-import-images.sh` + `04-deploy.sh` | 需要 ARM64 构建机 |

注意 `04-deploy.sh` 用 `base + docker-compose.update.yml` 重建 Frontend，**不带**该覆盖层，
所以下一次正常升级会自动卸下挂载、站点回到镜像内产物 —— 镜像还没重建的话即旧口径，
此时重跑一次 25 号脚本即可。

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
