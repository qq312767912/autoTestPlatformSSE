说明：构建并部署 Qdrant 无 jemalloc 镜像（离线/内网流程）

文件清单：
- docker/Dockerfile.nojemalloc  —— Dockerfile，用于在外网主机上构建 no‑jemalloc 的 qdrant 镜像（multi-stage）
- scripts/patch_no_jemalloc.sh  —— 源码修补脚本（best-effort），尝试删除 jemallocator 依赖并替换全局分配器为 System
- scripts/build_qdrant_no_jemalloc.sh —— 在外网主机上用 buildx 构建 linux/arm64 镜像并导出 tar
- scripts/deploy_qdrant_internal.sh —— 在内网主机上加载 tar、打 tag 并推送到内网 registry，可选替换 docker compose

快速使用：
1) 在一台能上外网且安装 Docker 的主机上，把仓库 clone 或把上述文件复制过去。
2) 运行构建并导出 tar（示例）：

```bash
chmod +x scripts/build_qdrant_no_jemalloc.sh scripts/deploy_qdrant_internal.sh scripts/patch_no_jemalloc.sh
./scripts/build_qdrant_no_jemalloc.sh my-qdrant:no-jemalloc v1.18.3 ./qdrant_v1.18.3_nojemalloc.tar
```

3) 将生成的 tar 文件通过安全方式复制到内网主机（scp/usb 等），然后在内网主机上运行：

```bash
chmod +x scripts/deploy_qdrant_internal.sh
./scripts/deploy_qdrant_internal.sh /tmp/qdrant_v1.18.3_nojemalloc.tar my-registry.local/qdrant/qdrant:v1.18.3 docker-compose.yml
```

注意与风险说明：
- 本补丁脚本为 best-effort 自动化修改，可能需要手工调整源码以匹配具体 qdrant 版本。使用前建议在外网测试环境验证二进制正确性与功能。
- 构建会耗时并需要适当的 RAM/CPU；建议使用有 buildx 支持的主机并启用 QEMU if cross-building.
- 若源码使用静态链接的 jemalloc（或没有 jemallocator symbols），此方法可能无效；如失败请回退到“直接从 upstream 获取 no‑jemalloc 镜像”或“深入修改源码以使用 System allocator”。
