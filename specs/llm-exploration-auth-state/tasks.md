# Implementation Plan

- [x] 1. 建立会话绑定与审计数据模型
  - 为 `ChatSession` 增加可空登录态绑定和绑定时间
  - 新增不保存凭据内容的 LLM 登录态使用审计模型及迁移
  - _Requirement: R1, R2, R5_

- [x] 2. 实现登录态解析与安全摘要 API
  - 按当前用户和项目权限查询登录态
  - 返回名称、环境、凭据摘要与过期状态，不返回 `state_json`
  - 实现 ID、显式解除和严格名称解析
  - _Requirement: R1, R3, R4_

- [x] 3. 接入 Agent Loop 会话生命周期
  - stream、non-stream、resume 入口统一解析绑定
  - 向 UI 返回实际绑定名称，绑定失败时在导航前终止
  - 切换或解除绑定时清理旧 Playwright 上下文
  - _Requirement: R1, R3, R5_

- [x] 4. 在持久化 Playwright 上下文安全注入 storageState
  - Backend 私下读取、校验并过滤过期 Cookie
  - Python 与 Node 本地 RPC 初始化上下文，不进入 LLM 工具参数或日志
  - 同一绑定复用上下文，不同绑定隔离并重建
  - _Requirement: R2, R3, R5_

- [x] 5. 增加聊天页登录态选择与绑定状态展示
  - 使用安全摘要接口按环境展示登录态
  - 请求携带 `auth_state_id`，支持不使用/切换/恢复会话绑定
  - 过期和停用状态不可选择
  - _Requirement: R1, R3, R4_

- [x] 6. 补齐自动化测试并完成本机验收
  - 覆盖权限、摘要脱敏、绑定语义、过期判断和上下文隔离
  - 执行后端测试、前端类型检查/构建及相关回归
  - 检查日志与响应不含 Cookie/token
  - _Requirement: R1, R2, R3, R4, R5_

- [ ] 7. 提交并推送 `origin/dev`
  - 只提交本功能文件，不包含已有无关改动和本地部署产物
  - _Requirement: R1, R2, R3, R4, R5_
