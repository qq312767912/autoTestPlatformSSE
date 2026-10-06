# 上线检查表（上证 e 投票质量飞轮 · 一期）

> 适用对象：本次交付的"质量飞轮—Agent 执行—Skill 自进化联动"一期（T01–T14）。
> 灰度范围：**先只开上证 e 投票项目的方案生成阶段（`test_plan_generation`）**。
> 自动化部分：`GET /api/knowledge-evolution/operations/launch-readiness/?project=<id>`
> （或 `LaunchReadinessService.check(project)`）——**它只能替人判可判定的那几条**，
> 下面的"人工项"必须有人签字确认。

## 0. 一句话判据

飞轮是**可选的控制面**。判断上线是否成功，不是"飞轮功能都能用"，而是：

1. 未灰度的项目，业务页面照常能跑方案分析、Agent 调用照常能发起；
2. 已灰度的项目，产出能登记、能反馈、能归因、能派生候选；
3. 任何一步失败**都看得见**（有记录、有计数、有告警），而不是只躺在日志里。

## 1. 自动化项（必须全绿）

| 检查码 | 含义 | 不通过怎么办 |
| --- | --- | --- |
| `migrations_applied` | 所有迁移已应用（含 `0041`–`0043`） | 先 `manage.py migrate`，再重跑自检 |
| `no_cross_project_reference` | 无跨项目引用 | 跑 `manage.py audit_flywheel_isolation --project <id> --fail-on-error`，按列出的 id 逐条修 |
| `no_dead_letter` | 候选队列与登记失败队列均无死信 | 见《异常补偿手册》第 3 节 |
| `runbooks_present` | 三份手册齐备（本环境若无手册目录则为 `skipped`，转人工项） | 补齐手册目录，或按人工项确认 |

`linkage_flag_configured` / `linkage_switch_state` 是**软条件**，不影响 `ready`：
"该不该开开关"是自检通过之后才决定的事，把它算成硬条件会绕成死循环。

## 2. 上线前（发布包与配置）

- [ ] 后端镜像版本与 `deploy_env/IMAGE_MANIFEST.txt` 一致；镜像 tag 与断言脚本一致。
- [ ] `python manage.py makemigrations --check --dry-run` 输出 **No changes detected**（证明模型与迁移没有漂移）。
- [ ] `python manage.py migrate` 在**预生产库的副本**上先跑一遍，确认 `0042`（`proposal_type` 增加 `skill_content`）与 `0043`（新建 `FlywheelRegistrationFailure`）都是纯增量：**不删列、不改类型、不回填数据**。
- [ ] `python manage.py audit_flywheel_isolation --fail-on-error` 退出码为 0。
- [ ] 前端构建产物 hash 与发布记录一致；入口按钮文案为「**跳转到Agent执行**」（四个阶段一致，由后端返回的 `launch_url` 决定去向）。
- [ ] `GET .../operations/launch-readiness/?project=<e投票项目 id>` 返回 `ready: true`。

## 3. 人工项（自动化判不了，必须有人确认）

- [ ] **开关默认关闭**已在目标环境生效：任意未灰度项目的
      `GET /api/knowledge-evolution/flywheel-settings/state/?project=<id>` 返回 `enabled: false`。
      ⚠️ 不要用"列表里有没有这条记录"判断——`configured: false` 与 `enabled: false` 是两件事。
- [ ] 已为**且仅为**上证 e 投票项目建灰度记录，`rollout_note` 写清灰度范围与负责人。
- [ ] 用**执行人员**（非负责人）账号验证一次：未灰度项目的飞轮入口应为灰/隐藏；
      即使直接调接口也拿到 403，且提示里**不含**"灰度"字样（不泄露其他项目的灰度状态）。
- [ ] 用负责人账号确认：开关的开启/关闭入口对自己可见，对执行人员返回 403。
- [ ] 抽查一条**在途**链路：发起后关闭开关，该链路的产出仍能登记、反馈仍能提交
      （开关控制"要不要开始用"，不该把跑到一半的用户踢出去）。
- [ ] 抽查一条 `case_review` 等**单次能力**的业务产出：飞轮关闭时照常可用，
      不被要求先开开关。
- [ ] 抽查一次人工补充文件上传：文件绑定到项目/流程/阶段/原始产出，
      且**未**自动成为金标、**未**触发任何 Skill 发布。
- [ ] 确认观测面无敏感信息：轨迹里没有隐藏思维链、账号凭据、完整敏感参数；
      知识原句按原文权限展示。
- [ ] 业务侧与运维侧都已知道补偿队列的入口（见《异常补偿手册》第 2 节），
      并指定了死信的处置人。

## 4. 灰度开启与观察

1. 负责人执行 `POST /api/knowledge-evolution/flywheel-settings/set/`
   `{"project": <e投票项目>, "enabled": true, "rollout_note": "一期灰度：方案阶段，负责人 <姓名>"}`。
2. 让方案阶段真实跑 **≥ 1 条**链路：发起 → 跳转到Agent执行 → 正式产出 → 人工确认。
3. 确认以下事实（用 `GET .../operations/metrics/`、`cockpit/`、`candidate-alerts/`、
   `registration-failures/` 逐项核对）：
   - 产出带上了锁定的 `skill_version` 与 `package_sha256`；
   - 门禁记录出现且状态为 `pending`（待评测）；
   - 候选队列里有对应事件（`source_type` 指向该产出）。
4. 观察 **≥ 1 个工作日**、**≥ 3 条**链路后再决定是否扩大灰度范围。
5. 扩范围时逐项目加记录，**不要**改成"默认开启"——那等于放弃灰度。

## 5. 上线期间的回退触发条件

出现以下任一情况，立即按《回滚手册》处理，不要"再观察一下"：

- 未灰度项目的业务生成出现新增失败（哪怕只有一例）——这是**红线**，
  说明执行面被控制面污染了；
- 补偿队列出现死信且**24 小时内无法归零**；
- 产出出现"有产出但无门禁 / 有门禁但无产出"的错配；
- 出现跨项目引用（`audit_flywheel_isolation` 报错）。

## 6. 验收留档

上线完成后把下面几项贴进交付记录（缺一项就没法证明当时是什么状态）：

```
git rev-parse HEAD                     # 交付 commit
manage.py makemigrations --check --dry-run   # 应为 No changes detected
manage.py audit_flywheel_isolation --fail-on-error   # 退出码 0
GET launch-readiness?project=<id>      # ready 与 checks 原文
GET flywheel-settings/state/?project=<id>   # 灰度状态与 rollout_note
GET registration-failures/?project=<id>     # 应为 open=0, alert=false
```
