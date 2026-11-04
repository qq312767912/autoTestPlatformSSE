"""Hub 独立测评：原件不变、缺实测不补分、私有候选、完整验证再提交。"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from knowledge_evolution import hub_assessment as service
from knowledge_evolution.hub_assessment_models import SkillAssessmentSession
from knowledge_evolution.hub_assessment_views import SkillAssessmentViewSet
from knowledge_evolution.paired_evaluation import EvaluationBudget
from knowledge_evolution.skill_assessment import GENERAL_RUBRIC, GeneralSkillEvaluator
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests
from skills.models import SkillVersion
from skills.packaging import compute_package_sha256


def judge_response(structure=False):
    return json.dumps({'dimensions': [{'id': item['id'], 'score': 8, 'reason': '对应已读文件与实际证据',
                                      'evidence': ['SKILL.md 第2节']} for item in GENERAL_RUBRIC
                                     if not structure or item['id'] != 'execution']})


def assessor(structure=False):
    return GeneralSkillEvaluator(Mock(return_value=SimpleNamespace(invoke=Mock(return_value=judge_response(structure)))))


class StructuralAssessmentTests(SimpleTestCase):
    def test_unexecuted_dimension_never_gets_fake_full_score(self):
        result = assessor(True).evaluate(package={'files': []}, output={}, source={}, config=None,
                                        budget=EvaluationBudget(None), structural_only=True)
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['total'])
        self.assertEqual(result['score_basis'], 77)
        self.assertEqual(result['assessed_score'], 61.6)
        execution = next(item for item in result['dimensions'] if item['id'] == 'execution')
        self.assertIsNone(execution['score'])
        self.assertEqual(execution['status'], 'not_run')

    def test_cases_require_expected_results_and_external_evidence(self):
        for mode, values in [('text', []), ('text', [{'prompt': 'task', 'expected': ''}]),
                             ('external', [{'prompt': 'task', 'expected': 'criteria', 'output': 'x'}])]:
            with self.assertRaises(ValidationError):
                service.normalize_cases(mode, values)


class HubAssessmentTests(SkillHubBaseTests):
    def create(self, mode='structure'):
        _, version = self.make_skill_version()
        session = service.create_session(project=self.project, version=version, actor=self.lead, mode=mode,
            cases=[] if mode == 'structure' else [{'prompt': 'input', 'expected': 'expected task result', 'output': 'baseline output', 'evidence_ref': 'job:baseline:1'}], confirmed=True)
        return session, version

    def assess(self, session, candidate_id='', **kwargs):
        with patch.object(service, 'llm_config', return_value=SimpleNamespace(name='test')):
            return service.assess(session.pk, candidate_id=candidate_id, evaluator=assessor(session.mode=='structure'), actor=self.lead, **kwargs)

    def generate(self, session, content='\n输入不足时列出缺失信息，禁止补造业务规则。\n'):
        parent = service.selected_item(session, session.winner_id)
        original = (Path(service.package(session, parent).get_full_path()) / 'SKILL.md').read_text()
        factory = Mock(return_value=SimpleNamespace(invoke=Mock(return_value=json.dumps({
            'edits': [{'path': 'SKILL.md', 'content': original + content}], 'rationale': '修复测评中的失败分支短板'}))))
        with patch.object(service, 'llm_config', return_value=SimpleNamespace(name='test')):
            return service.generate(session.pk, factory=factory)

    def test_standalone_static_assessment_needs_no_review_or_business_metrics(self):
        session, version = self.create()
        count = SkillVersion.objects.count()
        session = self.assess(session)
        session = self.generate(session)
        candidate = session.candidates[0]
        session = self.assess(session, candidate['id'])
        self.assertEqual(session.baseline['general']['status'], 'partial')
        self.assertEqual(SkillVersion.objects.count(), count)
        self.assertEqual(compute_package_sha256(version.get_full_path(), redact_secrets=True), version.package_sha256)
        source_path = Path(version.get_full_path())
        self.assertNotEqual(source_path, service.session_root(session) / candidate['id'])
        with self.assertRaises(ValidationError):
            service.submit_candidate(session.pk, actor=self.lead, confirmed=True)

    def test_structural_candidate_can_be_refined_without_claiming_verified_gain(self):
        session, _ = self.create()
        session = self.assess(session);session = self.generate(session)
        session = self.assess(session, session.candidates[0]['id'])
        session = service.select_candidate(session.pk, candidate_id=session.candidates[0]['id'], reason='已检查结构 diff，继续加工')
        parent = session.winner_id
        session = self.generate(session, content='\n处理失败时展示原因和兜底步骤。\n')
        self.assertEqual(session.candidates[1]['parent_id'], parent)
        self.assertNotIn('hub_version_id', session.candidates[0])

    def test_external_candidates_need_their_own_outputs_and_exact_hash(self):
        session, _ = self.create('external')
        session = self.assess(session);session = self.generate(session)
        candidate = session.candidates[0]
        with self.assertRaisesMessage(ValidationError, '匹配的候选指纹'):
            self.assess(session, candidate['id'], evidence=[{'id': '1', 'output': 'better', 'evidence_ref': 'job:new'}], confirmed=True, version_sha256='wrong')
        session.refresh_from_db()
        jury = SimpleNamespace(compare=Mock(return_value={'eligible': True, 'clear_gain': True, 'reason': '真实产出改善', 'votes': []}))
        session = self.assess(session, candidate['id'], evidence=[{'id': '1', 'output': 'better complete output', 'evidence_ref': 'job:candidate'}],
                              confirmed=True, version_sha256=candidate['sha256'], jury=jury)
        self.assertTrue(session.candidates[0]['comparison']['eligible'])
        self.assertEqual(session.candidates[0]['attestation']['actor_id'], str(self.lead.pk))

    def test_declared_tool_skill_cannot_fake_text_execution(self):
        session, _ = self.create('text')
        with self.assertRaisesMessage(ValidationError, '不模拟工具执行'):
            self.assess(session, factory=Mock())
        session.refresh_from_db()
        self.assertEqual(session.state, 'failed')
        self.assertNotIn('general', session.baseline)

    def test_publish_requires_full_assessment_pairwise_validation_and_manual_confirmation(self):
        session, original = self.create('external')
        session = self.assess(session);session = self.generate(session)
        item = session.candidates[0]
        session = self.assess(session, item['id'], confirmed=True, version_sha256=item['sha256'],
            evidence=[{'id': '1', 'output': 'better complete output', 'evidence_ref': 'candidate-job:1'}],
            jury=SimpleNamespace(compare=Mock(return_value={'eligible': True, 'clear_gain': True, 'reason': 'better'})))
        session = service.select_candidate(session.pk, candidate_id=item['id'], reason='核对实际产出和 diff 后选择')
        with self.assertRaisesMessage(ValidationError, '确认 diff'):
            service.submit_candidate(session.pk, actor=self.lead)
        result = service.submit_candidate(session.pk, actor=self.lead, confirmed=True)
        self.assertEqual(result.state, 'submitted')
        candidate = SkillVersion.objects.get(pk=result.candidates[0]['hub_version_id'])
        self.assertNotEqual(candidate.pk, original.pk)
        self.assertNotEqual(candidate.state, 'active')
        self.assertEqual(compute_package_sha256(original.get_full_path(), redact_secrets=True), original.package_sha256)
        with self.assertRaises(ValidationError):
            service.submit_candidate(session.pk, actor=self.lead, confirmed=True)

    def test_private_package_tampering_blocks_assessment_and_publication(self):
        session, _ = self.create()
        path = service.session_root(session) / 'baseline' / 'SKILL.md'
        path.write_text(path.read_text() + '\nchanged')
        with self.assertRaisesMessage(ValidationError, '副本已变化'):
            self.assess(session)

    def test_candidate_patch_whitelist_rejects_path_escape(self):
        session, _ = self.create();session = self.assess(session)
        factory = Mock(return_value=SimpleNamespace(invoke=Mock(return_value=json.dumps({'edits': [{'path': '../outside.md', 'content': 'bad'}]}))))
        with patch.object(service, 'llm_config', return_value=SimpleNamespace(name='test')):
            with self.assertRaises(ValidationError):
                service.generate(session.pk, factory=factory)
        session.refresh_from_db()
        self.assertEqual(session.candidates, [])

    def test_project_scope_history_and_async_duplicate_dispatch(self):
        session, _ = self.create()
        factory = APIRequestFactory()
        view = SkillAssessmentViewSet.as_view({'get': 'retrieve', 'post': 'assess'})
        request = factory.get('/sessions/', {'project': self.other_project.pk})
        force_authenticate(request, self.lead)
        self.assertEqual(view(request, pk=session.pk).status_code, 404)
        with patch('knowledge_evolution.tasks.hub_assessment_job.apply_async') as dispatch:
            request = factory.post('/assess/', {'project': self.project.pk}, format='json');force_authenticate(request, self.lead)
            response = view(request, pk=session.pk)
            self.assertEqual(response.status_code, 202)
            request = factory.post('/assess/', {'project': self.project.pk}, format='json');force_authenticate(request, self.lead)
            response = view(request, pk=session.pk)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(dispatch.call_count, 1)
        session.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, '租约'):
            service.assess(session.pk, job_id='other')
        self.assertEqual(SkillAssessmentSession.objects.get(pk=session.pk).state, 'queued')

    def test_cross_project_hub_source_is_copied_into_current_project(self):
        _, version = self.make_skill_version(name='public-material', project=self.other_project)
        session = service.create_session(project=self.project, version=version, actor=self.lead, mode='structure', cases=[])
        self.assertEqual(session.project_id, self.project.pk)
        self.assertEqual(session.source_version_id, version.pk)
        self.assertEqual(service.session_root(session).parts[-2], str(self.project.pk))

    def test_pure_text_skill_executes_real_model_call_and_gets_nine_dimensions(self):
        import tempfile
        from skills.versions import SkillVersionService
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'SKILL.md').write_text('---\nname: text-assessment\ndescription: 将输入改写为清晰文本\nversion: 1.0.0\nstage: case_review\n---\n# 文本改写\n保留事实并简化表述。\n')
            _, version = SkillVersionService.create_candidate_from_dir(source_dir=root, project=self.project, actor=self.lead)
        session = service.create_session(project=self.project, version=version, actor=self.lead, mode='text',
                                         cases=[{'prompt': '简化这段文字', 'expected': '保留事实、输出简短文本'}])
        factory = Mock(return_value=SimpleNamespace(invoke=Mock(return_value=json.dumps({'status': 'completed', 'output': '实际模型生成的简短文本'}))))
        session = self.assess(session, factory=factory)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(session.baseline['output']['execution_mode'], 'llm_text_only')
        self.assertEqual(session.baseline['general']['status'], 'completed')
        self.assertEqual(len(session.baseline['general']['dimensions']), 9)

    def test_changed_external_reference_alone_does_not_count_as_improvement(self):
        session, _ = self.create('external');session = self.assess(session);session = self.generate(session)
        candidate = session.candidates[0]
        session = self.assess(session, candidate['id'], confirmed=True, version_sha256=candidate['sha256'],
                              evidence=[{'id': '1', 'output': 'baseline output', 'evidence_ref': 'different-job'}])
        self.assertFalse(session.candidates[0]['comparison']['eligible'])

    def test_worker_failure_before_lease_is_marked_failed(self):
        from knowledge_evolution.tasks import hub_assessment_job
        session, _ = self.create()
        session.state, session.job_id = 'queued', 'worker-job'
        session.save()
        with patch.object(service, 'assess', side_effect=ValidationError('source unavailable')):
            with self.assertRaises(ValidationError):
                hub_assessment_job.run(str(session.pk), 'assess', {}, self.lead.pk, 'worker-job')
        session.refresh_from_db()
        self.assertEqual(session.state, 'failed')
        self.assertEqual(session.job_id, '')

    def test_slight_gain_stops_next_round_and_executor_cannot_submit(self):
        session, _ = self.create('external');session = self.assess(session);session = self.generate(session)
        candidate = session.candidates[0]
        session = self.assess(session, candidate['id'], confirmed=True, version_sha256=candidate['sha256'],
            evidence=[{'id': '1', 'output': 'slightly better output', 'evidence_ref': 'candidate job'}],
            jury=SimpleNamespace(compare=Mock(return_value={'eligible': True, 'clear_gain': False, 'reason': 'slight'})))
        session = service.select_candidate(session.pk, candidate_id=candidate['id'], reason='轻微改善，保留本轮供审核')
        with self.assertRaisesMessage(ValidationError, '收益有限'):
            self.generate(session)
        request = APIRequestFactory().post('/submit/', {'project': self.project.pk, 'confirmed': True}, format='json')
        force_authenticate(request, self.executor)
        self.assertEqual(SkillAssessmentViewSet.as_view({'post': 'submit'})(request, pk=session.pk).status_code, 403)


class HubAgentHandoffTests(SkillHubBaseTests):
    def request(self, view, action, data, actor=None, pk=None):
        factory = APIRequestFactory()
        request = (factory.get if action == 'list' else factory.post)('/', data, format='json')
        force_authenticate(request, actor or self.lead)
        return view.as_view({'get' if action == 'list' else 'post': action})(request, **({'pk': pk} if pk else {}))

    def test_external_evidence_cannot_enter_hub_api_or_mutate_legacy_sessions(self):
        _, version = self.make_skill_version()
        response = self.request(SkillAssessmentViewSet, 'create', {'project': self.project.pk, 'source_version_id': str(version.pk), 'mode': 'external'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(SkillAssessmentSession.objects.count(),0)
        legacy=service.create_session(project=self.project,version=version,actor=self.lead,mode='external',confirmed=True,
            cases=[{'prompt':'input','expected':'expected','output':'unbound output','evidence_ref':'untrusted file'}])
        for action in ['assess','generate','select','submit']:
            response=self.request(SkillAssessmentViewSet,action,{'project':self.project.pk,'confirmed':True},pk=str(legacy.pk))
            self.assertEqual(response.status_code,400)
        from knowledge_evolution.tasks import hub_assessment_job
        legacy.state,legacy.job_id='queued','old-job';legacy.save()
        with patch.object(service,'assess') as execute:
            with self.assertRaises(ValueError):hub_assessment_job.run(str(legacy.pk),'assess',{},self.lead.pk,'old-job')
            execute.assert_not_called()

    def test_agent_handoff_creates_fresh_owned_session_and_immutable_version_binding(self):
        from .skill_agent_tasks import SkillAgentTaskViewSet, resolve_agent_task_version
        _,version=self.make_skill_version()
        payload={'project':self.project.pk,'source_version_id':str(version.pk)}
        first=self.request(SkillAgentTaskViewSet,'create',payload)
        second=self.request(SkillAgentTaskViewSet,'create',payload)
        self.assertEqual(first.status_code,201)
        self.assertNotEqual(first.data['session_id'],second.data['session_id'])
        self.assertEqual(first.data['package_sha256'],version.package_sha256)
        args=dict(session_id=first.data['session_id'],project_id=self.project.pk,actor=self.lead)
        self.assertEqual(resolve_agent_task_version(**args),str(version.pk))
        for overrides in [dict(requested_version_id='wrong'),dict(actor=self.executor),dict(project_id=self.other_project.pk)]:
            with self.assertRaises(ValidationError):resolve_agent_task_version(**(args|overrides))
        lookup=self.request(SkillAgentTaskViewSet,'list',{'project':self.project.pk,'session_id':first.data['session_id']},actor=self.executor)
        self.assertIsNone(lookup.data['binding'])
        version.package_sha256='0'*64;version.save(update_fields=['package_sha256'])
        with self.assertRaises(ValidationError):resolve_agent_task_version(**args)

    def test_bound_task_cannot_use_legacy_chat_that_ignores_skill_version(self):
        from .skill_agent_tasks import SkillAgentTaskViewSet
        from langgraph_integration.views import ChatAPIView
        from asgiref.sync import async_to_sync
        _,version=self.make_skill_version()
        launched=self.request(SkillAgentTaskViewSet,'create',{'project':self.project.pk,'source_version_id':str(version.pk)})
        request=SimpleNamespace(user=self.lead,data={'session_id':launched.data['session_id'],'message':'execute','project_id':self.project.pk})
        response=async_to_sync(ChatAPIView().post)(request)
        self.assertEqual(response.status_code,409)
