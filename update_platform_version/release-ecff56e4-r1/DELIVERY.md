# 交付清单

- 代码基线：`dev@e22e1f22`
- 镜像基线：`ecff56e40673d502bd85e702dbf4e11ef969f2af`
- 目标架构：`linux/arm64`
- 新镜像：Backend、Frontend
- 复用镜像：PostgreSQL、Redis、Qdrant、MCP、Playwright MCP、
  Vision MCP、微信插件宿主、三个 Actuator
- 数据卷：全部保留，不复制、不删除、不新建替代卷

## 需复制到内网的文件

1. `images/` 下全部 3 个分卷。
2. `deploy_env-ecff56e4-r2-arm64.tar.gz`。

部署步骤见压缩包内 `deploy_env/README.md`。
