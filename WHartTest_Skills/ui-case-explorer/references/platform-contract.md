# 平台执行契约

目标后端不同版本时以实际路由、序列化字段及执行器为准。本表是当前仓库的已核对格式。

## REST 资源

前缀 `/api/ui-automation/`。使用平台已授权连接，或环境注入的 WHARTTEST_BACKEND_URL、WHARTTEST_API_KEY，密钥请求头为 X-API-Key。不得使用脚本内置默认密钥。列表跟进同源分页，兼容 data、results 包装。

- GET `testcases/<id>/` 读取原 UI 用例。
- GET `testcases/<id>/execute-data/` 读取执行器所需完整结构。
- `modules/` 保存 project、name、parent，最多五层。
- `pages/` 保存 project、module、name、url。
- `elements/` 保存 page、name、locator_type、locator_value，可选备用定位和 iframe 信息。
- `page-steps/` 保存 project、module、page、name，可通过实际序列化器支持的 auth_state 或 auth_state_id 绑定身份。UI 用例没有独立 auth_state_id 字段。
- POST `page-steps-detailed/batch_update/` 保存 page_step 和 steps 数组。
- POST `case-steps/batch_update/` 保存 test_case 和 steps 数组，条目引用 page_step。
- GET `execution-records/` 查询实际执行结果，以详情中时间、test_case 和步骤结果核对本次任务。

两个 batch_update 都是集合替换，未提交已有步骤会删除。保留原步骤 id、case_data 和排序。description/case_flow 是文字信息，执行依赖已关联具体操作。

## 导航与动作

步骤详情字段包含 step_type、element、ope_key、ope_value、description。step_type 0 为操作，1 为断言。

```json
{"step_type":0,"ope_key":"goto","ope_value":{"url":"https://example.com/"},"description":"导航到已确认地址"}
```

当前执行器导航方法为 goto，无需 element。open_url 不能当成其同义操作提交。click 参数为 {}，fill 参数为 {"value":"内容"}。assert_visible 参数为 {}，assert_text 为 {"expected":"预期全文"}，assert_contain_text 为 {"expected":"预期片段"}。元素操作/断言引用查询返回的真实 element ID。

locator_type 支持 css、xpath、text、role、label、placeholder、test_id、id、name，具体表达式需核对实际解析器。NEVER 将 `text=section-text=标题` 之类未经验证的拼接字符串保存为定位。

## 执行入口

当前平台 UI 执行通过 WebSocket `/ws/ui/web/` 派发。MUST 发现并使用运行环境提供的执行工具或界面操作，核对其环境、身份和执行器参数。不得猜测 POST /testcases/<id>/execute/ 存在。只读 execute-data 不会启动任务。没有执行工具或在线执行器时报告 blocked，并保留已构建资产。

MUST 等待真实记录终态，记录超时仍为未知/阻塞，不能推断通过或重复派发。HTTP 200 只证明请求响应，不代表业务测试通过。
