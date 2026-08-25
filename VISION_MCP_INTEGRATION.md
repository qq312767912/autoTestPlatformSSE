# Vision MCP 接入

## 启动

在 `.env` 中填写 `VISION_MCP_API_KEY`，然后运行：

```bash
docker compose -f docker-compose.yml -f docker-compose.vision.yml up -d --build vision-mcp backend
```

## 注册工具

```bash
docker compose -f docker-compose.yml -f docker-compose.vision.yml exec backend \
  python manage.py configure_vision_mcp
```

注册后平台会同步以下工具并交给现有Agent动态调用：

- `extract_requirement_images`
- `extract_text_from_screenshot`
- `analyze_requirement_ui`
- `compare_page_models`
- `ui_diff_check`
- `compare_requirement_with_page_model`
- `image_analysis`

## 推荐调用顺序

1. `extract_requirement_images` 提取需求文档截图与上下文。
2. `analyze_requirement_ui` 优先由视觉AI直接解析原图并生成需求页面模型；OCR在视觉结果返回后单独保留副本，仅在视觉分析失败时兜底，不参与覆盖AI结构化字段。
3. Playwright获取当前页面结构和截图。
4. `compare_page_models`先执行确定性差异比较。
5. 有视觉标注、图标或布局问题时，再调用`compare_requirement_with_page_model`复核。

Vision MCP与backend共享`/app/data`，与Playwright MCP共享`/tmp/playwright-output`，调用工具时应使用这些容器内路径。
