# Implementation Plan

- [ ] 1. 建立后端数据模型与迁移
  - 新增映射、配置状态、发布版本、节点状态和诊断记录
  - 实现域名/IPv4 规范化、约束和自定义权限
  - _Requirement: FR-1, FR-2, FR-3, FR-4_

- [ ] 2. 实现发布、回滚与差异服务
  - 实现草稿修订、稳定快照、SHA-256、并发冲突与回滚新版本
  - 为映射写操作维护草稿修订号
  - _Requirement: FR-1, FR-2_

- [ ] 3. 实现管理端 API、Agent API 与审计接入
  - 新增 CRUD、概览、差异、发布、版本、回滚、节点和诊断 API
  - 新增独立 Token 认证的快照导出与节点上报 API
  - 纳入现有操作日志模块映射
  - _Requirement: FR-1, FR-2, FR-3, FR-4_

- [ ] 4. 实现安全异步诊断
  - 实现 DNS、IP 一致性、TCP 80/443 和 HTTP/HTTPS 诊断
  - 加入 SSRF 目标校验、超时、重定向和返回体限制
  - _Requirement: FR-4_

- [ ] 5. 实现宿主机同步服务
  - 实现快照获取、checksum 校验、hosts 受管区块替换、备份、容器白名单发现与节点上报
  - 新增 systemd service/timer、安装脚本和单元测试
  - _Requirement: FR-3, NFR-安全, NFR-可用性_

- [ ] 6. 实现前端管理页
  - 新增类型、API service、配置表格、编辑抽屉、发布差异、节点状态、诊断和版本回滚
  - 新增路由、菜单、权限门禁和中英文文案
  - _Requirement: UI 规格, FR-1, FR-2, FR-3, FR-4_

- [ ] 7. 集成内网离线部署
  - 为 Backend 和宿主机同步服务配置共享 secret
  - 将安装、升级、备份和验证步骤纳入 `deploy_env`
  - 保持 Backend Alpine/musl、open-code-review 和 `ocr` CLI 构建约束
  - _Requirement: NFR-离线, NFR-兼容, FR-3_

- [ ] 8. 完成自动化验证
  - 运行 Django 迁移检查和后端单元测试
  - 运行同步服务单元测试与 Shell/Python 语法检查
  - 运行 Vue TypeScript 检查、单元测试和生产构建
  - 启动本地服务并在浏览器验证路由、列表、表单、发布与节点状态
  - _Requirement: 全部验收标准_
