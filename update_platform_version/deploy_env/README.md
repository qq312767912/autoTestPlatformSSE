# WHartTest v2.8 内网 ARM64 增量升级包

本包对应 `dev@ac65a6fbf0f0`，目标为麒麟 ARM64（64KB 页）离线环境。

## 包目录结构

```text
update_platform_version/
├── deploy_env/       # 本 zip 解压后的脚本
└── images/           # 6 个镜像分卷，不放入 zip
```

请将 `deploy_env.zip` 与 `images/` 一起携带到内网。解压后保持上述同级目录结构。

## 可复用镜像

本次不重新打包 PostgreSQL、Redis、Qdrant、Playwright MCP、Vision MCP、WHartTest MCP 和微信插件宿主。详见 `IMAGE_MANIFEST.txt`。

## 升级步骤

在内网服务器上进入 `deploy_env` 目录，依次执行：

```bash
bash 01-precheck.sh
bash 02-backup.sh
bash 03-import-images.sh
bash 04-deploy.sh
bash 05-verify.sh
```

如果旧的执行器密钥文件与平台当前密码不一致，`07-start-actuators.sh`
会在启动前拒绝继续。请在 `update_platform_version/` 目录执行独立修复脚本：

```bash
bash fix-actuator-api-credentials.sh
```

该脚本会先与 Backend 校验新凭据，成功后才替换 secret，然后清理卡住的 slot 并重建三个执行器。

如果录制浏览器执行内网 SSE 用例时报 `ERR_NAME_NOT_RESOLVED`，请在
`update_platform_version/` 目录执行独立脚本：

```bash
bash fix-ui-test-dns.sh
```

默认将 SSE 测试域名映射到 `10.122.215.111`。如内网实际 IP 不同，使用
`SSE_TEST_HOST_IP=<实际IP> bash fix-ui-test-dns.sh`。

如果 Backend 日志中 `celery_worker` 每隔约 20～30 秒退出，并出现
`kombu.exceptions.VersionMismatch`，请上传新版 `deploy_env.zip`、三个 Backend
镜像分包和独立部署脚本，然后执行：

```bash
bash deploy-backend-kombu562-r1.sh
```

该脚本导入重建后的 Alpine/musl Backend，固定 `celery 5.4.0 + kombu 5.6.2 +
redis-py 5.2.0`，只重建 Backend，并验证 Redis 协议连接和 worker 45 秒不重启。

默认基础 Compose 文件为 `/projects/ai-test-platform/offline-images/docker-compose.offline.yml`。如位置不同，先设置：

```bash
export BASE_COMPOSE=/实际路径/docker-compose.offline.yml
```

## Backend 强制验证

`03-import-images.sh` 会在启动前阻断不合格镜像，必须同时满足：

- `linux/arm64`
- `/etc/os-release` 为 Alpine Linux
- `ldd` 为 musl
- 存在 `ocr` CLI，且 `ocr --version` 成功
- npm 全局安装 `@alibaba-group/open-code-review`
- 内置 Alpine 系统 Chromium 和录制器 Node Playwright，运行时无需联网下载
- Python 依赖为 `celery 5.4.0 + kombu 5.6.2 + redis-py 5.2.0`
- Backend OCI revision 为 `ac65a6fb-v2.8-r3-kombu562`

## 备份与回滚

升级前备份由 `02-backup.sh` 生成，默认保存在 `deploy_env` 同级的 `backups/时间戳/`，不写入可被升级包覆盖的 `deploy_env/` 内。需回滚时执行 `bash 06-rollback-app.sh`。

## ONNX Runtime 离线备用件

验证码识别仍优先自动调用 Vision MCP/RapidOCR。为避免内网环境受第三方 wheel 缺失影响，本包额外携带了从官方 `v1.29.0` 源码自行编译的 Python 3.12 ARM64/musl wheel：

```text
wheels/onnxruntime-1.29.0-cp312-cp312-musllinux_1_2_aarch64.whl
```

该 wheel 已在干净 `python:3.12-alpine` ARM64 容器中完成安装和导入验证，可用的执行提供器为 `CPUExecutionProvider`。可复现构建脚本为 `update_platform_version/build-onnxruntime-musl-arm64.sh`。
