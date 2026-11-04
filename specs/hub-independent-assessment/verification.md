# 验证 · 2026-11-04

## 后端

在现有容器中加载 workspace 源码，使用独立 PostgreSQL 测试库和临时日志：

```sh
python manage.py test knowledge_evolution.test_hub_assessment knowledge_evolution.test_skill_assessment knowledge_evolution.test_paired_evolution knowledge_evolution.tests_evolution_execution_v2 knowledge_evolution.tests_t09_t13.DerivationTests --keepdb --noinput --verbosity 0
python manage.py makemigrations knowledge_evolution --check --dry-run
```

56 项回归通过，覆盖独立结构测评、八项77分与实测N/A、纯文本真实模型调用路径（测试替身）、工具依赖拒绝模拟、外部产出每候选独立提交与哈希绑定、相同产出不同引用不算提升、Hub 原包保持不变、私有多轮候选与 diff、路径白名单、源码篡改、完整测评/成对验证/人工确认后的新增与审批、跨项目原材料可选而会话不可越权、队列重复派发和租约、任务启动失败落库、轻微收益早停、执行人员不能提交审批，以及已有两类测评与进化回归。

结构体检和业务评测均没有用模拟结果填满实测维度。所有模型调用在测试中使用替身，未消费真实模型额度。迁移一致性检查通过。

## 前端

Vue 类型检查和 Vite 生产构建通过。Playwright 加载实际组件及 Arco/Pinia，拦截测试 API，完成精确版本选择、新建/后台轮询、结构结果、生成私有候选、重测、填写理由、人工选择、无实测禁止提交的完整流程；无页面运行时异常。桌面截图检查通过，测试图片中的分数是夹具数据。

## 使用与边界

- Hub 卡片“通用测评”和版本历史内同名按钮均进入独立工作台，不要求用例审查记录或业务指标。
- 默认结构体检，实测表现未执行、百分制总分为空；文本实测只处理无脚本/工具依赖的文本任务。其他 Skill 可使用结构体检或人工确认的真实产出复核。
- 后台任务可关闭窗口后从历史重新打开，模型与候选生成共享会话预算，候选最多三轮。
- 候选留在项目私有目录。完整实测和成对验证通过、人工选择理由与 diff 确认后，由项目负责人提交新增回 Hub 既有验证审批，不自动激活。
- 部署需执行 0050 迁移，并更新 Django、Celery 和前端。
