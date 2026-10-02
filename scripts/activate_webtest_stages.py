"""把四阶段存量包从 shadow 激活为 active。

为什么必须做这一步
------------------
`staged-skills/` 里的四个 webtest-* 包**已经入库**、`manifest.stage` 声明也正确，
但对应的 `CapabilityRelease` 全部停在 `shadow`。而 `SkillRuntimeResolver.resolve_version`
只认 `release.state == 'active'`，于是出现两种都不想要的结果：

- **登记了这些包的项目**（如 `test`）：`bind_stage` 会走「登记了却没有可用活跃版本」
  的**拒绝**分支，四阶段流水线**根本发起不了**；
- **未登记这些包的项目**（如 `演示项目`）：虽然按 `allow_unmanaged` 放行，
  但四个阶段全部落进 `unmanaged_stages`，**没有任何版本溯源**。

两种结果都拿不到"这条链路的产出对应哪个 Skill 版本"，也就谈不上按版本回滚。

关于 min_observations=0
-----------------------
平台默认要求先积累 3 个灰度观察窗口才允许正式激活（`complete_canary`）。
存量包首次接入时平台上还没有任何观察数据，所以这里显式放宽为 0。

这是一次**有意的治理放宽**，不是默认路径：它让"能不能用"先于"灰度证据"。
回退方式：`CapabilityReleaseService.rollback(release, actor=..., reason=...)`，
或把 `state` 直接改回 `shadow`。

用法
----
    docker exec -i wharttest-new-backend \
      python manage.py shell --settings=wharttest_django.settings \
      < scripts/activate_webtest_stages.py

幂等：已经是 active 的会被跳过。
"""
from knowledge_evolution.capabilities import CapabilityReleaseService
from knowledge_evolution.capability_models import CapabilityRelease
from knowledge_evolution.operations import WORKFLOW_STAGE_ORDER
from projects.models import Project
from skills.models import SkillVersion
from skills.runtime import SkillRuntimeResolver

TARGETS = {
    "test_plan_generation": "webtest-plan-generator",
    "testcase_generation": "webtest-case-generator",
    "test_execution": "webtest-execution-runner",
    "report_generation": "webtest-report-generator",
}
REASON = "四阶段全链路联调：存量包首次接入，平台尚无灰度观察窗口"

print("=== 激活 ===")
for stage, name in TARGETS.items():
    release = (
        CapabilityRelease.objects
        .filter(name=name, kind="skill", state="shadow")
        .order_by("-created_at")
        .first()
    )
    if release is None:
        existing = CapabilityRelease.objects.filter(name=name, kind="skill").values_list("state", flat=True)
        print(f"[skip] {stage}: {name} 无 shadow 状态 release（现有状态：{list(existing)}）")
        continue
    # refresh_active_pointers 靠这条绑定找到 SkillVersion；没绑定就白激活。
    bound = SkillVersion.objects.filter(release=release).first()
    if bound is None:
        print(f"[warn] {stage}: {name} 的 release 未绑定 SkillVersion，激活也刷不到活跃指针")
    CapabilityReleaseService.complete_canary(
        release, actor=None, reason=REASON, min_observations=0,
    )
    release.refresh_from_db()
    skill_version = SkillVersion.objects.filter(release=release).first()
    active_ptr = getattr(getattr(skill_version, "skill", None), "active_version_id", None)
    print(
        f"[ok] {stage}: {name} v{release.version} -> {release.state} "
        f"(project={release.project_id}, 活跃指针={'已设置' if active_ptr else '未设置'})"
    )

print()
print("=== 解析验证（按阶段解析活跃版本）===")
for stage in WORKFLOW_STAGE_ORDER:
    hit = False
    for project in Project.objects.all().order_by("id"):
        version = SkillRuntimeResolver.resolve_version(project=project, stage=stage)
        if version is not None:
            print(f"  project={project.id:<2} {project.name:<24} {stage:<22} -> {version.skill.name} v{version.version}")
            hit = True
    if not hit:
        print(f"  （无项目可为 {stage} 解析到 active 版本）")
