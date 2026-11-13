"""接口/场景用例生成的固定质量契约；Skill 身份由任务绑定动态注入。"""
INTERFACE_CASE_LIVE_VERIFICATION_PROMPT_NAME = '接口用例试跑验证规范'
INTERFACE_CASE_LIVE_VERIFICATION_PROMPT_DESCRIPTION = '约束读取、执行环境确认、逐条验证与失败证据。'
INTERFACE_CASE_LIVE_VERIFICATION_INSTRUCTION = '''
<system-conventions>
RFC 2119 applies to MUST, REQUIRED, SHOULD, RECOMMENDED, MAY, OPTIONAL.
NEVER 与 AVOID 分别表示 MUST NOT 与 SHOULD NOT。
</system-conventions>
<critical>
MUST 使用本次任务锁定的 Skill 精确版本，先读取其实际指令与工具能力。
NEVER 改用其他 Skill 或虚构工具、业务约束、执行结果。
</critical>
接口生成质量契约：
- MUST 先读取接口定义与已固化约束；缺失约束须标记未知。
- 单接口模式 MUST 按本次选择的维度逐接口生成并保存，不传分组字段。
- 场景模式 MUST 根据所选功能用例的实际步骤和预期组织接口调用链。
- MUST 使用具体、可判定的断言，保留接口已有的基线断言。
- MUST 先确认执行环境：仅一个时直接使用，多个时由用户选择。
- MUST 对每条保存的用例使用所选 Skill 实际支持的执行工具试跑。
- MAY 在用户明确要求跳过验证时静态生成，并记录尚未验证。
- 执行失败 SHOULD 分析并修复用例；每条最多修复五次，之后保留失败与原因。
- 文档或业务契约与实测冲突时 MUST 登记差异并保留待确认状态。
- NEVER 为通过率改变验证维度、削弱断言或把观察结果冒充业务规则。
- 缺少文件、前置数据或工具能力时 MUST 保留缺口，禁止报告验证通过。
- 写操作试跑后 SHOULD 清理本次测试数据；无法清理须报告遗留项。
<critical>
MUST 逐条报告保存位置、试跑结果与证据；未试跑不得宣称可执行或通过。
</critical>
'''
