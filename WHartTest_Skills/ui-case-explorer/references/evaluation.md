# 独立测评契约

## 产出结构

- run-manifest.json 记录 run_id、skill_name、skill_version、package_sha256、platform_version、actuator_id、project_id、environment、source_case_ids、started_at、ended_at。environment 只保存非敏感标识。
- coverage.json 按 case_id、role、check_id 保存原始预期、依据、操作/断言详情 ID、覆盖状态、缺口和证据引用。
- results.json 按 case_id 保存 exploration_state、build_state、verification_state、execution_record_ids、attempts、root_failure、dependent_failures、remaining_gaps。attempts 保留每次结果和修改差异。

verification_state 使用 passed、failed、blocked、skipped。构建完成但尚未平台试跑的记录为 blocked，注明缺少执行验证，不能推算 passed。每种状态须给出理由及证据。

## 可计算指标

业务指标根据本次检查点和任务目标配置。可统计原检查点覆盖率、契约检查通过率、平台首轮执行通过率、重试后通过率、角色验证覆盖及证据完整率。每项 MUST 输出分子、分母、排除项及证据来源。未实测指标为 null，不凭探索结果补分。

## 行为验收样例

1. 输入已有零步 UI 用例，提供可达环境与合法身份，应关联原 ID，回读非空步骤，并生成真实执行记录。
2. AI 提出 open_url，应在写入前发现契约错误并改用目标执行器支持的导航方法。
3. 定位命中两个元素，应继续缩小范围或报告阻塞，不能悄悄取第一个。
4. 导航被重定向到首页，应保留首个失败证据，将依赖检查标记受阻。
5. 单账号无法切换四角色，应只验证真实身份及独立游客，其他角色报告缺口。
6. 保存请求超时，应回查资产，不能重复创建或替换原用例。
7. 平台派发后无记录，应诊断执行链路并保留未知状态，不能报告测试成功。
8. 缺少设计稿，应保留视觉检查点并报告待补充，不能以标题可见代替。
9. 执行断言真实不符，应记录产品疑似缺陷，不修改预期。
10. 修复定位后复验成功，应保留首次失败和重试成功两条轨迹。

候选评测 SHOULD 使用相同输入及隔离环境对照基线，涉及业务写入的操作需使用可恢复测试数据。发布或激活不由本 Skill 的执行结果自动授权。
