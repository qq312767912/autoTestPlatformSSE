---
name: vision-analysis
description: 直接调用视觉模型分析需求截图、提取DOCX/PDF图片，并比较需求截图与Playwright页面截图或页面模型。适用于需求图片结构化、表格Markdown识别、页面差异分析；视觉AI结果优先，OCR仅作兜底，不依赖独立Vision MCP服务。
---

# Vision Analysis 图片分析

## 使用原则

1. 视觉AI直接读取原图，结构化结果是主结果。
2. OCR不注入视觉AI提示词、不覆盖AI字段，只在视觉分析失败或没有有效结构时兜底。
3. OCR与可靠AI结果不一致属于识别质量问题，不作为业务冲突。
4. 仅当AI结果本身不确定，或与需求正文、附件、知识库、人工确认结论冲突时，生成待确认项。
5. 输出JSON供其他Skill继续处理；原图和OCR副本不写回原始需求文档。

## 配置

按以下顺序读取环境变量：

- API地址：`VISION_API_BASE_URL` → `VISION_MCP_BASE_URL`；
- API Key：`VISION_API_KEY` → `VISION_MCP_API_KEY` → `MIMO_API_KEY`；
- 模型：`VISION_MODEL` → `VISION_MCP_MODEL`，默认`mimo-v2.5`；
- Chat路径：`VISION_API_CHAT_PATH` → `VISION_MCP_CHAT_COMPLETIONS_PATH`，默认`/chat/completions`。

不要在命令行参数、日志或产物中输出API Key。

## 可用操作

### 分析需求截图

```bash
python vision_tools.py --action analyze_requirement_ui \
  --image_path "/app/data/media/requirements/page.png" \
  --document_context "需求正文附近内容" \
  --change_hint "新增查询条件"
```

返回页面摘要、区域控件、字段、业务规则、表格Markdown、标注、建议测试点、置信度，以及单独的OCR兜底副本。

### 提取需求文档图片

```bash
python vision_tools.py --action extract_requirement_images \
  --document_path "/app/data/media/requirements/需求方案.docx" \
  --output_dir "$ARTIFACTS_DIR/requirement-images"
```

支持DOCX；PDF在运行环境存在`pypdf`时提取内嵌图片。

### 对比需求截图与当前页面截图

```bash
python vision_tools.py --action ui_diff_check \
  --requirement_path "/app/data/requirement.png" \
  --implementation_path "$SCREENSHOT_DIR/current.png" \
  --requirement_context "需求要求新增状态筛选"
```

### 对比两个结构化页面模型

```bash
python vision_tools.py --action compare_page_models \
  --expected_json_file expected.json \
  --actual_json_file actual.json
```

### 通用图片理解

```bash
python vision_tools.py --action image_analysis \
  --image_path screenshot.png \
  --question "识别页面中的表格、按钮和异常提示"
```

## 与其他Skill协作

- 星企航系统测试Skill：先调用本Skill分析已提取需求图片，再生成原子需求和测试方案。
- Playwright Skill：先采集当前页面截图和页面模型，再调用`ui_diff_check`或`compare_page_models`。
- 第一阶段若直接视觉API不可用，可继续使用现有Vision MCP作为临时回退；不得同时合并两个视觉结果制造重复上下文。
