# 前端热修复通道：报告导出标题与文件名改用代码仓库名

## 这个包解决什么

代码审查 / 测试分析报告**导出 HTML** 时，报告顶部标题与下载文件名原本是
「`代码审查报告_<平台项目名>`」。同一平台项目下可以有多个代码仓库，用项目名会让不同仓库的
报告同名、无法区分。本次改为「`代码审查报告_<代码仓库名>`」（测试分析报告同理）。

改动落点在 `WHartTest_Vue/src/features/code-analysis/reportExport.ts` —— 报告是**前端在浏览器里**
拼 HTML 再 Blob 下载的，后端只提供 Markdown。因此：

| 通道 | 能覆盖到吗 | 说明 |
| --- | --- | --- |
| `deploy_env/hotfix/`（Python 代码覆盖层） | ❌ | 只能覆盖 Backend 容器内的文件 |
| `deploy_env/04-deploy.sh` 换镜像 | ✅ 但要重建镜像 | 需要 ARM64 构建机 `docker save`，不是热修复 |
| **本包（挂载已构建产物）** | ✅ | 不重建镜像、不联网，重建一次 Frontend 容器即生效 |

## 内网怎么用

把整个 `frontend-hotfix/` 目录拷到内网 `/projects/ai-test-platform/update_platform_version/`，
保持目录名不变（脚本按 `../deploy_env/docker-compose.update.yml` 定位升级覆盖层）。

```bash
cd /projects/ai-test-platform/update_platform_version/frontend-hotfix

# 应用
bash 25-apply-frontend-report-title-hotfix.sh

# 复核（脚本内已含报告命名口径断言）
bash ../deploy_env/05-verify.sh

# 回退（恢复镜像内产物）
bash 25-apply-frontend-report-title-hotfix.sh --revert
```

执行时脚本会依次：校验产物包 SHA256 → 解包 → **在动容器之前**先断言产物确是新口径 →
只读挂载到 `/usr/share/nginx/html` 并重建 Frontend → 事后核对挂载来源与容器内实际产物。
任一步失败都会在碰容器之前中止，不会留下半成品。

## 目录构成

| 文件 | 作用 |
| --- | --- |
| `25-apply-frontend-report-title-hotfix.sh` | 应用 / 回退（`--revert`） |
| `docker-compose.frontend-hotfix.yml` | 覆盖层：把 dist 只读挂到 `/usr/share/nginx/html`；挂载源必须由 `FRONTEND_DIST_DIR` 显式给出，未设置时 compose 直接报错，避免把站点根目录挂成空目录 |
| `frontend-report-title-dist.tar.gz` | 已构建的前端产物（137 个文件，约 4.4 MB） |
| `SHA256SUMS` | 产物包校验和 |
| `pack-frontend-hotfix.sh` | 【联网构建机专用】重新构建并打包产物，内网不执行 |

## 三条必须知道的边界

1. **这是临时通道，不是发布通道。** 生效的是挂载产物而不是镜像本体。要让修复永久生效，
   请重建 `wharttest-250-frontend` 镜像（`wharttest-image-release` 流程）并走 03/04 正常升级。
2. **下一次 `04-deploy.sh` 会把它卸掉。** `04-deploy.sh` 用
   `base + docker-compose.update.yml` 重建 Frontend，不带本覆盖层，挂载随之消失、站点回到
   镜像内产物（即旧口径）。这不是故障，是这条通道的固有代价——届时如果镜像还没重建，
   重新执行一次本脚本即可。
3. **`05-verify.sh` 的报告命名断言此时校验的就是挂载产物。** 它证明的是「站点当前产物是新口径」，
   不代表镜像已经更新。镜像更新与否由 `03-import-images.sh` 的镜像 revision 校验负责。

## 判据说明（避免两处口径漂移）

`05-verify.sh` 与 `25-apply-*.sh` 用的是**同一条判据**：在产物里找到 `代码审查报告_` /
`测试分析报告_` 前缀字面量后，检查其后 200 字符窗口内是否出现 `repository_name`。

只断言「产物里含 `代码审查报告_`」是不够的——旧版同样含这个字符串（旧写法是
`` `代码审查报告_${task.project_name || ...}` ``），两种产物都能过。加上邻近窗口判据后：
旧产物用的是 `project_name`，直接过不了；这一点在交付前用改动前后的两份产物做过对照验证
（旧产物被拦下、新产物通过）。
