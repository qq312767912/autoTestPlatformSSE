### roadshow.sseinfo.com

#### **技术栈**  

- 框架： Vue.js
- UI 组件库：Element UI  

#### **页面特征**  

- 常见组件：大量 `el-dialog`、`el-drawer` 组件；图片采用 Intersection Observer 懒加载；SPA 路由不触发 `load` 事件
- 特殊行为：如SPA 路由切换不刷新页面、动态加载内容、弹窗层级等  

#### **特殊处理要求**  

- **必须**设置 `page_load_strategy = "none"`

- **必须**使用 滚动触发懒加载 + JS 轮询 稳定截图 

  ```
  模板结构：
  ├── 配置区          # TARGET_URL / HEADLESS / 输出路径
  ├── init_driver()  # page_load_strategy='none' 初始化
  ├── stable_navigate()  # Strategy 4 完整流程
  ├── _scroll_trigger_lazy_images()  # 滚动触发懒加载
  ├── _wait_all_images_loaded()  # JS 轮询等待图片
  ├── register() / record()  # 用例注册
  └── main()  # 执行入口
  ```

  ```python
  # 关键配置（Vue SPA）
  
  # 必须在 init_driver() 中设置
  opts.page_load_strategy = "none"   # ← Vue SPA 关键，不等 load 事件
  
  # stable_navigate() 流程
  driver.get(url)                    # 立即返回（不等 load）
  → wait DOM ready                  # 等 document.readyState=='complete'
  → sleep(2)                        # 等 Vue 异步渲染
  → _scroll_trigger_lazy_images()    # 分段滚动触发 Observer
  → _wait_all_images_loaded()       # JS 轮询，最多 15s
  → sleep(0.5)                      # 最终稳定
  → driver.save_screenshot()        # 截图
  ```


#### **探索注意事项**  

- 首页始终有约 38/86 张图片无法加载（CDN 超时或不存在），不视为失败
- 需要分段滚动触发 Observer（步长 = 视口高度/2）
- 图片等待超时建议 15 秒，连续 3 次剩余数量不变则提前退出 

#### **已验证策略**（可选）  

- 无