# 代码审查确定缺陷分类热修复

适用提交：`1d74f76f`

## 应用

将 zip 包完整解压，保持 `WHartTest_Django/` 与 `update_platform_version/` 同级，然后执行：

```bash
cd <解压目录>
bash update_platform_version/apply-code-review-confirmed-defect-hotfix.sh
```

如果 Backend 容器名不是 `wharttest-backend`：

```bash
BACKEND_CONTAINER=<实际容器名> bash update_platform_version/apply-code-review-confirmed-defect-hotfix.sh
```

脚本只替换 Backend 的 `/app/code_analysis/services.py`，不修改数据库，也不重建其他容器。
它会自动备份旧文件、校验热修复文件 SHA256、执行 Python 语法检查，并重启
Backend 容器。执行前请确认没有必须保留的后台任务。

## 回滚

```bash
bash update_platform_version/rollback-code-review-confirmed-defect-hotfix.sh
```

回滚脚本会读取最近一次应用时生成的备份并恢复原文件，然后重启 Backend 进程。

## 持久性说明

- 如果 `/app/code_analysis/services.py` 是宿主机绑定挂载，脚本会更新挂载源文件，容器重建后仍有效。
- 如果文件来自镜像内部，脚本会更新当前容器可写层；普通 `docker restart` 后仍有效，但重新创建 Backend 容器后会丢失。后续正式升级镜像时应包含提交 `1d74f76f`。
