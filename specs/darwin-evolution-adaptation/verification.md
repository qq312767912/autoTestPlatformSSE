# 验证记录 · 2026-11-03

运行环境：现有 backend 容器；源码通过只读 workspace 挂载加载；数据库使用独立 `test_darwin_validation`，日志与临时包写入容器 /tmp。

## 后端

```sh
python manage.py test knowledge_evolution.test_paired_evolution knowledge_evolution.tests_evolution_execution_v2 knowledge_evolution.tests_t09_t13.DerivationTests --keepdb --noinput --verbosity 1
python manage.py makemigrations knowledge_evolution --check --dry-run
```

结果：26 项测试全部通过。

覆盖：新旧顺序交换、全平票、严格多数、弱收益早停、相同产出、评委失败、退化反证、预算不足、真实运行协议、上下文冻结、包篡改、输入/输出漂移、发布二次校验、多轮实际包派生、活跃版本不变、重复派生阻断、历史项目隔离与快照不外发。模型调用用测试替身；包与数据库流程真实执行。新增迁移已在独立测试库通过，模型无待生成迁移。

## 前端

```sh
./node_modules/.bin/vue-tsc -b --pretty false
./node_modules/.bin/vite build --outDir /tmp/wharttest-darwin-dist
```

类型检查和构建通过。既有 Browserslist 陈旧、stream 浏览器外置、静态/动态导入与大包提示仍存在，无新增构建错误。未使用真实模型完成端到端业务效果验收，未部署业务数据库或运行中前后端。
