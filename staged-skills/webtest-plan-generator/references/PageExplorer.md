# PageExplorer：页面结构探索（入口A）

## 功能定位

对目标网站进行自动化页面探索，提取页面模块清单、可交互元素、页面流转关系，为后续测试方案和用例生成提供结构化输入。

## 前置条件

- 目标网站 URL 可访问
- 如需登录，已准备认证方式（用户名/密码、Cookies 或 Token）

## 工具选择策略

| 序号 | 工具           | 适用场景                               | 局限性                       |
| ---- | -------------- | -------------------------------------- | ---------------------------- |
| 1    | `web_fetch`    | 纯 HTML 静态页面、快速获取内容         | 无法获取 JS 动态渲染内容     |
| 2    | `browser` 工具 | JS 渲染页面、动态交互                  | 无法处理复杂反爬、需手动操作 |
| 3    | Selenium 脚本  | **推荐主力工具**，可控性强、超时可配置 | 需解决进程超时问题           |

### 推荐方案：Selenium 脚本探索

使用 `robust_explorer.py` 模式（模板见`../templates/robust_explorer.py`），优势：
- 超时时间可精确控制（`timeout=300`）
- headless / 有头模式可切换
- 可保存完整 DOM 结构、截图、JSON 数据
- 页面加载后有稳定等待时间，不依赖 UI 状态

**执行方式（关键）：**

```python
exec(
    command='python "path/to/robust_explorer.py" --url https://example.com --output ./reports --headless',
    background=True,   # 后台执行，避免超时杀进程
    timeout=300        # 5分钟，足够探索一个站点
)
```

## 探索清单（每个页面必记）

| 维度      | 记录内容                             |
| :-------- | :----------------------------------- |
| 导航菜单  | 顶部 Tab、侧边栏、Footer 链接        |
| 筛选器    | 输入框、下拉选择、日期范围、Tab 标签 |
| 列表/Feed | 列字段、分页、排序、空状态           |
| 交互操作  | 按钮、点赞/收藏/评论、展开/折叠      |
| 登录态    | 登录前/登录后行为差异、弹窗提示      |
| 外部链接  | 跳转第三方页面的链接                 |

## 输出格式

探索完成后，生成 JSON 文件 `page_structure.json`作为交付，结构示例：

```json
{
  "url": "https://example.com",
  "pages": [
    {
      "name": "首页",
      "path": "/",
      "elements": [
        { "type": "nav", "items": ["产品", "价格", "支持"] },
        { "type": "search_input", "locator": "#search" },
        { "type": "button", "text": "登录", "locator": ".login-btn" }
      ],
      "interactions": ["点击登录", "搜索关键词", "切换Tab"]
    }
  ],
  "login_required": true,
  "auth_methods_supported": ["cookie", "basic_auth"]
}
```

## 输出产物

执行完成后，输出目录结构如下，路径默认由主 skill 的 `交付物清单-输出目录` 决定

```
{OUTPUT_ROOT}/
├── analysis/				# 探索/分析结果目录（Step 1 产出）
│   └── page_structure.json
```

## 降级方案

当自动探索失败时（反爬、动态加载超时、元素无法定位）：

1. 提示用户手动补充页面模块描述（提供表格模板）
2. 要求用户提供关键页面截图（至少首页、核心功能页）
3. 若仍无法获取足够信息，终止流程并建议用户提供需求文档作为替代入口

## 注意事项

- 探索前先检查 `../websiteFeature/{domain}.md` 是否存在历史特征库，若有则优先参考
- 对于需要登录的站点，若用户提供了认证方式，自动注入登录步骤后再探索
- 探索过程应覆盖至少 3~5 个核心页面，不要求全量覆盖
- 若站点包含反爬（验证码、IP 封禁），暂停自动探索，提示用户进入手动模式