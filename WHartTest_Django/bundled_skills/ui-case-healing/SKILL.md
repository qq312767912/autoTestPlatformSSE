---
name: ui-case-healing
description: 修复已有 UI 自动化用例的定位与交互故障，保留业务预期，由平台重跑验证。
---
# UI 用例自愈

<system-conventions>RFC 2119 applies to MUST, SHOULD, MAY. NEVER means MUST NOT.</system-conventions>
<critical>
你 MUST 读取 get_ui_healing_context，仅维护当前 UI 用例。
你 NEVER 删除、跳过、弱化或替换业务断言，NEVER 新建替代用例或修改业务用例绑定。
</critical>

1. MUST 读取上下文中的失败执行记录、步骤和实际环境。缺少登录态、测试数据或环境不可用时 MUST 说明阻塞原因并停止修复。
2. MUST 使用 ui_generation_browser 查看真实 DOM；先 open [起始URL]，再 snapshot []，必要时 click/fill/get。NEVER 编造定位器或将未观察到的业务结果当成通过。
3. MUST 使用本 Skill 的 ui_automation_tools.py 查询和修复资产。例如 `python ui_automation_tools.py --action get_testcase --testcase_id <当前ID>`；参数以 `--help` 为准。MUST 仅修复定位、等待、交互配置；共享步骤和元素的修改会影响其他用例，MUST 在总结中列出影响。
4. NEVER 调用 execute_testcase；平台在本轮修复结束后统一重跑一次，并以真实执行记录判定自愈结果。
5. MUST 总结修改点与阻塞原因；NEVER 自行宣布自愈成功。
