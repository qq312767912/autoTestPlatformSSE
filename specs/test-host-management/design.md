# 测试域名配置模块技术设计

## 1. 设计结论

V1 采用“Django 管理与发布 + 宿主机同步服务”架构，不引入 CoreDNS 容器。Django 负责草稿、版本、权限、审计和诊断；宿主机同步服务使用部署密钥读取已发布快照，并将受管区块应用到宿主机和明确允许的容器。

选择该方案的原因：

- 不占用宿主机 53 端口，不与内网 DNS/systemd-resolved 冲突。
- 不需要为 Docker 网络固定网段和 DNS 容器 IP。
- Backend 不挂载 Docker Socket，不获得宿主机 root 或容器管理权限。
- 现有 Backend、Recorder、Playwright MCP 和 Actuator 无需引入新的 DNS 客户端依赖。
- 容器重启丢失动态 hosts 内容时，同步服务在下一轮巡检自动修复。

CoreDNS 作为后续可选演进：当内网运维能提供统一 DNS 委派或允许平台使用 53 端口时，可以保留现有数据模型和 UI，仅替换发布适配器。

## 2. 架构与边界

```mermaid
flowchart LR
    UI[Vue 测试域名配置] -->|JWT + Django 权限| API[Django test_host_config]
    API --> DB[(PostgreSQL/SQLite)]
    API -->|Celery 异步任务| DIAG[解析/端口/HTTP 诊断]
    AGENT[宿主机 wharttest-host-sync] -->|Bearer 部署密钥| EXPORT[已发布快照 API]
    EXPORT --> API
    AGENT --> HOST[/etc/hosts 受管区块]
    AGENT --> BACKEND[backend / recorder]
    AGENT --> PW[playwright-mcp]
    AGENT --> ACT[wharttest-actuator-*]
    AGENT -->|checksum + 节点结果| REPORT[同步上报 API]
    REPORT --> API
```

### 2.1 Django 新应用 `test_host_config`

独立应用承载数据模型、REST API、发布服务、诊断任务和测试。不将结构化映射塞入 `accounts.SystemConfig` JSON/Text 字段，以便执行唯一性、权限、版本和审计约束。

### 2.2 宿主机同步服务

- 脚本：`update_platform_version/host_sync/wharttest_host_sync.py`。
- systemd 单元：`wharttest-host-sync.service`和 `wharttest-host-sync.timer`，默认每 30 秒检查一次。
- 配置：`/etc/wharttest/host-sync.env`，权限 `0600`，包含 Backend URL 和独立的同步 Token。
- 本地状态：`/var/lib/wharttest-host-sync/state.json`，仅记录版本、checksum 和节点结果，不存储用户密码。
- 备份：每次修改宿主机 hosts 前，备份至 `/projects/ai-test-platform/backups/host-sync/<timestamp>/hosts`，不放入 `deploy_env`。

### 2.3 允许同步的节点

宿主机同步服务仅处理以下明确目标，不接受 API 传入的任意容器名或命令：

- 宿主机 `/etc/hosts`。
- `wharttest-backend`（同时覆盖 Django、Celery 和 Recorder）。
- `wharttest-playwright-mcp`。
- 名称满足 `wharttest-actuator-[A-Za-z0-9_.-]+` 的运行中执行器。

同步服务在容器中以 root 用户执行固定的 hosts 更新程序，不拼接 shell 命令。由于 Docker 的 `/etc/hosts` 是特殊挂载，容器内采用“生成临时内容 → 校验 → 覆写挂载文件内容”，不尝试 rename `/etc/hosts`。

### 2.4 本地开发容器同步

- `docker-compose.local.yml` 启动独立的 `wharttest-host-sync` 容器，通过只读发布 API和 Docker Socket同步明确白名单内的运行中容器。
- 本地同步容器设置 `HOST_SYNC_APPLY_HOST=false`，不挂载、不读取也不修改 macOS 的 `/etc/hosts`；开发人员按需手工维护本机解析。
- Backend 与本地同步容器使用仅供本地开发的共享 Token。Docker Socket 只挂载给隔离的同步容器，不挂载给 Web/Backend 容器。
- 发布后最长约 30 秒应用到 Backend、Playwright MCP 和所有 `wharttest-actuator-*` 容器；容器重建后的配置漂移由下一轮同步自动修复。

## 3. 数据模型

### 3.1 `TestHostMapping`

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | BigAutoField | 主键 |
| `system_name` | CharField(100) | 必填，用于识别被测系统 |
| `hostname` | CharField(253) | 必填、小写规范化、全局唯一 |
| `ipv4` | GenericIPAddressField | 仅 IPv4 |
| `enabled` | BooleanField | 默认 true |
| `remark` | CharField(500) | 可选 |
| `created_by/updated_by` | FK(User, SET_NULL) | 审计 |
| `created_at/updated_at` | DateTimeField | 自动时间 |

验证规则：去除末尾点并转小写；仅接受 ASCII/IDNA 后的精确主机名；拒绝空标签、通配符、URL、端口、路径和空白字符；IPv4 拒绝 loopback、link-local、multicast、unspecified 和 broadcast，允许内网地址。

### 3.2 `TestHostConfigState`

单例表，保存 `draft_revision`、`published_version_id`和 `updated_at`。每次映射写操作在事务中递增 `draft_revision`，发布时通过 `select_for_update()` 防止并发发布。

### 3.3 `TestHostConfigVersion`

| 字段 | 说明 |
|---|---|
| `version` | 单调递增整数，唯一 |
| `source_draft_revision` | 生成版本时的草稿修订号 |
| `snapshot` | 已规范化、按 hostname 排序的 JSON 快照 |
| `checksum` | 快照规范 JSON 的 SHA-256 |
| `status` | `publishing/published/failed/superseded` |
| `created_by/created_at/published_at` | 发布审计 |
| `source_version` | 回滚时指向被选中的历史版本 |
| `error_message` | 发布失败的结构化摘要 |

### 3.4 `TestHostNodeStatus`

`node_id` 唯一，存储 `node_type`、`display_name`、`applied_version`、`applied_checksum`、`status`、`message`、`details`、`last_seen_at`和 `updated_at`。同步服务每轮上报所有发现节点；超过 90 秒没有上报的节点在 API 层计算为“离线”。

### 3.5 `TestHostDiagnosis`

保存映射、触发人、状态、DNS 解析、TCP 80/443、HTTP/HTTPS HEAD 结果、错误分类、耗时与时间。任务由 Celery 执行，单阶段超时 5 秒，整体超时 20 秒。

## 4. 发布与同步流程

1. 前端带 `expected_draft_revision` 发起发布。
2. Backend 锁定 `TestHostConfigState`；如果修订号不一致，返回 HTTP 409，要求用户重新查看差异。
3. Backend 读取全部启用映射，重新验证，生成排序 JSON 和 checksum，写入不可变版本快照。
4. 版本立即成为“已发布”，但页面的节点状态会明确显示“等待同步”，不误报为全部成功。
5. 宿主机同步服务读取快照，独立验证 checksum 和每条记录。
6. 同步服务先备份宿主机 hosts，再应用宿主机受管区块，然后对容器逐一应用；一个节点失败不阻断其他节点。
7. 同步服务在每个节点内执行实际解析验证，上报版本、checksum 和结果。
8. 即使版本未变，同步服务也会检查新容器或配置漂移并修复。

回滚不修改历史版本，而是复制目标快照并创建一个新版本，随后走同样的同步流程。

## 5. API 设计

管理端 API 使用 JWT/Session 认证和 Django Model Permission：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET/POST | `/api/test-host-config/mappings/` | 列表、新增 |
| GET/PATCH/DELETE | `/api/test-host-config/mappings/{id}/` | 详情、编辑、删除 |
| GET | `/api/test-host-config/overview/` | 当前版本、草稿修订、节点概要 |
| GET | `/api/test-host-config/diff/` | 草稿与已发布快照差异 |
| POST | `/api/test-host-config/publish/` | 发布草稿 |
| GET | `/api/test-host-config/versions/` | 版本历史 |
| POST | `/api/test-host-config/versions/{id}/rollback/` | 以历史快照创建新版本 |
| GET | `/api/test-host-config/nodes/` | 节点同步状态 |
| POST | `/api/test-host-config/mappings/{id}/diagnose/` | 创建诊断任务 |
| GET | `/api/test-host-config/diagnoses/{id}/` | 查询诊断结果 |

同步端 API 使用独立 Bearer Token，不复用管理员 JWT：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/test-host-config/agent/export/` | 返回已发布版本、规范快照和 checksum；支持 `If-None-Match`/304 |
| POST | `/api/test-host-config/agent/report/` | 批量上报宿主机和容器结果 |

写 API 均不接收任意 hosts 文本、文件路径、容器名或命令。

## 6. 权限与审计

- `view_testhostmapping`：访问页面、列表、版本和节点状态。
- `add/change/delete_testhostmapping`：管理草稿映射。
- 自定义权限 `publish_testhostconfig`、`rollback_testhostconfig`、`diagnose_testhostmapping`。
- `is_superuser` 和 `is_staff` 继承平台现有的系统管理全权限行为，其他用户按模型与自定义权限授权。
- 扩展现有 `OperationLogMiddleware` 的模块映射，记录 CRUD、发布、回滚和诊断；记录变更差异，不记录同步 Token。
- Agent Token 来自 Backend 和宿主机的同名 secret 文件，Backend 使用 `secrets.compare_digest` 比较；不写入数据库、日志或 API 响应。

## 7. 诊断安全

域名诊断本质上具有 SSRF 风险，因此：

- 仅具有 `diagnose_testhostmapping` 权限的用户可触发。
- 仅诊断已保存的 hostname/IPv4，请求不接收临时 URL。
- 仅访问 80/443，HTTP 路径固定为 `/`，优先 HEAD，不跟随跨主机重定向，限制返回体读取。
- 在连接前再次解析并校验实际目标，拒绝 loopback、link-local、multicast 和 unspecified 地址。
- 超时、连接数和并发数均受限；错误响应不暴露系统文件和密钥。

## 8. 界面设计规格

### DESIGN SPECIFICATION

1. **Purpose Statement**：页面面向内网平台管理员，用来低风险地维护被测系统解析规则，并快速识别哪个执行节点没有同步。信息层级优先于装饰，发布与回滚必须清晰、可预期。
2. **Aesthetic Direction**：Industrial/utilitarian，但必须与现有 WHartTest 系统管理页保持一致。
3. **Color Palette**：主操作 `#165DFF`，主文字 `#1D2129`，次文字 `#86909C`，边框/底色 `#E5E6EB`/`#F2F3F5`，成功 `#00B42A`，警告和失败使用 Arco 语义 Token。
4. **Typography**：沿用项目已有 `Avenir, Helvetica, Arial, sans-serif` 和 Arco 排版 Token。这是对通用 UI skill 字体禁用规则的窄范围覆盖，原因是避免单一系统管理页引入额外字体并破坏现有产品一致性。
5. **Layout Strategy**：沿用左侧导航和内容容器。页内使用“上方紧凑状态带 + 下方全宽表格”；右侧抽屉承载编辑、差异、节点和版本详情。现有管理后台的导航一致性覆盖通用 skill 的“必须打破网格”要求。

### 8.1 信息架构

- 菜单：系统管理 → 测试域名配置，使用 Arco `IconLink`或同类网络图标，不使用 Emoji。
- 顶部状态带：已发布版本、未发布变更数、已同步/异常/离线节点数、最后发布人和时间。
- 工具栏：系统名称/域名搜索、启用状态筛选；右侧依次为节点状态、版本历史、新增映射、发布配置。
- 表格：系统名称、域名、IPv4、状态、更新人/时间、操作。备注在较宽屏幕显示，窄屏收入详情。
- 行操作：诊断、编辑、启用/停用；删除收入“更多”且需要二次确认。
- 发布前差异抽屉：按新增、修改、停用/删除分组，展示旧值和新值；用户确认后才发布。
- 节点抽屉：显示节点、类型、应用版本、checksum 摘要、状态、最后心跳和错误。
- 诊断抽屉：使用纵向步骤呈现格式、DNS、TCP、TLS/HTTP，单项失败不隐藏已成功项。

## 9. 前端模块

- `src/features/test-host-config/views/TestHostConfigView.vue`：页面编排和状态管理。
- `components/MappingEditorDrawer.vue`、`PublishDiffDrawer.vue`、`NodeStatusDrawer.vue`、`DiagnosisDrawer.vue`、`VersionHistoryDrawer.vue`。
- `service.ts`：集中封装 API，复用项目 `request` 客户端。
- `types.ts`：映射、版本、节点、差异和诊断类型。
- `MainLayout.vue`：基于 `test_host_config.view_testhostmapping` 显示菜单并纳入 `hasSystemMenuItems`。
- `router/index.ts`：注册 `/test-host-config`，页面层和 API 层都检查权限，不仅依赖菜单隐藏。
- i18n：新增中英文文案键，中文错误不从后端英文异常直接透传。

## 10. 部署变更

- Backend 镜像仍必须使用 `WHartTest_Django/Dockerfile.alpine` 基于 Alpine/musl 完整构建，保留 `@alibaba-group/open-code-review` 和 `ocr` CLI。
- `deploy_env` 新增安装/升级同步服务的通用脚本、systemd 单元模板和验证项；不再写死 SSE 域名 `extra_hosts`。
- 现有 `extra_hosts` 在首次上线时保留一个版本作为迁移保险；验证 UI 发布与同步服务后再删除，避免一次性切换造成 UI 测试中断。
- Compose 为 Backend 挂载只读 Agent Token secret；宿主机同步服务读取同一个私有文件。
- `05-verify.sh` 新增同步服务活性、已发布版本、宿主机/容器解析一致性检查。

## 11. 测试策略

### Backend

- 模型和 serializer：域名规范化、唯一性、IPv4 拒绝规则、权限。
- 发布：快照排序稳定、checksum 稳定、并发修订冲突返回 409、回滚生成新版本。
- Agent API：缺失/错误 Token 拒绝、ETag 304、上报 schema 校验、不泄漏密钥。
- 诊断：用 mock socket/HTTP 覆盖成功、DNS 错误、IP 不一致、端口拒绝、TLS 错误和超时。
- 审计：发布、回滚和诊断产生可搜索操作日志。

### Host sync

- 纯函数测试受管区块替换，确保标记外内容字节级保留。
- 测试 checksum 错误、非法映射、锁竞争、写入失败、容器不存在和部分失败上报。
- 在临时目录中测试备份与原子替换，不在单元测试中修改真实 `/etc/hosts`。
- 使用受控测试容器做端到端验证：应用、重启丢失、下一轮自动修复。

### Frontend

- Vitest：权限隐藏、表单验证、草稿标记、发布 409 提示、节点部分失败和诊断轮询。
- Playwright：新增映射 → 查看差异 → 发布 → 查看节点状态 → 回滚的主路径。

## 12. 可观测性与失败处理

- Backend 日志携带 `version`、`draft_revision`、`diagnosis_id`，不输出 Token。
- 同步服务使用 journald，日志携带节点名和错误阶段；错误连续发生不会覆盖上一个成功版本的本地状态。
- 页面将“发布成功但部分节点待同步”与“发布失败”区分呈现。
- 同步服务未安装或 Token 错误时，页面显示“同步服务离线”和最后成功时间，不会将平台整体标记为已收敛。

## 13. 兼容与迁移

- 新增 Django 数据库迁移，不修改现有业务表。
- 首次升级可将 Compose 中现有 SSE `extra_hosts` 导入为草稿，由管理员检查后发布；导入脚本幂等。
- 无已发布版本时，Agent export API 返回 204，同步服务不修改任何 hosts。
- 同步服务只管理标记区块，不会清理存量非受管 hosts 条目；迁移完成后可由运维人员删除重复的旧条目。
