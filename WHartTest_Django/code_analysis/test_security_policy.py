from django.test import SimpleTestCase
from .security_policy import ARTICLES, policy_references
from .services import _apply_verification_results, _classify_finding

class SecurityPolicyTests(SimpleTestCase):
    def check(self, article=8, requirement=None):
        return {'article':article,'requirement':requirement or '不在代码中硬编码敏感信息，包括但不限于密码、密钥、API 令牌、数据库连接串、私钥、证书、短信模板密钥等。','applicable':True,'violation':True,'evidence':'api_key = "actual-secret"'}

    def test_catalog_preserves_all_articles(self):
        self.assertEqual(len(ARTICLES),28)
        self.assertIn('不避免',ARTICLES[10]['content'])

    def test_citation_is_resolved_from_original(self):
        r=policy_references([self.check()])[0]
        self.assertEqual(r['article'],'第八条')
        self.assertEqual(r['content'],ARTICLES[8]['content'])
        self.assertTrue(r['mandatory'])

    def test_unknown_article_or_fabricated_quote_is_ignored(self):
        self.assertEqual(policy_references([self.check(99), self.check(requirement='禁止一切操作。')]),[])

    def test_recommendation_is_not_mandatory(self):
        r=policy_references([self.check(9,'敏感数据传输宜使用 TLS 1.2 及以上版本。')])[0]
        self.assertFalse(r['mandatory'])

    def test_partial_quote_cannot_turn_recommendation_into_requirement(self):
        r=policy_references([self.check(20,'原则上不使用超级管理员账号。')])[0]
        self.assertFalse(r['mandatory'])

    def test_proven_policy_violation_is_confirmed_without_claiming_runtime_failure(self):
        finding={'key':'policy','disposition':'advisory','policy_checks':[self.check()],
                 'verification_trace':{'source_status':'read','missing_steps':[]}}
        verdict={'key':'policy','verdict':'advisory','reason':'目标代码硬编码密钥，违反明确禁令',
                 'supporting_evidence':['目标源码包含真实密钥赋值'],'policy_checks':[self.check()]}
        active,rejected=_apply_verification_results([finding],[verdict])
        self.assertFalse(rejected)
        self.assertEqual(active[0]['disposition'],'confirmed')
        self.assertEqual(active[0]['confirmation_basis'],'mandatory_policy_violation')

    def test_failed_verification_does_not_confirm_policy_candidate(self):
        finding=_classify_finding({'key':'policy','disposition':'advisory','policy_checks':[self.check()]})
        active,_=_apply_verification_results([finding],[])
        self.assertEqual(active[0]['disposition'],'needs_confirmation')
        self.assertEqual(active[0]['policy_references'][0]['article'],'第八条')
