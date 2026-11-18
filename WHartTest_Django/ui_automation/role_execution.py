"""用例角色矩阵：共用脚本，每个身份独立浏览器会话、独立证据。"""
import uuid
import re
from asgiref.sync import sync_to_async
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from .models import UiTestCase, UiAuthState, UiBatchExecutionRecord, UiExecutionRecord, UiEnvironmentConfig


def required_roles(text):
    """仅从明确的“遍历角色：”声明提取角色，不臆造业务身份。"""
    roles = []
    for line in re.findall(r'遍历角色[：:]\s*([^\n。；;]+)', text or ''):
        for name in re.split(r'[、,，]', line):
            name = name.strip()
            if name and name not in roles: roles.append(name)
    return roles


def validate_matrix(rows, project_id):
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValidationError('执行角色必须为数组，最多20项')
    names = set()
    normalized = []
    for row in rows:
        if not isinstance(row, dict): raise ValidationError('角色配置格式错误')
        name = str(row.get('name') or '').strip()
        if not name or len(name) > 80 or name in names: raise ValidationError('角色名称不能为空、超过80字或重复')
        names.add(name)
        guest = row.get('guest', False)
        if type(guest) is not bool: raise ValidationError('游客标记必须为布尔值')
        auth_id = row.get('auth_state_id')
        if auth_id is not None and (type(auth_id) is not int or auth_id <= 0): raise ValidationError('登录态ID必须为正整数')
        if guest and auth_id: raise ValidationError('游客不能绑定登录态')
        if auth_id and not UiAuthState.objects.filter(pk=auth_id, env_config__project_id=project_id).exists():
            raise ValidationError('登录态不存在或不属于当前项目')
        from .role_login import normalize_login
        login = {} if guest else normalize_login(row, project_id)
        normalized.append({**login, 'name': name, 'guest': guest, 'auth_state_id': None if login else (auth_id or None), **({k:str(row.get(k) or '').strip() for k in ['identity_selector','identity_text']} if not login and (row.get('identity_selector') or row.get('identity_text')) else {})})
    return normalized


def execution_roles(case_id, env_id, user=None, override=None):
    case = UiTestCase.objects.get(pk=case_id)
    if user is not None:
        from knowledge_evolution.views import _ensure_project_member
        _ensure_project_member(user, case.project_id)
        if not user.has_perm('ui_automation.view_uitestcase'):
            raise PermissionDenied('缺少UI用例查看权限')
    roles = validate_matrix(case.role_matrix if override is None else override, case.project_id)
    if roles and not UiEnvironmentConfig.objects.filter(pk=env_id,project_id=case.project_id).exists():
        raise ValidationError('角色执行需要选择当前项目的执行环境')
    for role in roles:
        if role['guest']: continue
        if role.get('account'):
            from .role_login import validate_login
            validate_login(role,case.project_id)
            continue
        auth = UiAuthState.objects.filter(pk=role['auth_state_id'], env_config_id=env_id, is_active=True).first()
        if not auth or not auth.state_json:
            raise ValidationError('角色「%s」缺少当前环境可用登录态，请配置后执行' % role['name'])
        if not role.get('identity_selector') or not role.get('identity_text'):
            raise ValidationError('角色「%s」需要配置身份校验CSS及预期文本，避免过期登录态或账号串用' % role['name'])
    return case, roles


async def run_roles(consumer, args, user):
    try:
        case, roles = await sync_to_async(execution_roles)(args['case_id'], args.get('env_config_id'), consumer.scope.get('user'), args.get('execution_role_matrix'))
    except (ValidationError, PermissionDenied, UiTestCase.DoesNotExist) as exc:
        await consumer._recorder_exec_error('case', args, user, str(exc))
        return
    if not roles:
        await consumer._execute_via_recorder('case', args, user)
        return
    batch = await sync_to_async(UiBatchExecutionRecord.objects.create)(name=case.name + ' · 角色执行', total_cases=len(roles), executor=consumer.scope.get('user'), status=1, start_time=timezone.now())
    started = timezone.now()
    consumer._role_stop_requested = False
    results = []
    try:
        for index, role in enumerate(roles):
            if consumer._role_stop_requested: break
            from .socket_models import SocketDataModel, QueueModel, ResponseCode, NoticeType
            await consumer.send_json(SocketDataModel(code=ResponseCode.SUCCESS,msg='role_progress',user=user,is_notice=NoticeType.WEB,data=QueueModel(func_name='u_role_progress',func_args={'case_id':case.pk,'role':role['name'],'index':index+1,'total':len(roles)})))
            request_id = uuid.uuid4().hex
            current = {**args, 'role_batch_id':batch.pk, 'role_name':role['name'], 'role_auth_state_id':role['auth_state_id'], 'role_identity_selector':role.get('identity_selector'), 'role_identity_text':role.get('identity_text'), 'execution_request_id':request_id}
            if role.get('account'):
                from .role_login import login_steps
                current['role_login_steps'] = login_steps(role,case.project_id)
            try:
                await consumer._execute_via_recorder('case', current, user)
            except Exception as exc:
                results.append({'role':role['name'],'record_id':None,'status':'failed','message':str(exc)[:300]})
                continue
            record = await sync_to_async(lambda: UiExecutionRecord.objects.filter(test_case=case, environment__execution_request_id=request_id).order_by('-pk').first())()
            results.append({'role':role['name'], 'record_id':record.pk if record else None, 'status':'success' if record and record.status==2 else 'failed', 'message':record.error_message if record else '未取得执行记录'})
    finally:
        passed = sum(x['status']=='success' for x in results)
        complete = len(results)==len(roles)
        success = complete and passed==len(roles)
        for role in roles[len(results):]:
            results.append({'role':role['name'],'record_id':None,'status':'not_run','message':'执行已停止，未覆盖此角色'})
        summary = {'case_id':case.pk, 'role_matrix_snapshot':roles, 'execution_request_id':args.get('execution_request_id'), 'status':'success' if success else 'failed', 'message':f'角色执行：通过 {passed}/{len(roles)}', 'role_results':results, 'batch_id':batch.pk, 'role_complete':complete}
        for row in results:
            row['description'] = '角色：' + row['role']
        aggregate = await sync_to_async(UiExecutionRecord.objects.create)(test_case=case, executor=consumer.scope.get('user'), status=2 if success else 3, start_time=started, end_time=timezone.now(), step_results=results, error_message=None if success else summary['message'], duration=(timezone.now()-started).total_seconds(), environment={'role_name':'全部角色汇总','env_config_id':args.get('env_config_id'),'actuator_id':'recorder-browser','execution_request_id':args.get('execution_request_id'),'role_suite_batch_id':batch.pk,'role_complete':complete}, log=summary['message'])
        summary['execution_record_id'] = aggregate.pk
        await sync_to_async(UiTestCase.objects.filter(pk=case.pk).update)(status=2 if success else 3,result_data=summary,error_message=None if success else summary['message'])
        await sync_to_async(UiBatchExecutionRecord.objects.filter(pk=batch.pk).update)(status=2 if success else (3 if passed else 4),passed_cases=passed,failed_cases=len(results)-passed,end_time=timezone.now())
        from .socket_models import SocketDataModel, QueueModel, UiSocketEnum, ResponseCode, NoticeType
        await consumer.send_json(SocketDataModel(code=ResponseCode.SUCCESS,msg='role_result',user=user,is_notice=NoticeType.WEB,data=QueueModel(func_name=UiSocketEnum.CASE_RESULT,func_args=summary)))
        consumer._recorder_exec_task = None
