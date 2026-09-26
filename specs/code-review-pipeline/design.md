# 统一代码审查流水线设计

共享前段为 Diff 清单、Tree-sitter AST 切块、结构化相关代码检索和静态分析器注册表。快速模式在规则融合后结束；标准模式将 AST、相关源码、业务文档和规则命中交给 LLM；深度模式以 OpenCodeReview 为语义主审，平台 AI 仅补审失败文件。

Semgrep使用独立扫描器镜像和随镜像发布的离线规则包，并关闭遥测。Backend通过内部 HTTP 接口提交变更文件，因此不将 Semgrep 及其 glibc 依赖塞入必须保持 Alpine/musl 的 Backend；扫描器缺失时报告明确标记跳过。CodeQL和SonarQube只作为外部结果适配器。交付以独立 HTML 报告为主，不回写 Reviewdog/MR 评论。
