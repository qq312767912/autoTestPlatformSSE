"""独立 Hub 通用测评；候选在项目私有副本中，确认提交前不写 Hub。"""
from contextlib import contextmanager
from copy import deepcopy
import difflib
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from langchain_core.messages import HumanMessage, SystemMessage

from .dual_evaluation import full_package
from .hub_assessment_models import SkillAssessmentSession
from .llm_judges import _json_object
from .paired_evaluation import EvaluationBudget, PairedOutputEvaluator, bounded_llm, normalize_policy
from .skill_assessment import GENERAL_RUBRIC, GeneralSkillEvaluator, SCORING_CONVENTIONS, digest
from .skill_evolution import SkillCandidateDeriver, normalize_package_relative_path
from skills.packaging import compute_package_sha256, iter_package_files, redact_bytes, redact_text
from skills.validation import scan_package_dir
from skills.versions import SkillVersionService


def session_root(session):
    return Path(settings.MEDIA_ROOT) / 'skill-assessments' / str(session.project_id) / str(session.pk)


def package(session, item):
    path = session_root(session) / item['id']
    if not path.is_dir() or compute_package_sha256(path, redact_secrets=True) != item['sha256']:
        raise ValidationError('测评副本已变化，不能复用旧结论')
    return SimpleNamespace(get_full_path=lambda: str(path), package_sha256=item['sha256'])


def verify_source(version):
    version.refresh_from_db()
    if version.state in {'quarantined', 'rejected'} or 'hub_deleted_at' in (version.source_metadata or {}):
        raise ValidationError('该精确版本已隔离、驳回或归档，不能继续测评')
    if not SkillVersionService.verify_package_integrity(version)['ok']:
        raise ValidationError('Hub 原包不存在或指纹不一致')


def normalize_cases(mode, cases):
    if mode not in {'structure', 'text', 'external'}:
        raise ValidationError('测评模式无效')
    if mode == 'structure':
        return []
    if not isinstance(cases, list) or not 1 <= len(cases) <= 5:
        raise ValidationError('请提供 1–5 个测试输入及验收标准')
    result = []
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise ValidationError('测试输入必须是对象')
        item = {'id': str(index)}
        for key in ('prompt', 'expected'):
            value = case.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > 20000:
                raise ValidationError('测试输入与验收标准必须填写，且各不超过 20000 字')
            item[key] = value.strip()
        if mode == 'external':
            for key in ('output', 'evidence_ref'):
                if not isinstance(case.get(key), str) or not case[key].strip() or len(case[key]) > 40000:
                    raise ValidationError('请提供实际产出和可定位来源，不能留空或超过 40000 字')
                item[key] = case[key].strip()
        result.append(item)
    return result


def create_session(*, project, version, mode, cases, actor, policy=None, confirmed=False):
    verify_source(version)
    cases = normalize_cases(mode, cases)
    if mode == 'external' and confirmed is not True:
        raise ValidationError('请确认已有产出确实来自所选精确版本')
    session = SkillAssessmentSession.objects.create(
        project=project, source_version=version, source_sha256=version.package_sha256,
        mode=mode, cases=cases, policy=normalize_policy(policy), rubric=deepcopy(GENERAL_RUBRIC), created_by=actor,
    )
    try:
        root = session_root(session) / 'baseline'
        root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(version.get_full_path(), root, symlinks=False)
        for relative in iter_package_files(root):
            path = root / relative
            redacted, changed = redact_bytes(path.read_bytes())
            if changed:
                path.write_bytes(redacted)
        sha = compute_package_sha256(root, redact_secrets=True)
        if sha != session.source_sha256:
            raise ValidationError('原包与项目副本指纹不一致')
        session.baseline = {'id': 'baseline', 'label': '原始基线', 'sha256': sha, 'state': 'ready'}
        if mode == 'external':
            session.baseline['attestation'] = {'actor_id': str(actor.pk), 'at': timezone.now().isoformat(), 'version_sha256': sha}
        session.save(update_fields=['baseline', 'updated_at'])
    except Exception as exc:
        session.state, session.error = 'failed', str(exc)
        session.save(update_fields=['state', 'error', 'updated_at'])
        raise
    return session


@contextmanager
def operation(session_id, job_id=None):
    with transaction.atomic():
        session = SkillAssessmentSession.objects.select_for_update().select_related('source_version__skill', 'project').get(pk=session_id)
        if job_id is not None and (session.job_id != job_id or session.state != 'queued'):
            raise ValidationError('任务已被处理或不属于当前执行租约')
        if session.state in {'running', 'submitted'} or (session.state == 'queued' and job_id is None):
            raise ValidationError('会话正在执行或已提交，请勿重复操作')
        verify_source(session.source_version)
        if session.source_version.package_sha256 != session.source_sha256:
            raise ValidationError('原始版本指纹已变化')
        session.state, session.error = 'running', ''
        session.save(update_fields=['state', 'error', 'updated_at'])
    budget = EvaluationBudget(session.policy)
    budget.calls = session.budget.get('calls', 0)
    budget.estimated_tokens = session.budget.get('estimated_tokens', 0)
    budget.started -= session.budget.get('elapsed_seconds', 0)
    try:
        yield session, budget
        if session.state == 'running':
            session.state = 'completed'
    except Exception as exc:
        session.state, session.error = 'failed', str(exc)
        raise
    finally:
        session.budget = budget.snapshot()
        session.job_id = ''
        session.save(update_fields=['job_id', 'state', 'error', 'baseline', 'candidates', 'winner_id', 'budget', 'updated_at'])


def selected_item(session, candidate_id):
    if candidate_id in (None, '', 'baseline'):
        return session.baseline
    for candidate in session.candidates:
        if candidate['id'] == candidate_id:
            return candidate
    raise ValidationError('候选不属于当前测评会话')


def llm_config():
    from langgraph_integration.models import LLMConfig
    config = LLMConfig.objects.filter(is_active=True).first()
    if not config:
        raise ValidationError('请先配置激活的测评模型')
    return config


def task_outputs(session, item, *, config, budget, evidence=None, confirmed=False, version_sha256='', factory=bounded_llm):
    if session.mode == 'structure':
        return {}
    if session.mode == 'external':
        if item['id'] == 'baseline':
            values = session.cases
        else:
            if confirmed is not True or version_sha256 != item['sha256']:
                raise ValidationError('请确认产出来自此候选，并提供匹配的候选指纹')
            if not isinstance(evidence, list) or len(evidence) != len(session.cases):
                raise ValidationError('每个冻结的测试输入都必须提供新的候选实际产出')
            values = evidence
        by_id = {str(value.get('id')): value for value in values if isinstance(value, dict)}
        if set(by_id) != {case['id'] for case in session.cases}:
            raise ValidationError('候选产出与冻结测试输入不一致')
        rows = []
        for case in session.cases:
            entry = by_id[case['id']]
            if not all(isinstance(entry.get(key), str) and entry[key].strip() and len(entry[key]) <= 40000 for key in ('output', 'evidence_ref')):
                raise ValidationError('候选实际产出和来源证据不能为空或超限')
            rows.append({'id': case['id'], 'output': entry['output'], 'evidence_ref': entry['evidence_ref']})
        return {'execution_mode': 'human_attested_external', 'cases': rows}
    if not session.source_version.is_runnable:
        raise ValidationError('该 Skill 当前不可运行，可使用结构体检')
    manifest = scan_package_dir(Path(package(session, item).get_full_path())).manifest or {}
    if manifest.get('entrypoint') or manifest.get('permissions') or manifest.get('allowed-tools'):
        raise ValidationError('此 Skill 声明脚本或工具依赖；请使用结构体检或前往 Agent 任务执行与复核，文本模式不模拟工具执行')
    data = full_package(package(session, item))
    outputs = []
    for case in session.cases:
        instruction = SCORING_CONVENTIONS + (
            '执行给定 Skill 的纯文本任务。NEVER 声称调用工具、运行脚本、联网或保存文件；此运行器没有这些能力。'
            '任务需要这些操作时 MUST 返回 status=unsupported，不得模拟成功。'
            'MUST 返回JSON：{"status":"completed|unsupported","output":"实际文本产出或不可执行原因"}。'
        )
        import json
        material = json.dumps({'skill': data, 'input': case['prompt']}, ensure_ascii=False)
        budget.reserve(instruction + material, output_tokens=8192)
        payload = _json_object(factory(config, max_tokens=8192).invoke([SystemMessage(content=instruction), HumanMessage(content=material)]))
        budget.check_time()
        if payload.get('status') != 'completed' or not isinstance(payload.get('output'), str) or not payload['output'].strip():
            raise ValidationError('文本任务无法完成：' + str(payload.get('output') or '没有有效实际产出'))
        outputs.append({'id': case['id'], 'output': payload['output']})
    return {'execution_mode': 'llm_text_only', 'cases': outputs}


def assess(session_id, *, candidate_id='', evaluator=None, jury=None, factory=bounded_llm, evidence=None, confirmed=False, version_sha256='', actor=None, job_id=None):
    with operation(session_id, job_id=job_id) as (session, budget):
        item = selected_item(session, candidate_id)
        if item.get('general', {}).get('status') in {'completed', 'partial'}:
            raise ValidationError('该副本已测评，不能覆盖历史结果')
        config = llm_config()
        output = task_outputs(session, item, config=config, budget=budget, evidence=evidence,
                              confirmed=confirmed, version_sha256=version_sha256, factory=factory)
        source = {'test_cases': [{'id': case['id'], 'prompt': case['prompt'], 'expected': case['expected']} for case in session.cases]}
        result = (evaluator or GeneralSkillEvaluator()).evaluate(
            package=full_package(package(session, item)), output=output, source=source,
            config=config, budget=budget, rubric=session.rubric, structural_only=session.mode == 'structure',
        )
        item.update(general=result, output=output, output_hash=digest(output), state='assessed')
        if session.mode == 'external':
            item['attestation'] = {'actor_id': str(getattr(actor, 'pk', session.created_by_id)), 'at': timezone.now().isoformat(), 'version_sha256': item['sha256']}
        if item['id'] != 'baseline' and result.get('status') == 'completed':
            parent = selected_item(session, item['parent_id'])
            package(session, parent)
            if parent.get('general', {}).get('status') != 'completed':
                raise ValidationError('父基线缺少完整实测，不能比较候选')
            old = {'cases': [{'id': row['id'], 'output': row['output']} for row in parent['output'].get('cases', [])]}
            new = {'cases': [{'id': row['id'], 'output': row['output']} for row in output.get('cases', [])]}
            comparison = (jury or PairedOutputEvaluator()).compare(
                baseline=old, candidate=new, source=source, config=config, budget=budget,
            )
            comparison['binding'] = digest([parent['sha256'], parent['output_hash'], item['sha256'], item['output_hash'], session.cases])
            item['comparison'] = comparison
    return SkillAssessmentSession.objects.get(pk=session_id)


def generate(session_id, *, factory=bounded_llm, job_id=None):
    import json
    with operation(session_id, job_id=job_id) as (session, budget):
        if len(session.candidates) >= session.policy['max_rounds']:
            raise ValidationError('达到最多三轮候选预算')
        parent = selected_item(session, session.winner_id)
        if parent.get('general', {}).get('status') not in {'completed', 'partial'}:
            raise ValidationError('请先对基线完成通用测评')
        if parent['id'] != 'baseline' and session.mode != 'structure' and not parent.get('comparison', {}).get('clear_gain'):
            raise ValidationError('本轮收益有限，停止继续迭代；可复核后提交本轮候选')
        data = full_package(package(session, parent))
        instruction = SCORING_CONVENTIONS + (
            '根据通用测评短板生成最小修复补丁。MUST 保留 Skill 用途、输入输出契约和业务规则。'
            '只允许修改 SKILL.md 或 references/*.md，NEVER 加入个人风格约束或无关规则。'
            'MUST 返回JSON：{"edits":[{"path":"SKILL.md","content":"完整文件内容"}],"rationale":"修改理由"}。'
        )
        material = json.dumps({'package': data, 'shortfalls': parent['general'].get('shortfalls', [])}, ensure_ascii=False)
        budget.reserve(instruction + material, output_tokens=8192)
        patch = _json_object(factory(llm_config(), max_tokens=8192).invoke([SystemMessage(content=instruction), HumanMessage(content=material)]))
        budget.check_time()
        edits = patch.get('edits')
        if not isinstance(edits, list) or not 1 <= len(edits) <= 8 or patch.get('remove'):
            raise ValidationError('候选补丁必须包含 1–8 个文本文件编辑，不能删除文件')
        root = Path(package(session, parent).get_full_path())
        diffs, changed, paths = [], False, set()
        for edit in edits:
            if not isinstance(edit, dict) or not isinstance(edit.get('content'), str):
                raise ValidationError('候选编辑缺少完整文本内容')
            path = normalize_package_relative_path(edit.get('path'))
            if (path != 'SKILL.md' and not (path.startswith('references/') and path.endswith('.md'))) or path in paths:
                raise ValidationError('候选只能编辑 SKILL.md 或 references Markdown，路径不得重复')
            paths.add(path);edit['path'] = path
            before = (root / path).read_text(encoding='utf-8') if (root / path).is_file() else ''
            if len(edit['content']) > max(len(before), 1024) * session.policy['max_growth_ratio']:
                raise ValidationError('候选文件增长超过体积预算')
            changed = changed or before != edit['content']
            clean_before, _ = redact_text(before);clean_after, _ = redact_text(edit['content'])
            diffs.append(''.join(difflib.unified_diff(clean_before.splitlines(True), clean_after.splitlines(True), fromfile=path, tofile=path)))
        if not changed:
            raise ValidationError('候选没有实质修改，停止无收益进化')
        candidate_id = str(uuid.uuid4())
        target = session_root(session) / candidate_id
        try:
            SkillCandidateDeriver.apply_patch(source_dir=root, patch={'edits': edits}, work_dir=target)
            report = scan_package_dir(target)
            if not report.ok:
                raise ValidationError('候选静态校验未通过：' + '；'.join(issue.message for issue in report.errors[:4]))
            candidate_sha = compute_package_sha256(target, redact_secrets=True)
            if candidate_sha == parent['sha256']:
                raise ValidationError('候选标准化包内容没有实质变化')
            session.candidates.append({'id': candidate_id, 'label': f'候选 {len(session.candidates)+1}',
                'parent_id': parent['id'], 'sha256': candidate_sha,
                'diff': '\n'.join(diffs), 'rationale': str(patch.get('rationale') or ''), 'state': 'ready'})
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
    return SkillAssessmentSession.objects.get(pk=session_id)


def validate_comparison(session, item):
    package(session, item)
    parent = selected_item(session, item['parent_id'])
    package(session, parent)
    comparison = item.get('comparison', {})
    binding = digest([parent['sha256'], parent.get('output_hash'), item['sha256'], item.get('output_hash'), session.cases])
    if (item.get('general', {}).get('status') != 'completed' or not comparison.get('eligible')
            or comparison.get('binding') != binding or digest(item.get('output')) != item.get('output_hash')
            or digest(parent.get('output')) != parent.get('output_hash')):
        raise ValidationError('候选缺少完整实测或独立比较未通过，不能提交 Hub')


def select_candidate(session_id, *, candidate_id, reason):
    with operation(session_id) as (session, budget):
        item = selected_item(session, candidate_id)
        if item['id'] == 'baseline' or item.get('general', {}).get('status') not in {'partial', 'completed'} or not reason.strip():
            raise ValidationError('请先测评候选并填写选择理由')
        package(session, item)
        if session.mode != 'structure':
            validate_comparison(session, item)
        session.winner_id = item['id']
        item['selection_reason'] = reason.strip()
    return SkillAssessmentSession.objects.get(pk=session_id)


def submit_candidate(session_id, *, actor, confirmed=False):
    with operation(session_id) as (session, budget):
        if confirmed is not True or not session.winner_id:
            raise ValidationError('请先人工选择候选并确认 diff')
        item = selected_item(session, session.winner_id)
        validate_comparison(session, item)
        if item.get('hub_version_id'):
            raise ValidationError('候选已提交，禁止重复提交')
        with tempfile.TemporaryDirectory(prefix='hub-assessment-submit-') as temporary:
            target = Path(temporary) / 'package'
            shutil.copytree(package(session, item).get_full_path(), target)
            SkillCandidateDeriver.advance_package_version(target, skill_version=session.source_version,
                                                         version_label=f'hub.{str(session.pk)[:8]}.{session.candidates.index(item)+1}')
            _, version = SkillVersionService.create_candidate_from_dir(
                source_dir=target, project=session.project, actor=actor, source_type='evolution',
                source_metadata={'assessment_session': str(session.pk), 'source_version_id': str(session.source_version_id),
                                 'source_sha256': session.source_sha256, 'private_candidate_sha256': item['sha256']},
                change_reason=item.get('selection_reason', ''), expected_benefit='通用测评与同输入独立比较通过',
            )
            SkillVersionService.submit_for_validation(version, actor=actor, reason=item.get('selection_reason', ''))
            item['hub_version_id'] = str(version.pk)
            session.state = 'submitted'
    return SkillAssessmentSession.objects.get(pk=session_id)


def session_view(session):
    def visible(item):
        return {key: value for key, value in item.items() if key != 'path'}
    return {'id': str(session.pk), 'state': session.state, 'error': session.error,
            'skill_name': session.source_version.skill.name, 'source_version_id': str(session.source_version_id),
            'source_version': session.source_version.version, 'source_sha256': session.source_sha256,
            'mode': session.mode, 'cases': session.cases, 'policy': session.policy, 'budget': session.budget,
            'baseline': visible({'id': 'baseline', 'label': '原始基线', 'sha256': session.source_sha256, 'state': session.state, **session.baseline}), 'candidates': [visible(item) for item in session.candidates],
            'winner_id': session.winner_id, 'created_at': session.created_at.isoformat()}
