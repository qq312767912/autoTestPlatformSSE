# TestExecution：执行、留痕与数据校验（Step 4）

## 功能定位

基于 Step3 的 `test_cases.xlsx`，按用例性质**分流执行**，采集证据（截图 / SQL 结果 / Kafka 消息 / 导出文件），回填 M 列执行结果与 N 列执行人，并输出日志与配置。

> ⚠️ 本平台**不存在"全部自动化"的选项**。目标不是把 90% 用例跑成脚本，而是**让每条用例都有可复核的证据**。强行自动化小程序真机、后台审核、现场服务工具只会产生假通过。

## 前置依赖

- 已完成 Step3，`test_cases.xlsx` 存在且字段完整。
- 已确认环境可达、账号可用、所需测试数据已准备（会议、股权文件、开关状态）。
- 已确认输出目录可写。

## 三类执行分流（先分类，再执行）

对每条用例按 E 值/步骤特征判定执行方式，写入 `test_config.json` 的 `cases[].mode`：

| 模式 | 适用用例 | 手段 | 证据要求 |
| --- | --- | --- | --- |
| **A · UI 自动化** | 新网站 PC/H5 页面展示、日期控件、跳转地址、机构端入口、后台可点击流程 | Selenium / Playwright（`templates/ui_case_template.py`） | 步骤截图（按用例编号命名）+ 断言结果 |
| **B · 数据校验脚本** | 表结构、唯一性约束、Kafka 消息字段、下游入库、文件生成规则 | SQL / Kafka 消费 / 文件校验（`templates/data_check_template.py`） | SQL 语句 + 结果集（导出为 csv/json）+ 消息原文 |
| **C · 人工执行留痕** | 小程序真机、业务系统后台审核、统计系统审核、现场服务工具、真机兼容性、走查类 | 人工操作 + 截图/录屏 | 截图或录屏 + 文字说明（执行人、时间、环境） |

> **判定原则**：只要用例的预期结果**依赖截图无法表达的状态**（多系统联动、审核动作、真机渲染），一律归 C。宁可人工，不要假自动。

## 配置项（`test_config.json`）

| 配置项 | 可选值 / 格式 | 默认 | 说明 |
| --- | --- | --- | --- |
| execution_mode | `serial` / `parallel` | `serial` | 串行/并行（并行仅限 A 类且无共享数据依赖） |
| concurrency | 1–10 | 1 | 并行线程数 |
| retries | 整数 | 1 | 失败重试次数（UI 抖动类） |
| retry_interval | 秒 | 2 | 重试间隔 |
| case_timeout | 秒 | 300 | 单用例超时 |
| fail_fast | true / false | false | 失败立即终止 |
| env | `test` / `uat` / `prod` | `test` | 影响 base_url；**prod 禁止执行破坏性操作** |
| screenshot | `always` / `on_failure` / `never` | `always` | 截图策略 |
| browser | `chrome` / `firefox` | `chrome` | UI 类浏览器 |
| headless | true / false | true | 无头模式（跳转/兼容性验证建议 false） |
| login_state | cookies 文件路径 | 无 | 登录态注入，避免重复登录 |
| db | host/port/user/password/database | 无 | 数据校验脚本连接串（**脱敏后写入配置**） |
| kafka | bootstrap/topic/group | 无 | Kafka 校验用 |

## A 类：UI 自动化

1. 生成脚本 `script/TC-{用例编号}.py`，基于 `templates/ui_case_template.py`；
2. 优先复用 `websiteFeature/vote.sseinfo.com.md` 中的元素定位与等待策略；
3. 断言映射：把 K 列的预期逐条转为断言（`assert_in_url` / `assert_text_present` / `assert_element_state` / `assert_filename_pattern`）；
4. **跳转地址类**用例用完整 URL 精确比对（含 query 参数），不要只判断"跳转成功"；
5. 截图命名 `screenshots/{用例编号}_step{N}.png`，便于 Step5 关联。

执行方式（**必须后台执行**）：

```
# 后台执行，给足超时，避免进程树被外层超时杀掉
python3 script/TC-001.py   →   background=true, timeout=300
```

## B 类：数据校验脚本

基于 `templates/data_check_template.py`，按用例填写校验函数。常用校验类型：

| 校验类型 | 实现要点 |
| --- | --- |
| 表结构校验 | `SELECT column_name FROM information_schema.columns WHERE table_name='vote_basic_info'`，断言含 `timestamp` |
| 唯一性约束校验 | 按规则库 4.3 三种组合分别 insert，断言"失败/成功 + 表内条数" |
| 消息字段校验 | 消费 `INFONET_VOTE.DATA`，断言消息体含 `timestamp` 且与库内值一致 |
| 历史数据失效校验 | 会议变更后断言历史记录 `is_valid` 置位、新记录 `timestamp` 与最新消息一致 |
| 生成物校验 | 按规则库 5.3 的命名规则用正则断言文件名，并校验关键字段取自会议信息 |

产物写入 `evidence/`：`{用例编号}_sql.txt`、`{用例编号}_result.csv`、`{用例编号}_kafka.txt`。

## C 类：人工执行留痕

- 截图或录屏命名 `screenshots/{用例编号}_manual.png`（可多张，加序号）；
- 在 `test_results.json` 中记录 `executor`、`executed_at`、`env`、`evidence`;
- 人工执行的**判定依据必须可复核**：如统计系统审核通过 → 附审核后页面截图 + 申请单号。

## 结果判定标准

| 结果 | 定义 | 处理 |
| --- | --- | --- |
| **通过** | 所有断言成功 / 人工核对与预期一致 | M 列 = `通过` |
| **失败** | 断言失败或存在与预期不符的观测 | M 列 = `失败`，记录失败步骤 + 证据路径 |
| **阻塞** | 前置条件不满足（数据未就绪、环境不可用、依赖外部人员配合、开关无法配置） | M 列 = `阻塞`，记录原因 |

> ⚠️ **阻塞 ≠ 失败**。数据/环境/配合类前置不满足时一律判阻塞，并记录阻塞原因与责任人 —— 这直接决定报告的通过率口径是否可信。

> **无法自动验证的步骤**（第三方页面内部内容、监管文案、真机渲染细节）：标记为"需人工二次确认"，在报告中单独列出，**不得直接计为通过**。

## 回填 `test_cases.xlsx`

1. 读取原文件，按 F 列的编号定位行（**不要按行号定位，行可能被增删**）；
2. 更新 M 列（执行结果）与 N 列（执行人）；
3. 若需记录失败详情，**在表格最后新增空白列**（如"实际结果详情"），不得覆盖既有 A~P 列；
4. 保存。**不要改变表头、列顺序与既有行顺序**。

## 输出产物

```
{OUTPUT_ROOT}/
├── script/           # A 类脚本 + B 类校验脚本
├── screenshots/      # 执行留痕（{用例编号}_*.png）
├── evidence/         # SQL 结果 / Kafka 消息 / 导出文件
├── logs/
│   └── execution.log
├── test_cases.xlsx   # 已回填 M/N 列
├── test_config.json  # 本次执行配置（脱敏）
└── test_results.json # 结构化执行结果（Step5 输入）
```

## 注意事项

- **环境隔离**：`prod` 环境禁止执行写操作/数据构造；破坏性操作（删记录、发起撤销、重传股权文件）必须在测试环境。
- **数据准备优先**：执行前先跑一遍"数据就绪检查"（会议是否存在、股权文件是否上传、议案是否确认、开关是否正确），避免大量用例因同一前置缺失批量阻塞。
- **登录态管理**：优先 cookies 注入；使用账号密码时凭据**不得明文写入配置或日志**。
- **并行风险**：股权处理、会议数据类用例存在共享状态，**默认串行**；确需并行时使用独立测试账号池与独立会议数据。
- **执行过程实时输出进度**，便于人工介入 C 类环节。
