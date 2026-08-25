# Vision MCP

多模态视觉 MCP 服务器 — 为 DeepSeek 等纯文本模型桥接图像/视频理解能力。

## WHartTest 集成版

本版本默认以 Streamable HTTP 运行，地址为 `http://0.0.0.0:8010/mcp`，并增加：

- DOCX/PDF需求图片提取及上下文关联；
- RapidOCR本地文字与坐标提取；
- GLM视觉语义、控件、表格和变更标注识别；
- 需求截图与Playwright页面模型比较；
- 不调用视觉API的确定性页面模型比较；
- 模型、接口路径、超时和重试全部环境变量化。

启动：

```bash
cp .env.example .env
docker build -t vision-mcp:0.2.0 .
docker run --rm --env-file .env -p 8010:8010 \
  -v /your/shared/data:/app/data vision-mcp:0.2.0
```

WHartTest远程MCP配置：

```text
名称：vision-mcp
URL：http://vision-mcp:8010/mcp
传输协议：streamable-http
```

## 架构

```
Claude Code (DeepSeek, 纯文本)
        │
        │ MCP 协议
        ▼
Vision MCP Server (本服务)
        │
        │ GLM-4.6V-Flash API (免费)
        ▼
图片/视频 → 结构化文字描述
```

## 安装

```bash
cd vision-mcp
uv sync
```

## 配置

在 Claude Code 的 `settings.json` 中添加：

```json
{
  "mcpServers": {
    "vision": {
      "command": "uv",
      "args": ["run", "--directory", "F:/project/vision-mcp", "vision-mcp"],
      "env": {
        "VISION_MCP_API_KEY": "你的智谱API Key",
        "VISION_MCP_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
        "VISION_MCP_MODEL": "glm-4.6v-flash"
      }
    }
  }
}
```

## 工具列表

| 工具 | 功能 |
|------|------|
| `ui_to_artifact` | UI 截图 → 代码提示词/设计规范/描述 |
| `extract_text_from_screenshot` | OCR 文字提取（代码/终端/文档） |
| `diagnose_error_screenshot` | 错误截图诊断 + 修复建议 |
| `understand_technical_diagram` | 架构图/流程图/UML 解读 |
| `analyze_data_visualization` | 仪表盘/图表数据分析 |
| `ui_diff_check` | UI 设计稿 vs 实现对比 |
| `image_analysis` | 通用图像理解 |
| `video_analysis` | 视频内容解析 (MP4/MOV, ≤8MB) |

## 视觉模型

默认使用 **GLM-4.6V-Flash**（智谱免费视觉模型）：
- 128K 上下文
- 支持图片/视频/文件
- 支持思考模式
- 兼容 OpenAI API 格式
