"""为 webtest 四阶段 Skill 创建可重复使用的五分区契约评测套件。

这里只建设评测输入和人工可复核的期望，不创建 EvaluationRun，也不伪造分数。
"""
import json

from django.contrib.auth import get_user_model
from django.utils import timezone

from knowledge_evolution.models import EvaluationCase, EvaluationSuite
from projects.models import Project


PROJECT_ID = 7
ANNOTATOR = "webtest-staged-skill-contract-v1"
SPLITS = ("gold", "regression", "fresh", "challenge", "hidden")

STAGES = {
    "test_plan_generation": {
        "name": "WebTest 测试方案生成契约集 v1",
        "actions": ["生成测试方案", "输出覆盖率", "记录未覆盖项"],
        "must": ["workflow_id", "test_plan_generation", "coverage", "plan_items", "open_questions"],
        "inputs": [
            {"website_url": "https://example.test", "requirements": "用户登录与退出"},
            {"website_url": "https://example.test/search", "page_structure": {"elements": ["搜索框", "搜索按钮"]}},
            {"website_url": "https://example.test/profile", "requirements": "头像上传，格式和大小限制待确认"},
            {"website_url": "https://example.test/order", "requirements": "跨页面下单，包含登录态、库存和支付失败"},
            {"website_url": "https://example.test/admin", "requirements": "角色权限未知，不得编造规则"},
        ],
    },
    "testcase_generation": {
        "name": "WebTest 测试用例生成契约集 v1",
        "actions": ["生成测试用例", "建立方案追溯", "标记执行方式"],
        "must": ["workflow_id", "testcase_generation", "parent_output_ids", "coverage", "test_cases"],
        "inputs": [
            {"plan_item": {"id": "PLAN-LOGIN-001", "scenario": "正向登录", "priority": "P0"}},
            {"plan_item": {"id": "PLAN-SEARCH-002", "scenario": "空关键词", "priority": "P1"}},
            {"plan_item": {"id": "PLAN-UPLOAD-003", "scenario": "文件大小边界", "priority": "P1"}},
            {"plan_item": {"id": "PLAN-ORDER-004", "scenario": "支付超时并重试", "priority": "P0"}},
            {"plan_item": {"id": "PLAN-EXTERNAL-005", "scenario": "第三方跳转需人工确认", "priority": "P2"}},
        ],
    },
    "test_execution": {
        "name": "WebTest 测试执行契约集 v1",
        "actions": ["执行测试", "采集证据", "保留失败轨迹"],
        "must": ["workflow_id", "test_execution", "parent_output_ids", "results", "failure_traces"],
        "inputs": [
            {"case": {"id": "TC-LOGIN-001", "mode": "automated", "steps": ["打开登录页", "提交合法账号"]}},
            {"case": {"id": "TC-SEARCH-002", "mode": "automated", "steps": ["搜索空字符串"]}},
            {"case": {"id": "TC-UPLOAD-003", "mode": "automated", "steps": ["上传超限文件"]}},
            {"case": {"id": "TC-RETRY-004", "mode": "automated", "steps": ["制造首次失败", "重试"]}},
            {"case": {"id": "TC-MANUAL-005", "mode": "manual", "steps": ["核对第三方监管文案"]}},
        ],
    },
    "report_generation": {
        "name": "WebTest 报告生成契约集 v1",
        "actions": ["生成测试报告", "引用上游产出", "列出未闭环问题"],
        "must": ["workflow_id", "report_generation", "coverage", "pass_rate", "failure_distribution", "unclosed_issues"],
        "inputs": [
            {"summary": {"total": 10, "passed": 10, "failed": 0, "blocked": 0}},
            {"summary": {"total": 10, "passed": 8, "failed": 2, "blocked": 0}},
            {"summary": {"total": 5, "passed": 3, "failed": 0, "blocked": 2}},
            {"summary": {"total": 4, "passed": 1, "failed": 2, "blocked": 1}, "retries": 2},
            {"summary": {"total": 0, "passed": 0, "failed": 0, "blocked": 0}, "upstream_refs": []},
        ],
    },
}


project = Project.objects.get(pk=PROJECT_ID)
actor = get_user_model().objects.get(username="admin")
result = []

for task_type, definition in STAGES.items():
    suite, _ = EvaluationSuite.objects.update_or_create(
        project=project,
        name=definition["name"],
        suite_type="seed",
        defaults={
            "task_type": task_type,
            "description": "四阶段 Skill 的结构化契约评测集；五分区齐全，但未经过业务专家确认前不冒充生产金标。",
            "split_ratio": {split: 0.2 for split in SPLITS},
            "is_active": True,
            "created_by": actor,
        },
    )
    for index, (split, payload) in enumerate(zip(SPLITS, definition["inputs"]), start=1):
        EvaluationCase.objects.update_or_create(
            suite=suite,
            case_number=index,
            defaults={
                "task_type": task_type,
                "split": split,
                "input_payload": {
                    "workflow_id": f"eval-{task_type}-{split}",
                    "task_type": task_type,
                    **payload,
                },
                "expected_payload": {
                    "expected_output": " ".join(definition["must"]),
                },
                "golden_labels": {
                    "must_contain": definition["must"],
                    "must_not_contain": ["明文密码", "绕过门禁", "伪造通过"],
                    "expected_actions": definition["actions"],
                },
                "annotator": ANNOTATOR,
                "annotated_at": timezone.now(),
                "metadata": {
                    "asset_level": "contract_seed",
                    "requires_business_confirmation": True,
                    "skill_stage": task_type,
                },
            },
        )
    result.append({
        "suite_id": str(suite.id),
        "name": suite.name,
        "task_type": task_type,
        "case_count": suite.cases.count(),
        "splits": list(suite.cases.order_by("case_number").values_list("split", flat=True)),
    })

print(json.dumps(result, ensure_ascii=False, indent=2))
