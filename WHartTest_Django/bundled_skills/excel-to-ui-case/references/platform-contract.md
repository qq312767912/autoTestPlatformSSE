# WHartTest UI 自动化入库契约

本契约对应仓库的 `ui_automation/models.py`、`serializers.py`、`views.py`、`urls.py` 和 `WHartTest_Actuator/executor.py`。目标平台可能运行其他版本：操作前核对实际接口，不自动改后端。

## 访问方式

平台提供的 UI 管理工具可用时，复用 `ui-automation` Skill 的管理能力。其 CLI 为 `ui_automation_tools.py`，实际路径从运行时已部署 Skill 目录解析，不能假设当前目录或固定绝对路径。

需要 `case_flow`、`auth_state` 等 CLI 未暴露字段时，使用已授权的 HTTP 客户端访问 REST API。平台地址来自 `WHARTTEST_BACKEND_URL`，API Key 来自 `WHARTTEST_API_KEY`，请求头 `X-API-Key`，JSON 请求体。也可使用平台提供的已认证会话。凭据缺失时不使用工具脚本内置默认密钥，不输出密钥。请求体 SHOULD 由 JSON 序列化器生成，避免 shell 插值。

前缀：`/api/ui-automation/`。列表可能返回数组、分页 `{results,next}` 或 `{data:...}` 包装；逐层解析并跟进分页。`next` 仅在同源且属于本平台接口时跟进，不将凭据发往其他地址。

## 原生资源字段

| 路径 | 创建时核心字段 | 关联 |
|---|---|---|
| `modules/` | `project, name, parent` | UI 模块最多 5 层，parent 可空 |
| `pages/` | `project, module, name, url, description` | page 名最长 64；url 未知可空 |
| `elements/` | `page, name, locator_type, locator_value` | name 最长 64；可选备用定位、iframe 信息 |
| `page-steps/` | `project, module, page, name, description` | name 最长 64；可选 `auth_state` 为登录态 ID |
| `testcases/` | `project, module, name, description, level` | name 最长 255；level=P0/P1/P2/P3 |
| `case-steps/` | `test_case, page_step, case_sort` | 用例引用页面步骤 |

模块 ID MUST 属于当前项目，page/element/page_step 引用 MUST 来自目标项目。NEVER 仅因拿到一个数字 ID 就复用。

UI 用例支持 `case_flow` 文本、`parametrize`、前后置数据等字段；它们不等于自然语言 AI 执行入口。只填写 description/case_flow 可保存草稿，执行器仍需要具体步骤。不要向 UI 用例提交普通业务用例的 `precondition`、`steps`、`execution_mode` 等字段。

无专门 draft/readiness 字段：通过名称前缀和 description 标注，平台不会因此自动禁止运行。交付时 MUST 明说草稿不可直接执行。`status` 是最近执行状态，不能改为成功来表示转换完成。

## 草稿入库

POST `testcases/`：以下是结构示例，数字仅为示例，实际运行必须使用查询返回的 ID。

```json
{
  "project": 1,
  "module": 5,
  "name": "[待采集] T1216832｜游客｜问询函查询登录引导",
  "level": "P1",
  "description": "[excel-to-ui-case]\nsource_key=星企航企业通用版|Sheet1|T1216832|游客|default\n来源行：14；原始模块路径：见来源\n角色：游客；转换状态：待采集\n前置：测试环境首页，无登录态\n步骤1：进入培训资源-上市之路。预期：显示上市流程介绍。\n步骤2：进入上市流程详情，点击发行审核/注册中的问询函查询。预期：出现登录引导，具体形式待确认。\n缺口：环境地址、真实定位、登录引导的验收基准。\n执行情况：未执行。"
}
```

来源行必须使用实际值；上例不是对附件行号的断言。`source_key` 是 description 内约定的标记，不是后端字段。建议构成为「稳定来源名|sheet|原 ID|角色|场景」；没有原 ID 时使用稳定行标识。原始文件内容摘要另记，用于发现变更，不把内容摘要直接当作重跑去重键。名称截断时保留原 ID/角色，完整原名写 description。

查询 `testcases/?project=<id>&module=<id>`，遍历分页后 GET 候选详情匹配来源标记。NEVER 只检查列表第一页或仅凭截断描述去重。平台当前没有该标记的唯一约束：同一来源导入串行执行；遇到并发冲突停下核对，不承诺服务端幂等。

写后 GET `testcases/<id>/` 验证名称、模块及 description；拿到非空 ID 仍不代表步骤已构建。

## 构建与关联

按依赖顺序创建/复用：模块 → 页面 → 元素 → 页面步骤 → 步骤详情 → 用例 → 用例步骤。

POST `page-steps-detailed/batch_update/`：

```json
{
  "page_step": 20,
  "steps": [
    {"step_type": 0, "element": 101, "ope_key": "click", "ope_value": {}, "description": "点击问询函查询"},
    {"step_type": 1, "element": 102, "ope_key": "assert_visible", "ope_value": {}, "description": "登录引导可见"}
  ]
}
```

POST `case-steps/batch_update/`：

```json
{"test_case": 50, "steps": [{"page_step": 20, "case_sort": 0}]}
```

两个 batch_update 都具有替换集合语义：未提交的已有步骤会被删除；不传原 `id` 会重建步骤。更新已有集合前 MUST GET 完整详情，保留已有步骤 ID、顺序、case_data 等参数。用户仅授权新增时 SHOULD 建立来源专属步骤，不能调用批量替换覆盖共享资产。

常用 step_type：0=元素操作，1=断言。字段 `ope_key` 决定实际执行方法，`ope_value` 传参数。已核对常用形状：

| ope_key | ope_value | 备注 |
|---|---|---|
| `click` | `{}` | 需要元素 |
| `fill` | `{"value":"测试文本"}` | 需要元素 |
| `press` | `{"key":"Enter"}` | 需要元素 |
| `assert_visible` | `{}` | 需要元素 |
| `assert_text` | `{"expected":"预期全文"}` | 需要元素；to_have_text 全文匹配 |
| `assert_contain_text` | `{"expected":"预期片段"}` | 需要元素；to_contain_text 包含匹配 |
| `goto` | `{"url":"用户提供的真实地址"}` | 页面级操作，无需元素；仍需所属页面步骤 |
| `switch_tab` | `{"value":"已确认的URL片段或标题"}` | 新窗口切换；不用不稳定的固定页签索引 |

以上不穷举所有动作。复杂断言先检查实际执行器，不凭方法名推测能力；没有支持就保留缺口，不自动生成 Python 代码来绕过。

GET `testcases/<id>/execute-data/` 回读完整执行结构。检查原始检查点对应的操作/断言已存在、引用正确；没有遗漏才标记已构建。平台实际执行通过才标记已验证，并引用真实执行记录。

## 登录态与错误处理

GET `auth-states/?project=<id>` 查询授权项目下登录态，具体字段以返回为准；通常只需 ID/名称，不读取或传播快照内容。按角色确认归属，可 PATCH `page-steps/<id>/` 的 `auth_state`。若执行入口支持运行时选择则沿用该入口，不捏造 `testcase.auth_state_id` 字段。游客不得复用已登录上下文。

HTTP 非 2xx、`status:error`、缺少预期资源 ID 均不能记成功。写入超时先回查，部分成功记录已创建的 ID 和失败阶段；不直接重跑整个批次，也不清理可能已被其他用例引用的资产。未完成用例保留待构建标记，修复后从失败阶段续传。NEVER 直接写数据库。
