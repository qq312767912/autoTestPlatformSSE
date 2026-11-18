"""UI 生成使用服务端快照与经执行证据校验的绑定工具。"""
import json
from django.core.exceptions import ValidationError
from django.db import transaction
from langchain_core.tools import tool
from asgiref.sync import sync_to_async
from knowledge_evolution.hub_assessment_models import SkillAgentTaskBinding
from testcases.models import TestCase
from ui_automation.models import UiModule, UiEnvironmentConfig, UiAuthState, UiTestCase

UI_GENERATION_INSTRUCTION = """
<system-conventions>RFC 2119 applies to MUST, REQUIRED, SHOULD, MAY. NEVER means MUST NOT.</system-conventions>
<critical>
你 MUST 使用本任务锁定的 Skill 精确版本，MUST 调用 get_ui_generation_context 读取服务端范围与实际功能步骤。
你 NEVER 改写功能用例业务步骤或绕过 bind_generated_ui_case 绑定工具；未试跑不得宣称成功。
</critical>
<workflow>
1. MUST 按服务端环境与目标模块执行，登录态由平台注入，NEVER 输出 Cookie、Token 或密码。没有登录态时如需鉴权，停止并请用户先采集登录态。
2. MUST 逐条处理功能用例，以真实浏览器 DOM 为依据，MUST 使用 ui_generation_browser 工具探索（open→snapshot→click/fill/get/eval），不切换其他浏览器 Skill。已有 ui_test_case_id 时 MUST 维护原 UI 用例，NEVER 另建替代或改绑；没有绑定时先按页面 URL、元素语义、定位器查询复用资产，无法复用才创建。NEVER 编造元素或使用绝对 XPath。
3. MUST 将功能步骤及预期展开为真实页面原子操作和明确断言，保存页面、元素、页面步骤与 UI 用例；NEVER 仅创建空用例或将手工步骤粘贴当作自动化完成。
4. MUST 使用所选环境 execute_testcase（--config_id=env_config_id，--actuator_id recorder-browser）并等待真实结果，失败最多修复1次，NEVER 删除或弱化断言来通过。定位漂移可补充备用定位并报告人工确认项。
5. 试跑通过后 MUST 调用 bind_generated_ui_case(functional_case_id, ui_test_case_id)。该工具检查服务端执行记录，失败或未执行不得绑定。NEVER 改用功能用例编辑接口写绑定。
6. MUST 汇总保存、试跑通过、失败、绑定、复用、新增数量与待确认项，区分已完成和未验证。
</workflow>
<critical>你 NEVER 将未执行、失败或绑定失败的用例报告为完成。</critical>
"""


def validate_ui_context(project, actor, raw):
    if not actor.has_perms(['ui_automation.add_uitestcase', 'testcases.view_testcase', 'testcases.change_testcase']):
        raise ValidationError('缺少 UI 用例生成或功能用例绑定权限')
    if not isinstance(raw, dict) or not isinstance(raw.get('functional_case_ids'), list):
        raise ValidationError('请选择功能用例、模块和环境')
    try:
        ids = list(dict.fromkeys(int(value) for value in raw['functional_case_ids']))
        if not ids or len(ids) > 100:
            raise ValueError()
        module = UiModule.objects.get(pk=raw['module_id'], project=project)
        env = UiEnvironmentConfig.objects.get(pk=raw['env_config_id'], project=project)
        cases = list(TestCase.objects.filter(pk__in=ids, project=project).prefetch_related('steps'))
        if len(cases) != len(ids):
            raise ValueError()
        if any(case.ui_test_case_id and not UiTestCase.objects.filter(pk=case.ui_test_case_id, project=project).exists() for case in cases):
            raise ValueError()
        if any(case.ui_test_case_id for case in cases) and not actor.has_perm('ui_automation.change_uitestcase'):
            raise ValidationError('缺少维护已有 UI 用例权限')
        if not env.base_url:
            raise ValueError()
        auth_id = raw.get('auth_state_id')
        if auth_id:
            from .auth_state_binding import prepare_storage_state
            state = UiAuthState.objects.get(pk=auth_id, env_config=env, is_active=True)
            prepare_storage_state(state.state_json)
            auth_id = state.pk
    except (KeyError, TypeError, ValueError, UiModule.DoesNotExist, UiEnvironmentConfig.DoesNotExist, UiAuthState.DoesNotExist):
        raise ValidationError('范围无效、跨项目，或环境／登录态不可用')
    if raw.get('actuator_id', 'recorder-browser') != 'recorder-browser':
        raise ValidationError('探索转换当前仅支持平台录制器浏览器')
    by_id = {case.pk: case for case in cases}
    return {'business_notes': str(raw.get('business_notes') or '')[:2000], 'actuator_id': 'recorder-browser', 'module_id': module.pk, 'env_config_id': env.pk, 'base_url': env.base_url,
            'auth_state_id': auth_id or None,
            'functional_cases': [{'id': pk, 'name': by_id[pk].name, 'precondition': by_id[pk].precondition,
                'ui_test_case_id': by_id[pk].ui_test_case_id,
                'baseline_assertions': assertion_snapshot(by_id[pk].ui_test_case_id) if by_id[pk].ui_test_case_id else [],
                'updated_at': by_id[pk].updated_at.isoformat(),
                'steps': list(by_id[pk].steps.values('step_number', 'description', 'expected_result'))} for pk in ids]}


def get_binding(session_id, user_id, project_id):
    binding = SkillAgentTaskBinding.objects.select_related('session').get(
        session__session_id=session_id, session__user_id=user_id, session__project_id=project_id, generation_mode__in=['ui_case', 'ui_healing'])
    if binding.session.auth_state_id != binding.generation_context.get('auth_state_id'):
        raise ValidationError('任务登录态绑定已变化，请重新创建生成任务')
    return binding


@transaction.atomic
def bind_generated_case(session_id, user_id, project_id, functional_case_id, ui_test_case_id):
    from django.contrib.auth import get_user_model
    actor = get_user_model().objects.get(pk=user_id)
    if not actor.has_perms(['testcases.change_testcase', 'ui_automation.change_uitestcase']):
        raise ValidationError('缺少功能用例绑定权限')
    binding = get_binding(session_id, user_id, project_id)
    binding = SkillAgentTaskBinding.objects.select_for_update().get(pk=binding.pk)
    snapshots = {case['id']: case for case in binding.generation_context['functional_cases']}
    active_source = binding.generation_run.get('active_source_id')
    if active_source is not None and functional_case_id != active_source:
        raise ValidationError('当前只允许处理本轮业务用例')
    if functional_case_id not in snapshots:
        raise ValidationError('功能用例不在当前任务范围')
    case = TestCase.objects.select_for_update().get(pk=functional_case_id, project_id=project_id)
    snapshot = snapshots[case.pk]
    already_verified = ((binding.generation_run.get('verified_cases') or {}).get(str(functional_case_id)) or binding.generation_run.get('verified') or {}).get('ui_test_case_id') == ui_test_case_id and case.ui_test_case_id == ui_test_case_id
    if case.ui_test_case_id != snapshot.get('ui_test_case_id') and not already_verified:
        raise ValidationError('功能用例关联已变化，请重新创建任务')
    if case.ui_test_case_id and case.ui_test_case_id != ui_test_case_id:
        raise ValidationError('已有绑定只能维护原 UI 用例')
    steps = list(case.steps.values('step_number', 'description', 'expected_result'))
    if case.name != snapshot['name'] or case.precondition != snapshot['precondition']:
        raise ValidationError('功能用例业务内容已变化，请重新创建任务')
    if (case.updated_at.isoformat() != snapshot['updated_at'] and not already_verified) or steps != snapshot['steps']:
        raise ValidationError('功能用例已变化，请重新创建生成任务')
    ui = UiTestCase.objects.get(pk=ui_test_case_id, project_id=project_id)
    if TestCase.objects.filter(ui_test_case_id=ui.pk).exclude(pk=case.pk).exists():
        raise ValidationError('目标 UI 用例已关联其他业务用例，请维护各自来源资产')
    if not case.ui_test_case_id and ui.module_id != binding.generation_context['module_id']:
        raise ValidationError('新增 UI 用例必须属于目标模块')
    case_steps = list(ui.case_steps.select_related('page_step__page').prefetch_related('page_step__step_details__element'))
    if not case_steps or any(not step.page_step.step_details.exists() for step in case_steps):
        raise ValidationError('空 UI 用例或空页面步骤不得绑定')
    touched = [binding.created_at, ui.updated_at]
    has_assertion = False
    for step in case_steps:
        touched += [step.updated_at, step.page_step.updated_at, step.page_step.page.updated_at]
        for detail in step.page_step.step_details.all():
            touched.append(detail.updated_at)
            has_assertion = has_assertion or detail.step_type == 1
            if detail.element:
                touched.append(detail.element.updated_at)
    if not has_assertion:
        raise ValidationError('缺少明确断言的 UI 用例不得绑定')
    from ui_automation.role_execution import required_roles
    required = required_roles('\n'.join(str(step.get('action') or '') for step in snapshot.get('steps', [])))
    if set(required) - {row['name'] for row in ui.role_matrix}:
        raise ValidationError('源用例要求遍历角色，请先在UI用例中配置全部执行角色：' + '、'.join(required))
    if ui.role_matrix:
        summary = ui.result_data or {}
        if not summary.get('role_complete') or summary.get('status') != 'success' or len(summary.get('role_results', [])) != len(ui.role_matrix):
            raise ValidationError('要求的角色尚未全部试跑通过，不得绑定')
    record = ui.execution_records.order_by('-created_at', '-pk').first()
    if not record or record.status != 2 or not record.start_time or record.start_time < max(touched):
        raise ValidationError('缺少本次任务开始后、最新定义的成功试跑记录')
    if record.executor_id != user_id:
        raise ValidationError('成功试跑执行人不属于当前任务')
    if not UiEnvironmentConfig.objects.filter(pk=binding.generation_context['env_config_id'], project_id=project_id, base_url=binding.generation_context['base_url']).exists():
        raise ValidationError('执行环境配置已变化，请重新确认环境')
    environment = record.environment or {}
    if str(environment.get('env_config_id')) != str(binding.generation_context['env_config_id']):
        raise ValidationError('成功试跑环境与生成任务不一致')
    if snapshot.get('baseline_assertions') and snapshot['baseline_assertions'] != assertion_snapshot(ui.pk):
        raise ValidationError('已有 UI 业务断言不得被自动改写')
    locked_assertions = (binding.generation_run.get('trial_assertions') or {}).get(str(ui.pk))
    if locked_assertions is not None and locked_assertions != assertion_snapshot(ui.pk):
        raise ValidationError('业务断言在试跑后被修改，禁止绑定')
    case.ui_test_case = ui
    case.execution_mode = 'hybrid'
    case.save(update_fields=['ui_test_case', 'execution_mode', 'updated_at'])
    result = {'bound': True, 'functional_case_id': case.pk, 'ui_test_case_id': ui.pk, 'execution_record_id': record.pk}
    receipts = {**binding.generation_run.get('verified_cases', {}), str(case.pk): result}
    binding.generation_run = {**binding.generation_run, 'verified': result, 'verified_cases': receipts}
    binding.save(update_fields=['generation_run'])
    return result


def ui_generation_tools(mode, session_id, user_id, project_id):
    if mode == 'ui_healing':
        from ui_automation.healing_tools import healing_tools
        return healing_tools(session_id, user_id, project_id)
    if mode != 'ui_case':
        return []

    @tool
    async def get_ui_generation_context() -> str:
        """读取当前UI生成任务的服务端范围、环境和功能用例步骤；开始生成前调用。"""
        binding = await sync_to_async(get_binding)(session_id, user_id, project_id)
        context = dict(binding.generation_context)
        active = binding.generation_run.get('active_source_id')
        if active is not None:
            context['functional_cases'] = [x for x in context['functional_cases'] if x['id'] == active]
            context['active_source_id'] = active
        return json.dumps(context, ensure_ascii=False)

    @tool
    async def bind_generated_ui_case(functional_case_id: int, ui_test_case_id: int) -> str:
        """成功试跑后绑定功能用例与UI用例，服务端核对范围、环境和最新成功执行记录。"""
        try:
            result = await sync_to_async(bind_generated_case)(session_id, user_id, project_id, functional_case_id, ui_test_case_id)
            return json.dumps(result, ensure_ascii=False)
        except (ValidationError, TestCase.DoesNotExist, UiTestCase.DoesNotExist) as exc:
            return json.dumps({'bound': False, 'error': str(exc)}, ensure_ascii=False)
    @tool
    async def ui_generation_browser(command: str, arguments: list[str]) -> str:
        """操作任务隔离的真实浏览器，无需切换Skill。先open [URL]，snapshot []返回元素ref，再click [@e1]、fill [@e2,文本]、get [attr,@e1,id]或eval [JS]检查DOM；完成后close []。禁止读取或输出凭据。"""
        from .ui_generation_browser import run_ui_browser
        binding = await sync_to_async(get_binding)(session_id, user_id, project_id)
        try:
            return await sync_to_async(run_ui_browser)(binding, command, arguments)
        except Exception as exc:
            return json.dumps({'error':str(exc)}, ensure_ascii=False)
    return [get_ui_generation_context, bind_generated_ui_case, ui_generation_browser]


def assertion_snapshot(ui_id):
    from ui_automation.models import UiPageStepsDetailed
    return list(UiPageStepsDetailed.objects.filter(page_step__case_usages__test_case_id=ui_id, step_type=1)
                .order_by('pk').values('element_id', 'ope_key', 'ope_value'))


@transaction.atomic
def lock_trial_assertions(session_id, project_id, ui_id, env_id):
    binding = SkillAgentTaskBinding.objects.select_for_update().get(session__session_id=session_id, session__project_id=project_id, generation_mode='ui_case')
    if str(env_id) != str(binding.generation_context['env_config_id']):
        raise ValidationError('试跑环境与任务不一致')
    if not UiTestCase.objects.filter(pk=ui_id, project_id=project_id).exists():
        raise ValidationError('UI 用例不属于当前项目')
    assertions = assertion_snapshot(ui_id)
    for source in binding.generation_context['functional_cases']:
        if str(source.get('ui_test_case_id')) == str(ui_id) and source.get('baseline_assertions') and source['baseline_assertions'] != assertions:
            raise ValidationError('已有 UI 用例的业务断言已变化，请人工复核')
    if not assertions:
        raise ValidationError('试跑前必须保存明确业务断言')
    run = dict(binding.generation_run)
    locks = dict(run.get('trial_assertions') or {})
    if str(ui_id) in locks and locks[str(ui_id)] != assertions:
        raise ValidationError('试跑后业务断言发生变化，禁止自动弱化或替换断言，请人工复核')
    locks[str(ui_id)] = assertions
    run['trial_assertions'] = locks
    binding.generation_run = run
    binding.save(update_fields=['generation_run'])
