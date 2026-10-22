# v3 最小热修复

本包严格基于内网镜像原始文件 `a1072297...455f` 生成，只修改代码审查确定缺陷分类逻辑。

可直接执行；如果检测到 v2，脚本会从 Backend 原始镜像自动恢复基线后再应用 v3：

```bash
bash confirmed-defect-hotfix-v3/apply.sh
```

脚本只接受镜像原始版本、已知 v2 或已经生效的 v3；其他未知版本会停止，避免覆盖额外修复。

回滚：

```bash
bash confirmed-defect-hotfix-v3/rollback.sh
```
