# WHartTest 内网 ARM64 正式增量升级包

- 代码基线：`dev@e22e1f22`
- 镜像基线：`ecff56e40673d502bd85e702dbf4e11ef969f2af`
- 交付版本：`update-ecff56e4-r2-arm64`
- 目标架构：`linux/arm64`

## 本次替换

- Backend：`wharttest-250-backend:update-ecff56e4-r1-arm64`
- Frontend：`wharttest-250-frontend:update-ecff56e4-r1-arm64`

PostgreSQL、Redis、Qdrant、MCP、Playwright MCP、Vision MCP、微信插件宿主、
三个 Actuator 和所有数据卷全部复用内网现有版本。`04-deploy.sh`
只替换 Backend 和 Frontend，不会执行 `docker compose down`，不会删除数据卷。

Backend 和 Playwright MCP 已内置测试站点的内网 hosts 映射，默认 IP 为
`10.122.215.111`。如目标 IP 变更，在执行 Compose 前设置：

```bash
export SSE_TEST_HOST_IP=新IP
```

## 放置位置

将 `images` 和解压后的 `deploy_env` 放在：

```text
/projects/ai-test-platform/update_platform_version/
├── images/
└── deploy_env/
```

内网当前 Compose 文件默认为：

```text
/projects/ai-test-platform/offline-images/docker-compose.offline.yml
```

如实际路径不同，执行前设置 `BASE_COMPOSE`。

## 执行顺序

```bash
cd /projects/ai-test-platform/update_platform_version/deploy_env
bash 01-precheck.sh
bash 02-backup.sh
bash 03-import-images.sh
bash 04-deploy.sh
bash 05-verify.sh
```

镜像已按 280 MB 分卷，`03-import-images.sh` 会直接合并流式导入，
无需手工生成完整 tar 文件。

## 回退

```bash
bash 06-rollback-app.sh
```

回退脚本恢复升级前记录的 Backend/Frontend 镜像，不动数据卷。
禁止执行 `docker compose down -v` 或手工删除任何数据卷。
