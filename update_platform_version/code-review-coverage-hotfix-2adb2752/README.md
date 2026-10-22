# 代码审查覆盖与三分类热修复

对应 Git 提交：`2adb2752`。

修复内容：

- 按实际完成文件计算 AI 覆盖率，并记录失败批次、文件和原因。
- JSON 输出异常时只重试当前 AI 批次。
- 排除二进制、构建日志和空 Diff，避免虚高覆盖率。
- 保留全部确定缺陷、待确认风险和改进建议；反证批次上限不再截断最终报告。
- HTML 与 Markdown 报告展示逐文件真实审查状态。

## 使用

1. 将整个目录复制到内网部署服务器并解压。
2. 确认当前没有正在执行的代码审查任务。
3. 如容器名称不是默认值，可设置 `BACKEND_CONTAINER`、`FRONTEND_CONTAINER`。
4. 执行：

   ```bash
   bash apply.sh
   ```

默认容器名：`wharttest-backend`、`wharttest-frontend`。

## 回滚

```bash
bash rollback.sh
```

脚本会在 `backups/<时间戳>/` 中保存后端源码和完整前端静态目录，并记录最近一次应用状态。
