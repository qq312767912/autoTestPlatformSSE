# 验证记录 · 2026-11-04

## 后端

现有 backend 容器加载只读 workspace 源码，使用独立 PostgreSQL `test_darwin_validation` 和 /tmp 日志。

```sh
python manage.py test knowledge_evolution.test_skill_assessment knowledge_evolution.test_paired_evolution knowledge_evolution.tests_evolution_execution_v2 knowledge_evolution.tests_t09_t13.DerivationTests --keepdb --noinput --verbosity 1
python manage.py makemigrations knowledge_evolution --check --dry-run
```

41 项回归覆盖九维原权重、加权短板、必须真实产出、跨输入结构、非法模型评分、Sheet 唯一标签、百分数、公式无缓存、零值、多批去重、求和/均值/数量、聚合排除表头、不同 Skill 指标配置、配置冻结、指标方向/容差/目标、人工补评及操作人、业务退化阻断、两类结论与产出绑定、配置 API 项目隔离、既有 A/B 实验与真实包多轮派生。完整重跑测试复用了实际 Excel 报告渲染器，确认统计与产出格式一致。新增 0049 迁移在独立测试库执行，模型迁移一致性检查无变化。

## 前端

```sh
./node_modules/.bin/vue-tsc -b --pretty false
./node_modules/.bin/vite build --outDir /tmp/wharttest-dual-assessment-dist
```

类型检查与生产构建通过；既有 Browserslist 数据陈旧、stream 外置和大包提示不影响构建。

使用 Playwright 在临时 Vite 页面加载实际新组件与 Arco/Pinia，拦截 API 返回测试数据，验证：九维/两类结果展示、保存 Skill 配置、空评分拦截、三路人工评分及证据提交、表格更新，无页面运行时异常。完成桌面截图检查；该页面为测试夹具，截图数值不代表业务测评效果。

## 发布边界

本次没有调用真实模型，没有更新业务库或运行中前后端。上线需执行 `knowledge_evolution.0049_dual_skill_evaluation`（自动依赖前一轮的 0048）并同步发布前后端。

新实验冻结两类配置；历史实验不伪造补齐通用/业务测评，仍按原记录展示。业务指标至少指定一个必需项才可选优；观察项仍展示，但不替代必需项。通用没有擅自增加固定分数门槛，业务目标/容差由用户明确设置。通用总分不与业务指标合并。
