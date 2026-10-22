# 代码审查覆盖与待确认项修复（内网 v2）

本包修复 AI 覆盖统计、失败批次记录和三类审查结果保留。只修改 Backend 的 `/app/code_analysis/services.py`。HTML 报告原有的确定缺陷、待确认风险和改进建议栏目会显示恢复后的候选；逐文件状态的新 HTML 文案需要后续与内网前端版本匹配后单独更新。

适用基线：上一次“确定缺陷 v3”热修复的 `services.py`，SHA256 为 `8f208701e0e4d557b106b8a242a210654b7e2324e4025f6de792cc544be3b945`。其他版本会在修改前停止，避免覆盖内网已有修复。本包不包含 `views.py`，也不替换前端静态目录。

先解压并检查：

```bash
bash apply.sh --check
```

检查通过后，确认没有正在运行的审查任务，再执行：

```bash
bash apply.sh
```

默认容器名为 `wharttest-backend`。如名称不同，先设置 `BACKEND_CONTAINER`。备份和回滚元数据在修改前写入 `backups/`；失败时脚本自动恢复。也可以手动执行：

```bash
bash rollback.sh
```

如果此前失败后容器当前文件并非上述 v3 基线，先不要强行应用；保留 `--check` 输出和现有 `backups/`，据实际版本再制作对应补丁。
