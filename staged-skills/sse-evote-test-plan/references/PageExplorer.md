# PageExplorer：页面结构探索（Step1-B）

## 功能定位

对目标 Web 系统进行自动化页面探索，提取页面模块清单、可交互元素、跳转关系，为测试方案与用例生成提供结构化输入。
**适用对象**：新版网站 PC 端 / H5 端、机构登录页、各后台系统（采编、业务系统、统计系统、网站后台）。**不适用**：小程序（需真机 / 微信开发者工具）、现场服务工具（桌面端）、基础服务（无界面）。

## 前置条件

- 目标 URL 可访问（测试环境域名见 `references/投票业务规则库.md` 第 9 节）。
- 如需登录，已准备认证方式（账号密码 / Cookies / Token）。机构端登录入口带 `number` 参数（见规则库 6.4）。
- **若目标站点已有特征库**（`websiteFeature/{domain}.md`），先读取，复用其中的元素定位与等待策略。

## 工具选择策略

| 序号 | 工具 | 适用场景 | 局限 |
| --- | --- | --- | --- |
| 1 | `WebFetch` | 纯静态 HTML、快速取内容 | 拿不到 JS 渲染内容 |
| 2 | 浏览器自动化工具（`agent-browser` 等） | 需要交互的探索 | 复杂反爬、需人工干预 |
| 3 | **Selenium 脚本（推荐主力）** | 可控性强、超时可配、可存 DOM/截图/JSON | 需处理进程超时 |

### 推荐方案：`templates/page_explorer.py`

```
python3 templates/page_explorer.py \
    --url https://vote.test.sseinfo.com \
    --output {OUTPUT_ROOT}/analysis \
    --paths '["/o/home","/o/margin/meetingListByTime"]' \
    --headless
```

优势：超时可精确控制、headless 可切换、可落盘完整 DOM/截图/JSON、页面加载后有稳定等待。

> ⚠️ **执行方式**：脚本必须**后台执行**并给足超时（`background=true, timeout=300`），否则进程树（含浏览器）会被外层超时杀掉。
> 脚本内部的多层超时防护：`set_page_load_timeout(20)` + `implicitly_wait(3)` + `WebDriverWait` 用于不稳定元素。

## 探索清单（每个页面必记）

| 维度 | 记录内容 |
| --- | --- |
| 导航菜单 | 顶部 Tab、侧边栏、Footer 链接、右上角（登录/回到旧版） |
| 筛选器 | 输入框、下拉、**日期控件**（本平台重点：中文化、日期/月/年切换、返回当天）、Tab |
| 列表/Feed | 列字段、分页、排序、空状态、当日投票/预投票区域 |
| 交互操作 | 按钮、查看更多、展开折叠、公告查看、投票入口 |
| 弹窗/抽屉 | 弹窗组成、按钮置灰条件、复选框联动 |
| 登录态 | 未登录 / 已登录的行为差异（会议数据展示、跳转） |
| 跳转与地址 | 每个入口的**完整目标 URL**（含 query），用于路径切换类验证 |
| 外部/嵌入 | 跳第三方页面的链接、被 APP 嵌入的 H5 页面（返回按钮显隐） |

## 输出格式

产出 `analysis/page_structure.json`：

```json
{
  "url": "https://vote.test.sseinfo.com",
  "pages": [
    {
      "name": "投票首页",
      "path": "/o/home",
      "elements": [
        { "type": "nav", "items": ["首页", "会议列表", "使用帮助"] },
        { "type": "date_picker", "locator": ".date-wrapper", "note": "需验证中文化" },
        { "type": "button", "text": "更多", "locator": "#more-today" }
      ],
      "links": [ { "text": "融资融券代征集", "href": "https://vote.test.sseinfo.com/o/margin/meetingListByTime" } ],
      "interactions": ["切换日期", "点击更多"]
    }
  ],
  "login_required": false,
  "auth_methods_supported": ["org_login_number_param", "personal_login"],
  "notes": ["SPA 路由切换不触发 load 事件，需 page_load_strategy='none'"]
}
```

## 输出产物

```
{OUTPUT_ROOT}/
└── analysis/
    ├── page_structure.json
    ├── explore_results_{timestamp}.json
    └── screenshots/  (探索截图)
```

## 特征库回填（探索完成后必做）

将探索结论写回 `websiteFeature/vote.sseinfo.com.md`，至少记录：

- **技术栈**（框架 / UI 组件库）；
- **页面特征**（常见组件、特殊行为）；
- **特殊处理要求**（是否必须 `page_load_strategy = "none"`、是否需要滚动+JS 轮询等）；
- **探索注意事项**（易超时页面、动态生成元素、已知无法自动化的部分）；
- **已验证策略**（最佳截图等待组合、超时配置）。

## 降级方案

自动探索失败时（反爬、动态加载超时、元素定位失败）：

1. 提示用户手动补充页面模块描述（给出本文件"探索清单"表格模板）；
2. 要求提供关键页面截图（至少首页、会议列表、会议详情、股权处理页）；
3. 若仍不足，**不要终止流程** —— 本平台的核心验证对象是业务规则与数据链路，可仅依赖 Step1-A 需求分析继续推进，并在产出中标注"UI 层未探索"。

## 注意事项

- 探索前先查 `websiteFeature/{domain}.md` 是否有历史特征库。
- 需登录的站点，若用户提供认证方式，自动注入登录后再探索。
- 覆盖 3–5 个核心页面即可，不要求全量。
- 站点含验证码 / IP 封禁时暂停自动探索，转人工模式。
