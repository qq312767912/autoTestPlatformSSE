# 回滚手册（质量飞轮 · 一期）

> 依据：设计 §13／§14、需求 R1（版本锁不漂移）、R13（评测与晋级）。
> 一期**没有任何数据库破坏性变更**，所以回滚分成三个**互相独立**的层次。
> 关键判断：**大多数事故只需要第 1 层**，不要一上来就回滚镜像。

## 1. 三层回滚，从小到大

| 层 | 动作 | 影响面 | 耗时 | 什么时候用 |
| --- | --- | --- | --- | --- |
| L1 关开关 | 把项目灰度开关置 `enabled=false` | 只停飞轮入口；业务生成完全不受影响 | 秒级 | 飞轮控制面出问题，但业务要照常跑 |
| L2 回滚 Skill 发布 | 把候选版本回滚到基线版本 | 只影响"某个能力用哪个包" | 分钟级 | 新激活的 Skill 版本导致质量退化 |
| L3 回滚镜像 | 前后端镜像退回上一版 | 整个平台 | 十分钟级 | 代码缺陷（接口/权限/迁移） |

**升级顺序**：L1 → 观察 → L2 → 观察 → L3。
理由：L1/L2 都是可逆的配置动作，L3 需要停机窗口。先做代价小的，
往往能立刻止血并把"是否需要 L3"的判断依据拿到手。

---

## 2. L1：关闭灰度开关（首选止血手段）

```bash
curl -X POST "$BASE/api/knowledge-evolution/flywheel-settings/set/" \
  -H "Authorization: Bearer <负责人 token>" -H 'Content-Type: application/json' \
  -d '{"project": <项目 id>, "enabled": false, "rollout_note": "事故止回滚：<原因> <时间>"}'
```

### 关闭后**应当**发生什么

- 飞轮**入口**一律 403：`flywheel-runs` 的创建与 `open`、`operations/start-workflow`、
  `workflow-stage-submit`、历史导入与回放。
- **不应当**发生变化的：业务页面的方案分析、Agent 调用、产出发布与检索；
  已存在链路的门禁登记、反馈提交、归因运行。

### 关闭后**不应当**被误判为故障的现象

- 未灰度项目点飞轮入口拿到 403，且提示含"灰度"字样 —— 这是**设计行为**。
- 执行人员看不到开关入口（403）—— 也是设计行为，开关是负责人的职责。

### 回滚后必须核对

```text
GET /api/knowledge-evolution/flywheel-settings/state/?project=<id>   # enabled=false
```

若关闭后业务侧仍出现失败 → 说明执行面被控制面污染，**这是代码缺陷**，
直接进 L3，不要停在 L1 反复观察。

---

## 3. L2：回滚 Skill 发布

适用于"某个 Skill 版本激活后质量退化"，且业务生成本身没问题。

1. 确认当前生效版本与基线版本：

   ```text
   GET /api/knowledge-evolution/optimization-proposals/<proposal id>/skill-content-running-flows/
   ```

   返回 `active_version_id`（当前解析到的版本）与 `locked_flows`（各条流程实际锁定的版本与包哈希）。
   ⚠️ 先看清 `locked_flow_count`：**运行中的流程钉在它们各自的锁上，回滚不会、也不该改写它们**。

2. 执行回滚（负责人）：

   ```text
   POST /api/knowledge-evolution/optimization-proposals/<proposal id>/skill-content-rollback/
   ```

   回滚走的是 `CapabilityReleaseService`，状态从 `active` → `rolled_back`；
   若回滚本身失败会转 `quarantined`（隔离），此时**必须人工介入**——
   隔离态意味着"这条发布既没生效也没干净退出"。

3. 核对：

   - `Skill.active_version` 已指回基线版本；
   - 之前 `locked_flows` 里的流程，其 `skill_version_id` **一个都没变**；
   - 新发起的流程使用基线版本。

4. 观察窗口：回滚后继续观察 **≥ 3 条**链路再判断是否恢复。

⚠️ 若候选是在**观察窗口内**因指标超阈值自动回滚的，记录会显示 `rolled_back` 且带有
超阈值的指标快照——这不是"回滚失败"，不要重复执行。

---

## 4. L3：回滚镜像

### 4.1 前提：本期迁移是可回退的

`0042`（`optimizationproposal.proposal_type` 增加 `skill_content` 选项）与
`0043`（新建 `FlywheelRegistrationFailure`）都是**纯增量**：

- 不删列、不改列类型、不回填历史数据；
- 因此**旧版后端代码可以在新库上正常运行**（旧代码不认识新选项与新表，但不会报错）；
- 反向迁移（`manage.py migrate knowledge_evolution 0041`）会删掉新表与新选项，
  **只在确认没有任何补偿记录需要保留时**才执行。

上线前的硬证据（必须留档）：

```bash
python manage.py makemigrations --check --dry-run    # 期望：No changes detected
```

### 4.2 回滚步骤

1. 关开关（L1）——避免回滚过程中有新的链路发起。
2. 确认补偿队列已清空或已导出：

   ```text
   GET .../operations/registration-failures/?project=<id>&detail=1
   ```

   `open > 0` 时先导出原文（这些记录在新旧版本之间**语义不变**，但回滚前留下证据更稳妥）。
3. 用上一版镜像 tag 重新 `docker compose up -d`（或按内网升级包的既有回滚脚本执行）。
4. **不要**自动执行反向迁移：表还在不影响旧代码运行；反过来删了表则补偿记录永久丢失。
5. 回滚后跑：

   ```bash
   python manage.py check
   python manage.py audit_flywheel_isolation --fail-on-error
   ```

6. 恢复开关（逐项目，先 e 投票项目）。

### 4.3 回滚后禁止做的事

- 不要用"手工改数据库"把某条产出/门禁/补偿记录改成看起来正确的样子：
  那会让审计失去意义（`audit_flywheel_isolation` 与就绪自检都会因此变绿，而问题还在）。
- 不要在回滚过程中同时改开关与改包版本——两件事一起做之后，
  "到底是哪一步起的作用"就再也分不清了。

---

## 5. 回滚完成判据（全绿才算完成）

```text
GET  .../operations/launch-readiness/?project=<id>     # ready=true，无死信
GET  .../operations/registration-failures/?project=<id>  # open=0
GET  .../flywheel-settings/state/?project=<id>          # 状态与预期一致
python manage.py audit_flywheel_isolation --project <id> --fail-on-error   # 退出码 0
```

外加一条**人工**确认：未灰度项目的业务页面能正常跑完一次方案分析。

## 6. 回滚记录模板（交付留档）

```text
时间：
触发条件：            # 命中《上线检查表》第 5 节的哪一条
影响面：              # 项目 / 阶段 / 产出 id / 反馈 id
执行层级：            # L1 / L2 / L3（若跨层，写清顺序）
执行人：
回滚前快照：          # launch-readiness、registration-failures 原文
回滚后核对：          # 第 5 节四条命令的输出
根因：
后续动作：            # 修复项 / 是否重新灰度 / 复查时间
```
