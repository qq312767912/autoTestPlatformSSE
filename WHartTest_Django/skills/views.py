import logging
import uuid

from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from wharttest_django.viewsets import BaseModelViewSet
from projects.models import Project
from projects.roles import (
    IsProjectScoped, IsTestExecutor, IsTestLead,
    business_role, is_test_executor, is_test_lead, visible_project_ids,
)
from .exporter import SkillPackageExporter
from .models import Skill, SkillVersion
from .serializers import (
    SkillSerializer, SkillUploadSerializer, SkillGitImportSerializer,
    SkillListSerializer, SkillToggleSerializer, SkillZipUrlImportSerializer,
    SkillVersionSerializer, SkillVersionListSerializer,
    SkillPreflightSerializer, SkillCandidateCreateSerializer,
    SkillVersionDiffQuerySerializer, SkillQuarantineSerializer,
    SkillVersionValidateSerializer,
)
from .validation import SkillPackageValidationService
from .versions import SkillVersionService

logger = logging.getLogger(__name__)


def _validation_message(exc) -> str:
    """把 ValidationError 提取成一句可直接展示的中文提示。"""
    messages = getattr(exc, 'messages', None)
    if messages:
        return messages[0]
    return str(exc)


def _get_version_or_404(skill, version_id):
    """按版本 ID 取版本；ID 非法或不属于该 Skill 一律 404，不泄露存在性。"""
    try:
        uuid.UUID(str(version_id))
    except (ValueError, TypeError, AttributeError):
        raise Http404('版本不存在')
    return get_object_or_404(SkillVersion, id=version_id, skill=skill)


class SkillViewSet(BaseModelViewSet):
    """
    Skill 视图集

    支持：
    - GET /projects/{project_id}/skills/ - 列表
    - POST /projects/{project_id}/skills/upload/ - 上传
    - POST /projects/{project_id}/skills/import-git/ - 从 Git 导入
    - GET /projects/{project_id}/skills/{id}/ - 详情
    - PATCH /projects/{project_id}/skills/{id}/ - 更新（仅 is_active）
    - DELETE /projects/{project_id}/skills/{id}/ - 删除
    """

    parser_classes = [JSONParser, MultiPartParser, FormParser]

    #: 治理类动作（审批、激活、回滚、隔离）仅测试负责人可执行。
    LEAD_ONLY_ACTIONS = frozenset({
        'approve', 'reject', 'activate', 'rollback', 'quarantine',
        # 版本维度的治理动作（T07）：隔离是安全事件，必须由测试负责人发起。
        'quarantine_version',
    })
    #: 仅做配置返回或远程代理拉取、不读写 Skill 数据的动作，只要求登录。
    PUBLIC_ACTIONS = frozenset({'store_config', 'store_manifest', 'store_readme'})

    def get_queryset(self):
        """所有读写一律锁定 URL 中的项目，杜绝跨项目读取与 UUID 猜测。

        原实现返回 ``Skill.objects.all()``，任何登录用户都能列出、读取、修改
        和删除全平台的 Skill。这里改为强制按嵌套路由的 ``project_pk`` 过滤；
        非嵌套场景（权限类反查模型信息、schema 生成）退化为"仅当前用户可见
        项目"，绝不返回全量。
        """
        # ``active_version`` 一起预取：列表页要用它作为"展示版本"的优先项，
        # 不预取就会按 Skill 逐个查版本（N+1）。
        queryset = Skill.objects.select_related('project', 'creator', 'active_version')
        project_id = (getattr(self, 'kwargs', None) or {}).get('project_pk')
        if project_id:
            return queryset.filter(project_id=project_id)
        allowed_project_ids = visible_project_ids(getattr(getattr(self, 'request', None), 'user', None))
        if allowed_project_ids is None:
            # None 表示超级用户不受项目限制。
            return queryset
        return queryset.filter(project_id__in=allowed_project_ids)

    def get_permissions(self):
        action = getattr(self, 'action', None)
        # store_config / store_manifest / store_readme 仅做配置返回或远程代理拉取，不读写 Skill 数据。
        if action in self.PUBLIC_ACTIONS:
            return [IsAuthenticated()]
        # hub_access 只回答"调用者在本项目属于哪种业务角色"，供控制台做按钮门控，
        # 不读任何 Skill 数据，因此不绑定具体业务角色：它要求的只是"是本项目成员"。
        # 非成员应当拿到 403「无权访问该项目」，而不是被误报成"缺少执行人员权限"——
        # 后者的文案会让人以为"再申请一下权限就能进"，其实是压根不在这个项目里。
        if action == 'hub_access':
            return [IsAuthenticated(), IsProjectScoped()]
        # 这里刻意**不叠加** HasModelPermission（基础视图集的全局模型权限位）。
        #
        # Skill Hub 的授权维度是"项目内业务角色"（测试负责人 / 测试执行人员），粒度比
        # 全局模型权限更细：一名项目成员完全应当能上传与预检本项目的 Skill，但他通常
        # 并不持有全局的 skills.add_skill 权限位。叠加模型权限会让 T01 要求的
        # "测试执行人员可以上传、预检"直接失效——验收时表现为项目成员一律 403。
        #
        # 项目边界（是否本项目成员）与角色（是否测试负责人）由下面两个权限类负责。
        return [
            IsAuthenticated(),
            IsTestLead() if action in self.LEAD_ONLY_ACTIONS else IsTestExecutor(),
        ]

    def get_serializer_class(self):
        # 按动作切换序列化器，确保每个接口只暴露必要字段与校验规则。
        if self.action == 'list':
            return SkillListSerializer
        if self.action == 'upload':
            return SkillUploadSerializer
        if self.action == 'preflight':
            return SkillPreflightSerializer
        if self.action == 'create_candidate':
            return SkillCandidateCreateSerializer
        if self.action == 'list_versions':
            return SkillVersionListSerializer
        if self.action in ('version_detail', 'download_version', 'quarantine_version'):
            return SkillVersionSerializer
        if self.action == 'validate_version':
            return SkillVersionValidateSerializer
        if self.action == 'version_diff':
            return SkillVersionDiffQuerySerializer
        if self.action == 'import_git':
            return SkillGitImportSerializer
        if self.action == 'import_zip_url':
            return SkillZipUrlImportSerializer
        if self.action == 'partial_update':
            return SkillToggleSerializer
        return SkillSerializer

    def get_project(self):
        # 解析嵌套路由中的项目 ID；不存在时返回 404。
        project_id = self.kwargs.get('project_pk')
        return get_object_or_404(Project, id=project_id)

    def list(self, request, *args, **kwargs):
        """获取项目下的所有 Skills

        一并给出每个 Skill 的**展示版本**元数据（来源 / 声明阶段 / 版本号），
        供 Skill Hub 的筛选器与卡片标签使用；版本一次批量取齐，不按 Skill 逐个查。
        """
        queryset = self.get_queryset()
        serializer = self.get_serializer(
            queryset, many=True, context={'versions': self._display_versions(queryset)},
        )
        return Response({
            'code': 200,
            'message': '获取成功',
            'data': serializer.data
        })

    @staticmethod
    def _display_versions(skills):
        """列表页的"展示版本"：活跃版本优先，其余取最新一版。

        只用来给筛选器与卡片提供**描述性元数据**（来源 / 声明阶段 / 版本号），
        **不是可用性判据** —— 可用性判据只有一处（``SkillRuntimeResolver``）。

        刻意**包含不可运行**的版本：存量迁移那批（``package_path`` 为空、跑不起来）
        恰恰是用户最需要"一眼看出它没包"的对象，若按可用性过滤，它们会显示成
        "无来源 / 无版本"，反而把问题藏起来。
        """
        skills = list(skills)
        if not skills:
            return {}

        versions = {}
        # 一次查询取回全部版本，按 (skill, -created_at) 排序后在 Python 里取每 Skill 首条。
        # 只取展示要用的列：``validation_report`` 等大字段不读。
        for version in (
            SkillVersion.objects
            .filter(skill_id__in=[skill.pk for skill in skills])
            .only('id', 'skill_id', 'version', 'source_type', 'manifest', 'created_at')
            .order_by('skill_id', '-created_at')
        ):
            versions.setdefault(version.skill_id, version)

        # 活跃版本优先：它代表"这个 Skill 现在认定的生产版本"。
        for skill in skills:
            if skill.active_version is not None:
                versions[skill.pk] = skill.active_version
        return versions

    def retrieve(self, request, *args, **kwargs):
        """获取 Skill 详情"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response({
            'code': 200,
            'message': '获取成功',
            'data': serializer.data
        })

    @action(detail=False, methods=['get'], url_path='hub-access')
    def hub_access(self, request, *args, **kwargs):
        """返回调用者在本项目内的业务角色（控制台自服务的角色真值）。

        T18 的控制台需要知道"我是测试负责人还是测试执行人员"来决定按钮的呈现与禁用。
        原实现让前端读 ``/projects/{id}/members/`` 反查自己那一行，有两个问题：

        ① **权限口径不对**。``members`` 是成员管理接口，要求
           ``projects.view_projectmember`` 这一全局模型权限位；而业务角色的真值本来
           就在 ``projects.roles``，与全局模型权限无关。于是一个实打实的项目成员
           （他持有 ``member`` 角色，本该能进控制台）会因为缺权限位拿到 403，
           前端据此把他判成"非本项目成员"——T18 验收的"只显示测试负责人和测试
           执行人员"就此落空。
        ② **取数范围过大**。为了知道自己是谁，前端要把整个项目的成员列表拉回来。

        这里改为直接返回调用者自己的角色：不需要额外权限位，也不返回其他成员。
        """
        project = self.get_project()
        return Response({
            'code': 200,
            'message': '获取成功',
            'data': {
                'project_id': project.id,
                'business_role': business_role(request.user, project.id),
                'is_test_lead': is_test_lead(request.user, project.id),
                'is_test_executor': is_test_executor(request.user, project.id),
            },
        })

    def partial_update(self, request, *args, **kwargs):
        """更新 Skill（仅支持 is_active）"""
        instance = self.get_object()
        # 条件：缺少 is_active；动作：拒绝；结果：避免误改其它只读字段。
        if 'is_active' not in request.data:
            return Response({
                'code': 400,
                'message': '缺少 is_active 字段',
                'data': None
            }, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response({
            'code': 200,
            'message': '更新成功',
            'data': SkillSerializer(instance).data
        })

    def destroy(self, request, *args, **kwargs):
        """删除 Skill"""
        instance = self.get_object()
        name = instance.name
        # 删除数据库记录会触发模型 delete() 中的文件清理逻辑。
        instance.delete()
        return Response({
            'code': 200,
            'message': f"Skill '{name}' 已删除"
        })

    @action(detail=False, methods=['post'], url_path='upload')
    def upload(self, request, *args, **kwargs):
        """
        上传 Skill zip 文件

        请求：multipart/form-data, file 字段为 zip 文件
        """
        serializer = SkillUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        zip_file = serializer.validated_data['file']
        api_key = (serializer.validated_data.get('api_key') or '').strip() or None
        project = self.get_project()

        try:
            # 条件：上传包合法；动作：解压并创建一个或多个 Skill；结果：返回新 Skills 的完整信息。
            skills = Skill.create_from_zip(
                zip_file=zip_file,
                project=project,
                creator=request.user,
                api_key=api_key,
            )
            names = ', '.join(s.name for s in skills)
            return Response({
                'code': 201,
                'message': f"成功上传 {len(skills)} 个 Skill: {names}",
                'data': SkillSerializer(skills, many=True).data
            }, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            # 参数/文件内容校验失败返回 400，便于前端直接提示用户修正输入。
            msg = _validation_message(e)
            return Response({
                'code': 400,
                'message': msg,
                'data': None
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            # 未预期异常记录堆栈并返回 500，避免将内部错误细节暴露给客户端。
            logger.exception("Skill upload failed: %s", e)
            return Response({
                'code': 500,
                'message': '上传失败',
                'data': None
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='import-git')
    def import_git(self, request, *args, **kwargs):
        """
        从 Git 仓库导入 Skills（支持仓库包含多个 Skill）

        请求：application/json，包含 git_url（必填）和 branch（可选）
        """
        serializer = SkillGitImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        git_url = serializer.validated_data['git_url']
        branch = serializer.validated_data.get('branch', 'main')
        api_key = (serializer.validated_data.get('api_key') or '').strip() or None
        project = self.get_project()

        try:
            skills = Skill.create_from_git(
                git_url=git_url,
                branch=branch,
                project=project,
                creator=request.user,
                api_key=api_key,
            )
            names = ', '.join(s.name for s in skills)
            return Response({
                'code': 201,
                'message': f"成功导入 {len(skills)} 个 Skill: {names}",
                'data': SkillSerializer(skills, many=True).data
            }, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            msg = _validation_message(e)
            return Response({
                'code': 400,
                'message': msg,
                'data': None
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Skill git import failed: %s", e)
            return Response({
                'code': 500,
                'message': '导入失败',
                'data': None
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='content')
    def content(self, request, *args, **kwargs):
        """获取 Skill 的 SKILL.md 内容"""
        instance = self.get_object()
        return Response({
            'code': 200,
            'message': '获取成功',
            'data': {
                'name': instance.name,
                'description': instance.description,
                'content': instance.skill_content
            }
        })

    @action(detail=False, methods=['post'], url_path='import-zip-url')
    def import_zip_url(self, request, *args, **kwargs):
        """
        从远程 zip URL 导入 Skill（用于 Skill 商店）

        请求：application/json，包含 zip_url（必填）和 sha256（可选）
        """
        serializer = SkillZipUrlImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        zip_url = serializer.validated_data['zip_url']
        sha256 = serializer.validated_data.get('sha256') or None
        api_key = (serializer.validated_data.get('api_key') or '').strip() or None
        project = self.get_project()

        try:
            skills = Skill.create_from_zip_url(
                zip_url=zip_url,
                project=project,
                creator=request.user,
                expected_sha256=sha256,
                api_key=api_key,
            )
            names = ', '.join(s.name for s in skills)
            return Response({
                'code': 201,
                'message': f"成功导入 {len(skills)} 个 Skill: {names}",
                'data': SkillSerializer(skills, many=True).data
            }, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            msg = _validation_message(e)
            return Response({
                'code': 400,
                'message': msg,
                'data': None
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Skill zip-url import failed: %s", e)
            return Response({
                'code': 500,
                'message': '导入失败',
                'data': None
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='store-config')
    def store_config(self, request, *args, **kwargs):
        """
        获取 Skill 商店配置（默认源、是否允许自定义源）

        前端启动时拉取一次，用于决定默认商店源 URL
        """
        from django.conf import settings as dj_settings
        return Response({
            'code': 200,
            'message': '获取成功',
            'data': {
                'default_source': getattr(dj_settings, 'SKILL_STORE_DEFAULT_SOURCE', ''),
                'default_source_name': getattr(dj_settings, 'SKILL_STORE_DEFAULT_SOURCE_NAME', ''),
                'allow_custom_source': bool(getattr(dj_settings, 'SKILL_STORE_ALLOW_CUSTOM_SOURCE', True)),
                'max_zip_size': int(getattr(dj_settings, 'SKILL_STORE_MAX_ZIP_SIZE', 10 * 1024 * 1024)),
            }
        })

    @staticmethod
    def _proxy_fetch_text(url: str, max_size: int, timeout: float = 15.0) -> str:
        """代理拉取远程文本资源（含 SSRF 校验、大小限制、超时）。"""
        try:
            import httpx
        except ImportError:
            raise ValidationError('服务器未安装 httpx')

        url = Skill._validate_remote_url(url)

        chunks: list[bytes] = []
        total = 0
        try:
            with httpx.stream(
                'GET', url,
                timeout=httpx.Timeout(timeout, connect=10.0),
                follow_redirects=True,
            ) as resp:
                if resp.status_code != 200:
                    raise ValidationError(f'上游响应异常：HTTP {resp.status_code}')
                # Content-Length 在头部时先做预判，避免无谓下载。
                cl = resp.headers.get('content-length')
                if cl and cl.isdigit() and int(cl) > max_size:
                    raise ValidationError(f'文件过大，超过 {max_size // 1024}KB 限制')
                for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_size:
                        raise ValidationError(f'文件过大，超过 {max_size // 1024}KB 限制')
                    chunks.append(chunk)
        except httpx.TimeoutException:
            raise ValidationError(f'上游请求超时（{int(timeout)}秒）')
        except httpx.RequestError as e:
            raise ValidationError(f'上游请求失败：{e}')

        raw = b''.join(chunks)
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ValidationError('上游响应不是 UTF-8 文本')

    @action(detail=False, methods=['get'], url_path='store-manifest')
    def store_manifest(self, request, *args, **kwargs):
        """
        代理拉取商店 manifest.json，绕过浏览器 CORS 限制

        请求：?url=https://.../manifest.json
        """
        import json

        url = (request.query_params.get('url') or '').strip()
        if not url:
            return Response({
                'code': 400, 'message': '缺少 url 参数', 'data': None
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            text = self._proxy_fetch_text(url, max_size=1 * 1024 * 1024)
        except ValidationError as e:
            msg = _validation_message(e)
            return Response({
                'code': 400, 'message': msg, 'data': None
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return Response({
                'code': 400, 'message': f'manifest.json 解析失败：{e}', 'data': None
            }, status=status.HTTP_400_BAD_REQUEST)

        if not isinstance(data, dict) or not isinstance(data.get('skills'), list):
            return Response({
                'code': 400, 'message': 'manifest.json 必须是包含 skills 数组的对象', 'data': None
            }, status=status.HTTP_400_BAD_REQUEST)

        return Response({'code': 200, 'message': '获取成功', 'data': data})

    @action(detail=False, methods=['get'], url_path='store-readme')
    def store_readme(self, request, *args, **kwargs):
        """
        代理拉取商店 README 文本，绕过浏览器 CORS 限制

        请求：?url=https://.../README.md
        """
        url = (request.query_params.get('url') or '').strip()
        if not url:
            return Response({
                'code': 400, 'message': '缺少 url 参数', 'data': None
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            content = self._proxy_fetch_text(url, max_size=1 * 1024 * 1024)
        except ValidationError as e:
            msg = _validation_message(e)
            return Response({
                'code': 400, 'message': msg, 'data': None
            }, status=status.HTTP_400_BAD_REQUEST)

        return Response({'code': 200, 'message': '获取成功', 'data': {'content': content}})

    # ------------------------------------------------------------------
    # Skill 版本（T06 / T07）
    #
    # 路由要点：这些 action 都是 detail=True，URL 形如
    # ``skills/{skill_id}/versions/...``。不能把集合入口做成
    # ``skills/versions/``——那会被 DRF 的详情路由 ``skills/{pk}/`` 抢先匹配，
    # 于是把 "versions" 当成 Skill 主键去查，得到 404。
    # ------------------------------------------------------------------

    @action(detail=False, methods=['post'], url_path='preflight')
    def preflight(self, request, *args, **kwargs):
        """上传包**预检**：只校验并返回报告与短期令牌，不写正式版本库（R1 / T05）。

        两阶段上传的第一步。暂存区在 ``MEDIA_ROOT/.skill-preflight`` 下，令牌带
        过期时间并与包哈希绑定；预检未通过的包会立即从暂存区删掉。
        """
        serializer = SkillPreflightSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # 项目必须先解析出来：即使权限类放行，也要确保 project_pk 指向真实项目。
        self.get_project()

        upload = serializer.validated_data['file']
        result = SkillPackageValidationService.preflight(
            zip_file=upload, filename=getattr(upload, 'name', '') or '',
        )
        payload = result.as_dict()
        if not result.report.ok:
            return Response(
                {'code': 400, 'message': '包校验未通过', 'data': payload},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({'code': 200, 'message': '预检通过', 'data': payload})

    @action(detail=False, methods=['post'], url_path='candidates')
    def create_candidate(self, request, *args, **kwargs):
        """用预检令牌创建候选版本（两阶段上传的第二步）。

        新建版本一律 ``draft``：候选不可能绕过评测与负责人审批直接生效（R7）。
        同一 Skill 下内容相同的包由服务层幂等返回已有版本，不会重复落库。
        """
        serializer = SkillCandidateCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = self.get_project()
        data = serializer.validated_data

        try:
            skill, version = SkillVersionService.create_candidate_from_preflight(
                token=data['token'], project=project, actor=request.user,
                change_reason=data.get('change_reason', ''),
                expected_benefit=data.get('expected_benefit', ''),
                impact_scope=data.get('impact_scope', ''),
                api_key=data.get('api_key') or None,
            )
        except ValidationError as e:
            return Response(
                {'code': 400, 'message': _validation_message(e), 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            'code': 201,
            'message': f"候选版本 {version.version} 已创建（草稿，待评测与审批）",
            'data': {
                'skill': SkillSerializer(skill).data,
                'version': SkillVersionSerializer(version).data,
            },
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='versions')
    def list_versions(self, request, *args, **kwargs):
        """列出该 Skill 的全部版本（新→旧）。"""
        skill = self.get_object()
        versions = skill.versions.select_related('release', 'created_by').order_by('-created_at')
        return Response({
            'code': 200,
            'message': '获取成功',
            'data': SkillVersionSerializer(versions, many=True).data,
        })

    @action(detail=True, methods=['get'], url_path=r'versions/(?P<version_id>[^/.]+)')
    def version_detail(self, request, version_id=None, *args, **kwargs):
        """版本详情（含 manifest 与校验报告）。"""
        skill = self.get_object()
        version = _get_version_or_404(skill, version_id)
        return Response({
            'code': 200,
            'message': '获取成功',
            'data': SkillVersionSerializer(version).data,
        })

    @action(detail=True, methods=['get'], url_path=r'versions/(?P<version_id>[^/.]+)/diff')
    def version_diff(self, request, version_id=None, *args, **kwargs):
        """版本差异；``?base=<version_id>`` 缺省时与该版本的父版本比较。"""
        skill = self.get_object()
        candidate = _get_version_or_404(skill, version_id)
        query = SkillVersionDiffQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        base_id = query.validated_data.get('base')
        base = _get_version_or_404(skill, base_id) if base_id else candidate.previous_version

        try:
            diff = SkillVersionService.diff(base, candidate)
        except ValidationError as e:
            return Response(
                {'code': 400, 'message': _validation_message(e), 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({'code': 200, 'message': '获取成功', 'data': diff})

    @action(detail=True, methods=['get'], url_path=r'versions/(?P<version_id>[^/.]+)/download')
    def download_version(self, request, version_id=None, *args, **kwargs):
        """下载确定性脱敏包（T07）。

        相同版本重复导出字节与 SHA-256 一致；导出前会做二次敏感扫描，命中即
        把该版本转为 ``quarantined`` 并拒绝下载。导出动作写 ``download`` 审计。
        """
        skill = self.get_object()
        version = _get_version_or_404(skill, version_id)
        try:
            return SkillPackageExporter.export_response(version, actor=request.user)
        except ValidationError as e:
            return Response(
                {'code': 400, 'message': _validation_message(e), 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=['post'], url_path=r'versions/(?P<version_id>[^/.]+)/quarantine')
    def quarantine_version(self, request, version_id=None, *args, **kwargs):
        """隔离版本：停止运行时加载并留痕（仅测试负责人可执行）。"""
        skill = self.get_object()
        version = _get_version_or_404(skill, version_id)
        serializer = SkillQuarantineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            SkillVersionService.quarantine(
                version, actor=request.user, reason=serializer.validated_data['reason'],
            )
        except ValidationError as e:
            return Response(
                {'code': 400, 'message': _validation_message(e), 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )

        version.refresh_from_db()
        return Response({
            'code': 200,
            'message': f"版本 {version.version} 已隔离",
            'data': SkillVersionSerializer(version).data,
        })

    @action(detail=True, methods=['post'], url_path=r'versions/(?P<version_id>[^/.]+)/validate')
    def validate_version(self, request, version_id=None, *args, **kwargs):
        """提交静态校验：把草稿候选推进到影子验证（可进入评测）。

        执行人员即可发起：校验属于验评环节，按设计第 10 节"上传和验评允许测试
        执行人员"。返回体同时给出版本快照与完整校验报告——控制台要当场展示
        "哪一项没过"，而不是只弹一句红字。
        """
        skill = self.get_object()
        version = _get_version_or_404(skill, version_id)
        serializer = SkillVersionValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            report, version = SkillVersionService.submit_for_validation(
                version, actor=request.user,
                reason=serializer.validated_data.get('reason', ''),
            )
        except ValidationError as e:
            return Response(
                {'code': 400, 'message': _validation_message(e), 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )

        version.refresh_from_db()
        return Response({
            'code': 200,
            'message': f"版本 {version.version} 静态校验通过，已进入影子验证",
            'data': {
                'version': SkillVersionSerializer(version).data,
                'report': report.as_dict(),
            },
        })
