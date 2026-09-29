"""任务 6：规则提取器回归测试。"""

from django.contrib.auth.models import User
from django.test import TestCase

from projects.models import Project

from .extractors import CandidateDeduplicator, RuleBasedExtractor
from .models import KnowledgeCandidate, SourceSnapshot


class ExtractorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="extractor-tester", password="pass")
        self.project = Project.objects.create(name="Extractor Project", creator=self.user)
        self.snapshot = SourceSnapshot.objects.create(
            project=self.project,
            source_type="document",
            source_id="doc-1",
            revision="r1",
            content_hash="h1",
            parsed_text="""
# 必须遵守的规范

所有服务必须记录日志。

## 建议做法

建议每天备份数据。

| 环境 | 状态 |
|------|------|
| 生产 | 生效 |

```python
def backup():
    pass
```
""",
        )

    def test_heading_becomes_rule_or_concept(self):
        extractor = RuleBasedExtractor()
        candidates = list(extractor.extract(self.snapshot))
        kinds = {c.kind for c in candidates}
        self.assertIn("rule", kinds)
        self.assertIn("concept", kinds)
        title_levels = {(c.payload.get("title"), c.level) for c in candidates if c.kind == "rule"}
        self.assertIn(("必须遵守的规范", "L1"), title_levels)

    def test_code_block_becomes_procedure(self):
        extractor = RuleBasedExtractor()
        candidates = list(extractor.extract(self.snapshot))
        procedures = [c for c in candidates if c.kind == "procedure"]
        self.assertEqual(len(procedures), 1)
        self.assertIn("backup", procedures[0].payload.get("content", ""))

    def test_table_becomes_fact(self):
        extractor = RuleBasedExtractor()
        candidates = list(extractor.extract(self.snapshot))
        facts = [c for c in candidates if c.kind == "fact"]
        self.assertTrue(len(facts) >= 1)
        self.assertTrue(
            any("生产" in c.payload.get("content", "") for c in facts),
            f"表格内容应包含 '生产'，实际 facts={facts}",
        )

    def test_deduplication_removes_duplicates(self):
        extractor = RuleBasedExtractor()
        candidates = list(extractor.extract(self.snapshot))
        dedup = CandidateDeduplicator(project=self.project)
        unique, conflicts = dedup.dedup_and_conflicts(candidates)
        self.assertLessEqual(len(unique), len(candidates))
        self.assertEqual(len(conflicts), 0)

    def test_save_candidates_is_idempotent(self):
        extractor = RuleBasedExtractor()
        candidates = list(extractor.extract(self.snapshot))
        dedup = CandidateDeduplicator(project=self.project, actor=self.user)
        unique, _ = dedup.dedup_and_conflicts(candidates)
        saved1 = dedup.save_candidates(self.snapshot, unique)
        saved2 = dedup.save_candidates(self.snapshot, unique)
        # 同一内容第二次保存应返回已存在记录，不新增
        self.assertEqual(
            KnowledgeCandidate.objects.filter(project=self.project).count(),
            len(saved1),
        )
        self.assertEqual(saved1[0].pk, saved2[0].pk)
