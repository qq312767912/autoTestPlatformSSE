# Skill 报告与版本管理修复

## 行为

- 用例审查保留标准工作表；Skill 可声明额外工作表，AI 返回对应 rows，后端统一渲染。
- 明确声明但未返回数据的 sheet 仍生成，显示缺失警告；摘要同步记录警告。不会自动恢复已取消的边界分析融合。
- 上传前读取 ZIP 中名称和版本；同名包追加到公共目录正本。版本冲突可在上传页面改号，只改暂存副本，重新校验及计算哈希。
- Card 展示最近上传的版本，含下载和版本管理入口。每个版本下载精确内容。文件缺失显示禁用提示。
- 删除旧版本为可恢复归档；生效版本和最后一个可见版本不能删。包目录保留，既有锁定任务继续可追溯；新任务不会自动选择已删除版本。

## 声明示例

将如下 report 字段放入 SKILL.md 的 YAML frontmatter。新增工作表只依赖这个包，不必再改平台代码。下面只演示通用附加分析，不把已取消的两个 Skill 重新融合。

```yaml
---
name: sample-review
version: 1.0.0
description: 评审用例并输出待确认的验收标准。
report:
  sheets:
    - name: 验收标准核查
      tab_color: F4B183
      header_color: 2F75B5
      columns:
        - key: case_id
          title: 用例编号
          width: 24
        - key: finding
          title: 待确认标准
          width: 64
        - key: status
          title: 状态
      highlight:
        - column: status
          equals: 待确认
          fill: FFF2CC
          color: 9C6500
          bold: true
---
按输入用例核查验收标准，将待确认项输出到上述工作表，不编造业务标准。
```

模型输出：`report_sheets: [{name: 验收标准核查, rows: [{case_id: TC-1, finding: 缺少允许的响应时长, status: 待确认}]}]`。

列 key 为数据键，title 为 Excel 表头。高亮支持 equals/contains 和 scope: row。Skill 仅文字描述额外 sheet 时，模型也可在 report_sheets 中返回 name/columns/rows/highlight，平台执行相同校验与渲染；需要稳定结构时使用上面的声明。

## 限制

最多 12 个额外工作表、每表 40 列/20000 行；表名遵循 Excel 规则，不能覆盖标准表。模型字符串按文本写入，不执行 Skill 自带 Python 或任意 Excel 公式。删除不回收包目录空间，恢复后可重新用于新任务。
