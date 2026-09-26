# Implementation Plan

- [x] 增加 Tree-sitter AST 切块及ARM64 musl依赖
- [x] 增加结构化相关代码检索并接入LLM上下文
- [x] 增加 Semgrep 可插拔扫描器与离线规则包
- [x] 快速模式复用确定性扫描，标准模式执行融合审查
- [x] 深度模式使用 OCR 主审和失败范围补审
- [x] 增加独立 Semgrep 扫描器镜像与 HTTP 扫描接口
- [x] 按需求保持独立 HTML 报告，不增加 Reviewdog/MR 回写
