from django.test import SimpleTestCase

from .code_review_benchmark import _matches, _rates, _bucket


class CodeReviewBenchmarkMetricsTest(SimpleTestCase):
    def test_candidate_match_is_not_a_verified_score(self):
        bucket = _bucket()
        bucket.update(cases=1, completed=1, gold_issues=1, reported_findings=1, candidate_matches=1)
        metrics = _rates(bucket)
        self.assertEqual(metrics["candidate_recall"], 1.0)
        self.assertIsNone(metrics["verified_recall"])
        self.assertIsNone(metrics["verified_precision"])

    def test_verified_clean_pr_false_positive_rate(self):
        bucket = _bucket()
        bucket.update(cases=2, completed=2, verified_cases=2, clean_cases=2,
                      reported_findings=1, verified_false_findings=1,
                      verified_clean_false_cases=1)
        metrics = _rates(bucket)
        self.assertEqual(metrics["clean_pr_false_positive_rate"], 0.5)
        self.assertEqual(metrics["verified_precision"], 0.0)

    def test_file_mismatch_rejects_candidate(self):
        finding = {"file": "src/other.py", "line": 12, "change": "invalid token"}
        issue = {"file": "src/auth.py", "line": 12, "description": "invalid token"}
        self.assertFalse(_matches(finding, issue))
