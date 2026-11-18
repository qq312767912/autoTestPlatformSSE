"""持久化脚本批次：用例数不等于浏览器槽位数，每次只派发一个执行单元。"""
import copy
import json
import time
import uuid
from urllib.parse import quote
from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import UiTestCase, UiBatchExecutionRecord, UiExecutionRecord, UiEnvironmentConfig
from .role_execution import validate_matrix, execution_roles

ACTIVE = (0, 1)


def project_access(user, pk, change=False):
    from knowledge_evolution.views import _ensure_project_member
    from projects.models import Project
    project = get_object_or_404(Project, pk=pk)
    _ensure_project_member(user, project.pk)
    permission = 'ui_automation.change_uitestcase' if change else 'ui_automation.view_uitestcase'
    if not user.has_perm(permission): raise PermissionDenied('缺少UI用例操作权限')
    return project


def selected_cases(project, ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(type(x) is not int or x <= 0 for x in ids):
        raise ValidationError('请选择1至100条UI用例')
    ids = list(dict.fromkeys(ids))
    rows = list(UiTestCase.objects.select_for_update().filter(project=project, pk__in=ids).prefetch_related('case_steps'))
    if len(rows) != len(ids): raise ValidationError('包含不存在或跨项目用例')
    by_id = {row.pk: row for row in rows}
    return [by_id[pk] for pk in ids]


def ensure_idle(project, ids):
    for other in UiBatchExecutionRecord.objects.filter(run_config__project=project.pk, status__in=ACTIVE):
        if set(ids) & set(other.run_config.get('case_ids', [])):
            raise ValidationError('所选用例已有排队或执行中的脚本批次')
    from knowledge_evolution.hub_assessment_models import SkillAgentTaskBinding
    for binding in SkillAgentTaskBinding.objects.filter(session__project=project, generation_run__status__in=['queued','running']):
        targets = {row['id'] for row in binding.generation_context.get('ui_cases', [])}
        targets |= {row.get('ui_test_case_id') for row in binding.generation_context.get('functional_cases', [])}
        if set(ids) & targets: raise ValidationError('所选用例仍在转换或自愈，请完成后执行')
    if UiTestCase.objects.filter(pk__in=ids, status=1).exists(): raise ValidationError('所选用例正在执行')


def batch_data(batch):
    return {'id':batch.pk,'task_id':batch.run_config.get('task_id'),'status':batch.status,'items':batch.run_items,'cancel_requested':batch.cancel_requested,
            'passed':batch.passed_cases,'failed':batch.failed_cases,'total':batch.total_cases,
            'heartbeat_at':batch.heartbeat_at,'finished_at':batch.end_time}


def publish_items(pk, items):
    UiBatchExecutionRecord.objects.filter(pk=pk).update(run_items=items,heartbeat_at=timezone.now(),
        passed_cases=sum(x['status']=='passed' for x in items),failed_cases=sum(x['status']=='failed' for x in items))


class UiScriptBatchViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def get_batch(self, request, pk):
        batch = get_object_or_404(UiBatchExecutionRecord, pk=pk, executor=request.user)
        project_access(request.user, batch.run_config.get('project'))
        return batch

    def retrieve(self, request, pk=None):
        return Response(batch_data(self.get_batch(request,pk)))

    def create(self, request):
        project = project_access(request.user, request.data.get('project'))
        env = get_object_or_404(UiEnvironmentConfig, pk=request.data.get('env_config_id'), project=project)
        actor = str(request.data.get('actuator_id') or 'recorder-browser')
        override = request.data.get('role_matrix')  # None沿用每条用例默认；数组为本次统一角色。
        if override is not None: override = validate_matrix(override,project.pk)
        with transaction.atomic():
            cases = selected_cases(project,request.data.get('ids'))
            ensure_idle(project,[case.pk for case in cases])
            snapshots = {}
            validated_profiles = {}
            for case in cases:
                if not case.case_steps.exists(): raise ValidationError(f'用例【{case.name}】缺少步骤')
                profile_key=json.dumps(case.role_matrix if override is None else override,sort_keys=True)
                if profile_key not in validated_profiles:
                    _, validated_profiles[profile_key] = execution_roles(case.pk,env.pk,request.user,override)
                roles = copy.deepcopy(validated_profiles[profile_key])
                if roles and actor != 'recorder-browser': raise ValidationError('角色遍历请使用录制器浏览器；独立执行器暂不支持角色展开')
                snapshots[str(case.pk)] = roles
            batch = UiBatchExecutionRecord.objects.create(name=f'脚本批量执行 · {len(cases)}条',executor=request.user,total_cases=len(cases),
                run_config={'project':project.pk,'base_url':env.base_url,'env_config_id':env.pk,'actuator_id':actor,'case_ids':[c.pk for c in cases], 'role_snapshots':snapshots},
                run_items=[{'id':c.pk,'name':c.name,'status':'queued'} for c in cases],heartbeat_at=timezone.now())
            def dispatch():
                from .tasks import run_ui_script_batch
                try:
                    task=run_ui_script_batch.delay(batch.pk)
                    UiBatchExecutionRecord.objects.filter(pk=batch.pk).update(run_config={**batch.run_config,'task_id':str(task.id)})
                except Exception:
                    UiBatchExecutionRecord.objects.filter(pk=batch.pk).update(status=4,end_time=timezone.now(),run_items=[{**x,'status':'failed','message':'后台任务派发失败，请重新创建批次'} for x in batch.run_items])
            transaction.on_commit(dispatch)
        return Response(batch_data(batch),status=202)

    @action(detail=False,methods=['post'],url_path='configure-roles')
    def configure_roles(self,request):
        project = project_access(request.user,request.data.get('project'),change=True)
        if 'role_matrix' not in request.data: raise ValidationError('缺少角色配置')
        roles = validate_matrix(request.data['role_matrix'],project.pk)
        with transaction.atomic():
            cases = selected_cases(project,request.data.get('ids'))
            ensure_idle(project,[c.pk for c in cases])
            UiTestCase.objects.filter(pk__in=[case.pk for case in cases]).update(role_matrix=roles,updated_at=timezone.now())
        return Response({'updated':len(cases),'role_matrix':roles})

    @action(detail=True,methods=['post'])
    def cancel(self,request,pk=None):
        batch = self.get_batch(request,pk)
        with transaction.atomic():
            batch=UiBatchExecutionRecord.objects.select_for_update().get(pk=batch.pk)
            if batch.status in ACTIVE:
                batch.cancel_requested=True
                if batch.status==0:
                    batch.status=4
                    batch.end_time=timezone.now()
                    batch.run_items=[{**item,'status':'cancelled','message':'批次已停止，未执行'} for item in batch.run_items]
                batch.save(update_fields=['cancel_requested','status','end_time','run_items'])
        return Response(batch_data(batch))


class WaitingForActuator(Exception): pass
class BatchCancelled(Exception): pass
class ExecutionTimeout(Exception): pass


def execute_one(batch, case_id, on_progress):
    import websocket
    from rest_framework_simplejwt.tokens import AccessToken
    from orchestrator_integration.builtin_tools.skill_tools import _resolve_skill_runtime_backend_url
    config = batch.run_config
    from django.contrib.auth import get_user_model
    actor = get_user_model().objects.get(pk=batch.executor_id)
    project_access(actor, config['project'])
    if not UiEnvironmentConfig.objects.filter(pk=config['env_config_id'],project_id=config['project'],base_url=config['base_url']).exists():
        raise ValidationError('执行环境已变化，请重新创建批次')
    request_id = uuid.uuid4().hex
    roles = config['role_snapshots'][str(case_id)]
    args = {'case_id':case_id,'executor_id':actor.pk,'executor_name':actor.username,'env_config_id':config['env_config_id'],'actuator_id':config['actuator_id'],
            'execution_request_id':request_id,'execution_role_matrix':roles}
    if config['actuator_id'] != 'recorder-browser':
        args['run_options']={'headless':True}
    base = _resolve_skill_runtime_backend_url().replace('https://','wss://').replace('http://','ws://')
    ws = websocket.create_connection(base+'/ws/ui/web/?background=1&token='+quote(str(AccessToken.for_user(actor))),timeout=5)
    try:
        ws.send(json.dumps({'data':{'func_name':'u_test_case','func_args':args}}))
        deadline = time.monotonic()+300*max(1,len(roles))
        stop_sent=False
        dispatched=False
        role_seen=False
        while time.monotonic()<deadline:
            batch.refresh_from_db(fields=['cancel_requested'])
            UiBatchExecutionRecord.objects.filter(pk=batch.pk).update(heartbeat_at=timezone.now())
            if batch.cancel_requested and not stop_sent and dispatched:
                ws.send(json.dumps({'data':{'func_name':'u_stop_execution','func_args':{'case_id':case_id,'actuator_id':config['actuator_id']}}}))
                stop_sent=True
                deadline=min(deadline,time.monotonic()+15)
            try: message=json.loads(ws.recv())
            except websocket.WebSocketTimeoutException: continue
            if not message: raise RuntimeError('执行连接已关闭')
            data=message.get('data') or {}; event=data.get('func_name'); result=data.get('func_args') or {}
            if event in ('u_test_case_ack','effective_runtime'):
                dispatched=True
                UiTestCase.objects.filter(pk=case_id).update(status=1)
                if not role_seen:on_progress({'status':'running'})
            if batch.cancel_requested and not dispatched:raise BatchCancelled('批次已停止')
            if event=='u_role_progress':
                role_seen=True
                UiTestCase.objects.filter(pk=case_id).update(status=1)
                on_progress(result)
            if message.get('code',200)>=400:
                text=str(result.get('error') or message.get('msg') or '执行器拒绝执行')
                if any(word in text for word in ['slot','不在线','在线执行器','无可用执行器']): raise WaitingForActuator(text)
                raise RuntimeError(text)
            if event not in ('u_case_result','case_result') or result.get('execution_request_id')!=request_id: continue
            record=UiExecutionRecord.objects.filter(test_case_id=case_id,executor=batch.executor,environment__execution_request_id=request_id).order_by('-pk').first()
            if not record or str((record.environment or {}).get('env_config_id'))!=str(config['env_config_id']):
                raise RuntimeError('未取得本轮执行记录或环境不一致')
            return record,result
        raise ExecutionTimeout('本条用例未在限时内返回执行结果，已发送停止请求；后续用例暂不派发')
    except Exception as exc:
        if locals().get('dispatched'):
            UiTestCase.objects.filter(pk=case_id,status=1).update(status=3,error_message='执行未完成：'+str(exc)[:300])
        raise
    finally:
        # 超时也发停止，防止客户端关闭后录制器继续占用资源。
        if 'record' not in locals() and locals().get('dispatched'):
            try: ws.send(json.dumps({'data':{'func_name':'u_stop_execution','func_args':{'case_id':case_id,'actuator_id':config['actuator_id']}}}))
            except Exception: pass
        ws.close()


def process_script_batch(pk):
    with transaction.atomic():
        batch=UiBatchExecutionRecord.objects.select_for_update().get(pk=pk)
        if batch.status!=0:return
        batch.status=1; batch.start_time=timezone.now();batch.heartbeat_at=timezone.now();batch.save(update_fields=['status','start_time','heartbeat_at'])
    items=copy.deepcopy(batch.run_items)
    unavailable=False
    try:
        for item in items:
            batch.refresh_from_db(fields=['cancel_requested'])
            if batch.cancel_requested:break
            item.update(status='running',message='正在执行')
            def progress(role):
                item['status']='running'
                item['current_role']=role.get('role')
                item['message']=f"角色 {role.get('index')}/{role.get('total')}：{role.get('role')}" if role.get('role') else '正在执行'
                publish_items(pk,items)
            publish_items(pk,items)
            waiting_until=time.monotonic()+900
            while True:
                try:
                    batch.refresh_from_db(fields=['cancel_requested'])
                    if batch.cancel_requested:raise BatchCancelled('批次已停止')
                    record,result=execute_one(batch,item['id'],progress)
                    record.batch=batch;record.save(update_fields=['batch'])
                    batch.refresh_from_db(fields=['cancel_requested'])
                    item.update(status='cancelled' if batch.cancel_requested else ('passed' if record.status==2 else 'failed'),record_id=record.pk,message=result.get('message') or record.error_message,role_results=result.get('role_results',[]))
                    break
                except WaitingForActuator as exc:
                    if time.monotonic()>=waiting_until:
                        item.update(status='failed',message='等待执行器空闲超时：'+str(exc));unavailable=True;break
                    item.update(status='waiting',message='等待匹配的执行器空闲，不占用额外槽位')
                    publish_items(pk,items)
                    time.sleep(2)
                except Exception as exc:
                    batch.refresh_from_db(fields=['cancel_requested'])
                    if isinstance(exc,ExecutionTimeout):unavailable=True
                    item.update(status='cancelled' if batch.cancel_requested or isinstance(exc,BatchCancelled) else 'failed',message=str(exc)[:500]);break
            publish_items(pk,items)
            if unavailable:break
    finally:
        for item in items:
            if item['status'] in ('queued','running','waiting'):item.update(status='cancelled',message='执行器持续不可用，后续未执行' if unavailable else '未执行，批次已结束或停止')
        passed=sum(x['status']=='passed' for x in items)
        UiBatchExecutionRecord.objects.filter(pk=pk).update(run_items=items,passed_cases=passed,failed_cases=sum(x['status']=='failed' for x in items),
            status=2 if passed==len(items) else (3 if passed else 4),end_time=timezone.now(),duration=(timezone.now()-batch.start_time).total_seconds(),heartbeat_at=timezone.now())
