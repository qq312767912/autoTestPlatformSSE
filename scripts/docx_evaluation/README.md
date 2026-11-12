# 一本化 DOCX 组件验证

全程本地执行，不调用模型，不修改业务原文件。当前为选型验证工具，尚未接入上传页面。

```sh
python3 -m venv /tmp/whart-docx-eval-env
/tmp/whart-docx-eval-env/bin/pip install docx-trackdiff==1.0.0 docx-revisions==0.1.5 psutil==7.2.2
/tmp/whart-docx-eval-env/bin/python scripts/docx_evaluation/evaluate.py --out /tmp/whart-docx-evaluation --paragraphs 100 10000
```

输出 results.json：修订及手工删除线语义、对比耗时、采样峰值 RSS（包括子进程）、进程退出码、上游校验结果。内存按 50ms 采样，可能漏过短峰值；合成段落数量不是 Word 页数。

合成数据包含中文、数字单位、正文及表格变更。上游校验通过不代表业务准确性已通过，也不代表支持样式继承、合并单元格、嵌套表格、图片或麒麟环境；这些需要额外样本验证。

docx-revisions 的 accepted_text 按 Word 修订语义处理，手工删除线是否被排除必须独立检查。两个库都不能自动决定哪些历史修订属于本次迭代。

## 已实现功能的测试

与上面的开源组件初测分开运行：

```sh
/tmp/whart-docx-eval-env/bin/pip install Django==5.2 djangorestframework==3.16.0 python-docx==1.1.2 Pillow psutil
/tmp/whart-docx-eval-env/bin/python scripts/docx_evaluation/run_api_tests.py
/tmp/whart-docx-eval-env/bin/python scripts/docx_evaluation/benchmark_comparison.py
```

API测试使用内存SQLite和临时媒体目录，不会连接业务数据库；同时校验迁移与模型一致。性能程序使用现有功能的解析与导出代码，默认写出 `/tmp/whart-shipped-comparison-benchmark.json`，并断言预期的两处修改均被识别。

## 上传控件组件测试（不需要浏览器扩展）

```sh
npm install --prefix /tmp/whart-upload-unit jsdom@29.1.1
node scripts/docx_evaluation/test_upload_component.cjs
```

在隔离DOM中挂载真实Vue组件与Arco上传组件，模拟文件选择与拖放事件。覆盖双文件状态、重新选择、大写扩展名、无效格式及开始按钮状态，不访问用户文件、不发送网络请求，也不验证系统文件选择窗口是否正常弹出。验证环境Node 25.9.0。
