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
2. MUST 逐条处理功能用例，以真实浏览器 DOM 为依据，MUST 使用 ui_generation_browser 工具探索（open→snapshot→click/fill/get/eval），不切换其他浏览器 Skill。先按页面 URL、元素语义、定位器依次查询复用已有资产，无法复用才创建。NEVER 编造元素或使用绝对 XPath。
3. MUST 将功能步骤及预期展开为真实页面原子操作和明确断言，保存页面、元素、页面步骤与 UI 用例；NEVER 仅创建空用例或将手工步骤粘贴当作自动化完成。
4. MUST 使用所选环境 execute_testcase 并等待真实结果，失败最多修复5次，NEVER 删除或弱化断言来通过。定位漂移可补充备用定位并报告人工确认项。
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
        if len(cases) != len(ids) or any(case.ui_test_case_id for case in cases):
            raise ValueError()
        if not env.base_url:
            raise ValueError()
        auth_id = raw.get('auth_state_id')
        if auth_id:
            from .auth_state_binding import prepare_storage_state
            state = UiAuthState.objects.get(pk=auth_id, env_config=env, is_active=True)
            prepare_storage_state(state.state_json)
            auth_id = state.pk
    except (KeyError, TypeError, ValueError, UiModule.DoesNotExist, UiEnvironmentConfig.DoesNotExist, UiAuthState.DoesNotExist):
        raise ValidationError('范围无效、跨项目、已绑定，或环境／登录态不可用')
    by_id = {case.pk: case for case in cases}
    return {'module_id': module.pk, 'env_config_id': env.pk, 'base_url': env.base_url,
            'auth_state_id': auth_id or None,
            'functional_cases': [{'id': pk, 'name': by_id[pk].name, 'precondition': by_id[pk].precondition,
                'updated_at': by_id[pk].updated_at.isoformat(),
                'steps': list(by_id[pk].steps.values('step_number', 'description', 'expected_result'))} for pk in ids]}


def get_binding(session_id, user_id, project_id):
    binding = SkillAgentTaskBinding.objects.select_related('session').get(
        session__session_id=session_id, session__user_id=user_id, session__project_id=project_id, generation_mode='ui_case')
    if binding.session.auth_state_id != binding.generation_context.get('auth_state_id'):
        raise ValidationError('任务登录态绑定已变化，请重新创建生成任务')
    return binding


@transaction.atomic
def bind_generated_case(session_id, user_id, project_id, functional_case_id, ui_test_case_id):
    from django.contrib.auth import get_user_model
    actor = get_user_model().objects.get(pk=user_id)
    if not actor.has_perm('testcases.change_testcase'):
        raise ValidationError('缺少功能用例绑定权限')
    binding = get_binding(session_id, user_id, project_id)
    snapshots = {case['id']: case for case in binding.generation_context['functional_cases']}
    if functional_case_id not in snapshots:
        raise ValidationError('功能用例不在当前任务范围')
    case = TestCase.objects.select_for_update().get(pk=functional_case_id, project_id=project_id)
    if case.ui_test_case_id:
        if case.ui_test_case_id == ui_test_case_id:
            return {'bound': True, 'functional_case_id': case.pk, 'ui_test_case_id': ui_test_case_id}
        raise ValidationError('功能用例已绑定其他 UI 用例')
    snapshot = snapshots[case.pk]
    steps = list(case.steps.values('step_number', 'description', 'expected_result'))
    if case.updated_at.isoformat() != snapshot['updated_at'] or steps != snapshot['steps']:
        raise ValidationError('功能用例已变化，请重新创建生成任务')
    ui = UiTestCase.objects.get(pk=ui_test_case_id, project_id=project_id, module_id=binding.generation_context['module_id'])
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
    record = ui.execution_records.order_by('-created_at', '-pk').first()
    if not record or record.status != 2 or not record.start_time or record.start_time < max(touched):
        raise ValidationError('缺少本次任务开始后、最新定义的成功试跑记录')
    environment = record.environment or {}
    if str(environment.get('env_config_id')) != str(binding.generation_context['env_config_id']):
        raise ValidationError('成功试跑环境与生成任务不一致')
    case.ui_test_case = ui
    case.execution_mode = 'hybrid'
    case.save(update_fields=['ui_test_case', 'execution_mode', 'updated_at'])
    return {'bound': True, 'functional_case_id': case.pk, 'ui_test_case_id': ui.pk, 'execution_record_id': record.pk}


def ui_generation_tools(mode, session_id, user_id, project_id):
    if mode != 'ui_case':
        return []

    @tool
    async def get_ui_generation_context() -> str:
        """读取当前UI生成任务的服务端范围、环境和功能用例步骤；开始生成前调用。"""
        binding = await sync_to_async(get_binding)(session_id, user_id, project_id)
        return json.dumps(binding.generation_context, ensure_ascii=False)

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
