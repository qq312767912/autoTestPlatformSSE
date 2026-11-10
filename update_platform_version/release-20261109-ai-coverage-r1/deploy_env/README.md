# 20261109 AI 代码审查完整版本升级

本包基于真实项目当前工作区，包含未提交业务改动；不使用旧版热补丁覆盖后端源码。
Backend / Frontend / Semgrep 重建；CRG、Actuator、Vision MCP 和基础服务复用内网现有镜像。
Backend 使用 WHartTest_Django/Dockerfile.alpine，完整 Alpine/musl ARM64 构建，强制安装并验证 OpenCodeReview / ocr；Semgrep 同为 Alpine/musl ARM64。Alpine 使用阿里云源。

## 放置位置

将 deploy_env.zip 解压到 /projects/ai-test-platform/update_platform_version/，得到 deploy_env/。
镜像分卷放在同级 images/。不要把旧 docker-compose.hotfix.yml 叠加到新版本。
覆盖原 deploy_env 前先备份该目录，尤其保留 secrets/ 和原 compose；压缩包不含密钥。

## 执行顺序

在 deploy_env 中依次运行：

```bash
bash 00-preflight.sh
bash 01-precheck.sh
bash 02-backup.sh
bash 03-import-images.sh
bash 04-deploy.sh
bash 05-verify.sh
```

02 会备份数据库、数据目录及当前镜像信息，短暂停应用写入。
04 会运行数据库迁移、更新内置技能、恢复执行器和域名同步；执行器凭据沿用内网 secrets。
迁移后的数据库不保证可直接回退旧应用；回退前先核对迁移兼容性，必要时恢复完整备份。
06 接收本次 02 生成的备份目录，按原镜像 ID 回退四个应用服务，保留新 compose 配置；不会自动回退数据库和技能。

## 必须人工验收

用内网现有仓库发起标准模式任务：确认 AI 覆盖率 > 0，并检查是否达到完整覆盖；确认无 helper NameError。
AI 不完整时应显示 partial，不能把机器规则零命中视为 AI 审查通过。
本机 Docker ARM64 验证不能替代麒麟 ARM64 64KB 页实机验证。

## CRG 补包修正
内网缺少原清单 CRG，本包增加 crg-20261109-ai-coverage-r1-arm64.tar.gz.part*。它复用本地已验证 Alpine/musl ARM64 镜像，原始 OCI revision 为 local，镜像 ID 为 sha256:27d5dedd238d27ee1f258dd3a89ea6ce5dc4f6f5b5032ceddc8f8f10db32f546。不是本次重建。先导入再部署；原有三个镜像分卷无需重传。
