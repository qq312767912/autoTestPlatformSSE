# 异常补偿手册（质量飞轮 · 一期）

> 依据：设计 §13「失败、补偿与安全」、需求 R15。
> 核心原则一句话：**业务生成成功与飞轮登记成功分别呈现；飞轮侧的失败不得让业务产物丢失，
> 也不得只记日志。**

## 1. 三类失败，三种处置，别混

| 失败类型 | 表现 | 记录在哪 | 谁处置 |
| --- | --- | --- | --- |
| Agent 中途失败 / SSE 断线 | 这一轮没有正式产出 | `StageExecutionAttempt`（`status=failed/cancelled/timed_out`）+ 已写入的 `ExecutionSpan` | 执行人员重试 |
| 产出发布失败 | 业务产物存在，但飞轮门禁**没建出来** | `FlywheelRegistrationFailure`（`last_error="门禁写入失败"` 类） | 测试负责人重试 |
| 飞轮登记失败 | 产出与门禁都缺，或候选队列没进 | `FlywheelRegistrationFailure` / `AssetCandidateEvent(failed/dead_letter)` | 测试负责人 + 运维 |

**判断顺序**：先看 `GenerationOutput` 在不在（业务产物），再看门禁在不在，最后看补偿队列。
顺序反了会得出"飞轮坏了"的结论，而实际上问题可能只是"这一轮 Agent 没跑出东西"。

## 2. 补偿队列在哪看

```text
队列概览（计数 + 告警）：GET  /api/knowledge-evolution/operations/registration-failures/?project=<id>
逐条明细：              GET  /api/knowledge-evolution/operations/registration-failures/?project=<id>&detail=1
批量重试：              POST /api/knowledge-evolution/operations/registration-failures-retry/  {"project": <id>}
候选队列（并列的一套）： GET  /api/knowledge-evolution/operations/candidate-alerts/?project=<id>
                        POST /api/knowledge-evolution/operations/...（asset-candidates/retry/）
```

两个队列**刻意不合并**：候选事件的失败意味着"金标没沉淀"，登记失败意味着"这一版产出没进门禁"。
处置人、处置动作、严重度都不同，合成一个数字只会让两边都看不清。

### 状态语义（真值：`workflow_models.REGISTRATION_*`）

| 状态 | 含义 | 是否告警 |
| --- | --- | --- |
| `pending` | 已入队、尚未尝试 | 否 |
| `failed` | 尝试过、失败，还会被重试消化 | **否** |
| `dead_letter` | 尝试次数用尽（默认 3 次），必须人工介入 | **是** |
| `resolved` | 补登成功或确认无需登记 | 否 |

⚠️ `alert` **只看 `dead_letter`**。把 `failed` 也算告警，会让控制台在正常重试窗口里持续闪红，
真正该看的人很快就不看了。

⚠️ **幂等**：同一产出 + 同一流程 + 同一阶段只有一条记录。重复失败只累加 `attempts`，
不新增记录——否则一次网络抖动重试三次会让控制台显示"3 个产出登记失败"，
而实际只有一个。告警数字一旦失真就再没人相信它。
`resolved` 之后同一产出再失败会**复用同一条记录**并打回 `failed`，不新建第二条时间线。

## 3. 处置流程

### 3.1 单条 `failed`（还能自愈）

1. 看 `last_error` 与 `history`（逐次尝试的错误摘要），判断是**瞬时**还是**确定性**问题：
   - 瞬时（连接被重置、锁等待超时、DB 抖动）→ 直接重试；
   - 确定性（产出协议里没有 `workflow_id`、阶段不在 `ALL_WORKFLOW_STAGES`、
     包被隔离导致阶段锁解析失败）→ 先修因，重试不会好。
2. 重试：`POST .../registration-failures-retry/ {"project": <id>}`（需测试负责人）。
3. 重试成功后该记录自动转 `resolved`，`open` 计数回落。

### 3.2 进入 `dead_letter`（必须人工）

1. 不要先重试。先取明细确认 `last_error` 的**根因分类**：

   | 根因 | 特征 | 正确动作 |
   | --- | --- | --- |
   | 产出协议缺 `workflow_id` | `reason=not_workflow_output` 不会进队列；进队列说明协议里**有** workflow_id 但阶段非法 | 核对 `output.metadata["protocol"]["stage"]` 是否在 `ALL_WORKFLOW_STAGES` |
   | 阶段 Skill 版本解析失败 | 日志有「阶段 X 的 Skill 版本未能锁定」 | 检查该阶段是否有可运行版本（被停用/隔离/驳回都会导致）；必要时由负责人在 Skill Hub 处理 |
   | DB / 连接类 | 错误串含连接、超时 | 修基础设施后重试 |
   | 跨项目引用 | `_resolve_source` 抛「不属于当前项目」 | **不要**用改 id 的方式绕过；这属于隔离事故，按第 4 节处理 |

2. 确认根因已消除后，逐条重试；重试仍失败 → 记录会继续累加 `attempts`，
   保持 `dead_letter` 并留痕，**不要**手工把 `status` 改成 `resolved` 掩盖问题。
3. 若确认这一版产出**本来就不该登记**（例如误用受控入口发布了旁路产出）：
   由负责人确认后置 `resolved`，并在 `history` 里补一条说明（谁、为什么）。

### 3.3 Agent 中途失败 / SSE 断线

1. `StageExecutionAttempt` 会停在 `failed` / `cancelled` / `timed_out`，**已有 Span 全部保留**，
   `output` 为空——这是正确的，失败尝试不得冒充正式产出。
2. 处置是**重试**，不是"改状态"：重试会新建一条 attempt 并通过 `retry_of` 指回原记录，
   这样"重试过几次、每次为什么失败"不会丢。
   `POST /api/knowledge-evolution/stage-attempts/<id>/retry/`（需测试负责人）。
3. 重试前建议先看 `GET .../stage-attempts/<id>/trace/`：
   `events` 说明走到哪一步断的，`spans` 给出工具调用与耗时。
   ⚠️ SSE 断线**不影响**已经产生的产出：断的是前端的那条流，不是这一轮的执行记录。

### 3.4 业务生成成功但飞轮登记失败（验收场景）

期望状态：

- `GenerationOutput` 存在且内容可读（业务文件仍可访问）；
- `POST .../flywheel-runs/open/` 等入口**不受影响**；
- `registration-failures` 里能看到该产出的补偿记录（`open > 0`）。

验证方式见 `tests_t14_launch.py::RegistrationCompensationTests` 与
`FailureCompensationTests`。若发现业务产物不在或内容被清空——这是**缺陷**，不是配置问题。

## 4. 隔离事故（跨项目引用）

- 判定：`python manage.py audit_flywheel_isolation --project <id> --fail-on-error` 退出码非 0，
  或 `launch-readiness` 的 `no_cross_project_reference` 为 false。
- 处置顺序：**先止血**（按《回滚手册》第 3 节关闭开关，必要时回滚发布），
  再按命令输出的 id 逐条核对。**不要**用改外键的方式消除报错——
  那会让"这条记录属于哪个项目"永久不可信。
- 记录：把 id、发现时间、根因、处置动作写进交付记录。

## 5. 什么情况下不要用补偿队列

- **正常的旁路产出**（协议里本来就没有 `workflow_id`）不算失败：
  `register` 会返回 `reason=not_workflow_output`，**不落记录**。
  若把正常情况也记成失败，死信队列会被噪声淹没，真正的问题反而看不见。
- 已经 `resolved` 且原因清楚的记录，不要反复重试："重试到它变成绿"不是处置。
