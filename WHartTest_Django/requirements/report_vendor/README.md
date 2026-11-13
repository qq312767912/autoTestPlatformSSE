# 用户提供的报告渲染器

来源：docx-revision-report-1.0.4 (1).zip 中 scripts/extract_all_revisions.py 与 selftest_regression.py。

保留原来的报告标题、章节、样式、编号模拟、引用章节照录、目录/记录表裁剪与资源复制逻辑。平台通过 requirements/revision_report.py 将确认的双文档差异转换为真实修订标记后调用原脚本；不再自建另一套报告模板。

对原脚本的功能补丁：analyze 在构建标题树前调用 normalize_manual_deletions，支持直接/继承的 strike 和 dstrike，并尊重显式关闭；这些文字以删除修订参与原统计与渲染。已有删除修订不重复包装。该补丁不依赖平台，可直接运行脚本。

回归：python selftest_regression.py。额外手工删除线及平台重新上传用例见 requirements/test_effective_content.py。
