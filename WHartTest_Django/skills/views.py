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
    IsProjectScoped, IsTestExecutor, IsTestExecutorAnywhere, IsTestLead,
    IsTestLeadAnywhere, business_role, is_test_executor, is_test_lead,
    is_test_lead_anywhere, visible_project_ids,
)
from .canonical import canonical_skills
from .exporter import SkillPackageExporter
from .models import Skill, SkillVersion
from .serializers import (
    SkillSerializer, SkillUploadSerializer, SkillGitImportSerializer,
    SkillListSerializer, SkillToggleSerializer, SkillZipUrlImportSerializer,
    SkillVersionSerializer, SkillVersionListSerializer,
    SkillPreflightSerializer, SkillCandidateCreateSerializer,
    SkillVersionDiffQuerySerializer, SkillQuarantineSerializer,
    SkillStageBindingSerializer,
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
    - GET /projects/{project_id}/skills/ - 列表（**公共目录**：不按项目过滤，同名归并成一条正本）
    - POST /projects/{project_id}/skills/upload/ - 上传（新 Skill 记在该项目名下 = 上传出处）
    - POST /projects/{project_id}/skills/import-git/ - 从 Git 导入（同上）
    - GET /projects/{project_id}/skills/{id}/ - 详情（公共可读）
    - PATCH /projects/{project_id}/skills/{id}/ - 更新（仅 is_active；超管 / 任一项目测试负责人）
    - DELETE /projects/{project_id}/skills/{id}/ - 删除（超管 / 任一项目测试负责人）
    - POST /projects/{project_id}/skills/{id}/stage/ - 补填能力阶段（超管 / 任一项目测试负责人）

    URL 里的嵌套 ``project_pk`` **不是访问边界**（2026-10-02 修订）：Skill Hub 是平台级
    公共目录，条目不属于任何单一项目，所以读与写都按角色判定，只有"创建类"动作
    （upload / preflight / candidates / import-*）把它当"新 Skill 记在哪个项目名下"
    的上传出处。

    ⚠️ 这一条改过一次：读侧先公共化、写侧仍锚定项目时，列表能返回全平台的卡，
    但卡片上的启停 / 查看内容 / 版本列表按 URL 项目过滤 → 归属项目 ≠ 当前项目的卡
    一律 404（实测项目 3 的 URL 下 20/20 张全死）。**列表能看到却点不动，比看不到更糟。**
    """

    parser_classes = [JSONParser, MultiPartParser, FormParser]

    #: 治理类动作（审批、激活、回滚、隔离）仅测试负责人可执行。
    LEAD_ONLY_ACTIONS = frozenset({
        'approve', 'reject', 'activate', 'rollback', 'quarantine',
        # 版本维度的治理动作（T07）：隔离是安全事件，必须由测试负责人发起。
        'quarantine_version',
        # 2026-10-02 追加：启停与删除作用于**公共目录的条目**，一次动作会影响
        # 所有项目看到的目录，所以从"执行人员"提到"负责人"。
        'partial_update', 'destroy',
    })
    #: 仅做配置返回或远程代理拉取、不读写 Skill 数据的动作，只要求登录。
    PUBLIC_ACTIONS = frozenset({'store_config', 'store_manifest', 'store_readme'})
    #: 读侧动作：Skill Hub 是公共目录，任何登录用户看到的都是同一份内容。
    #: 去掉这里的项目过滤 = "每个项目看到的 Skill Hub 是一样的"，这是用户明确要求的口径。
    PUBLIC_READ_ACTIONS = frozenset({'list', 'retrieve'})

    # ------------------------------------------------------------------
    # 公共目录条目的动作分组（2026-10-02 修订：**读写都公共，权限改由角色把关**）
    #
    # 背景：读侧公共化之后，列表给的是全平台 20 张卡（同名归并成一条正本），
    # 但卡片上的启停 / 查看内容 / 版本列表仍按 URL 项目过滤 → 只要这张卡的归属
    # 项目 ≠ 当前项目就一律 404（实测：URL 项目 1 下 5/20 张死、项目 7 下 15/20、
    # 项目 3 下 20/20 全死）。列表能看到的卡片却点不动，是比"看不到"更糟的状态。
    #
    # 结论：公共目录的条目不属于任何单一项目，"能不能操作它"改按**角色**判定
    # （在某个项目里有没有这个角色），不再按 URL 里的那个项目判定。
    # URL 里的 project_pk 只在**创建**类动作里仍是锚点（上传/预检/候选/导入把新
    # Skill 记在哪个项目名下），它是"上传出处"，不是访问边界。
    # ------------------------------------------------------------------

    #: 看目录内容与版本史 = 读。与 list/retrieve 同等对待，不再要求项目成员身份。
    GLOBAL_READ_ACTIONS = frozenset({
        'content', 'list_versions', 'version_detail', 'version_diff',
    })
    #: 比读取重、但不是治理：导出包、重跑静态校验。
    GLOBAL_EXECUTOR_ACTIONS = frozenset({'download_version', 'validate_version'})
    #: 会改变目录状态的：补填阶段、启停、删除、隔离版本。权限收敛到负责人。
    GLOBAL_LEAD_ACTIONS = frozenset({
        'bind_stage', 'partial_update', 'destroy', 'quarantine_version',
        'generate_metadata',
    })

    #: 需要跨项目取对象的动作：作用于**公共池里的一条 Skill**，而不是"URL 那个项目
    #: 名下的 Skill"。所以对象查询不能加项目过滤，否则 A 项目的负责人改不到
    #: A 项目自己上传、但正本落在别的项目名下的那份（同名归并后这是常态）。
    #: 权限由上面三组各自的权限类把关，不靠项目过滤兜底。
    GLOBAL_LOOKUP_ACTIONS = (
        PUBLIC_READ_ACTIONS | GLOBAL_READ_ACTIONS
        | GLOBAL_EXECUTOR_ACTIONS | GLOBAL_LEAD_ACTIONS
    )

    def get_queryset(self):
        """公共目录条目不按项目过滤；只有"创建类"动作仍锚定 URL 项目。

        - ``GLOBAL_LOOKUP_ACTIONS``（详情、内容、版本史、启停、删除、阶段补填…）：
          返回全平台 Skill。Skill Hub 是平台级公共目录，一条目录项不属于任何单一
          项目，所以取对象时不能按 URL 的项目过滤。
        - 其余动作（``upload`` / ``preflight`` / ``create_candidate`` /
          ``import_git`` / ``import_zip_url``）：仍按嵌套路由的 ``project_pk`` 解析
          目标项目。这里 ``project_pk`` 是"新 Skill 记在哪个项目名下"的**上传出处**，
          与访问边界无关。
          非嵌套场景（权限类反查模型信息、schema 生成）退化为"仅当前用户可见项目"。

        ⚠️ 判据是**动作名**，不是"有没有嵌套 project_pk"——所有路由都嵌套在
        ``/projects/{project_pk}/`` 下，用后者会让这条分支永不生效。

        ``active_version`` 一起预取：列表页要用它作为"展示版本"的优先项，
        不预取就会按 Skill 逐个查版本（N+1）。
        """
        queryset = Skill.objects.select_related('project', 'creator', 'active_version')
        if getattr(self, 'action', None) in self.GLOBAL_LOOKUP_ACTIONS:
            return queryset
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
        # 读侧公共：Skill Hub 的目录内容对所有项目一致，不再要求"是本项目成员"。
        # 看内容与版本史（content / versions / diff）同属读取，一并只要求登录。
        if action in self.PUBLIC_READ_ACTIONS or action in self.GLOBAL_READ_ACTIONS:
            return [IsAuthenticated()]
        # 目录条目的执行级动作（导出包、重校验）：不绑项目，要求"在任一项目里有
        # 执行人员角色"。注意这不是"所有登录用户"——公共可见不等于公共可动。
        if action in self.GLOBAL_EXECUTOR_ACTIONS:
            return [IsAuthenticated(), IsTestExecutorAnywhere()]
        # 目录条目的治理动作（补填阶段 / 启停 / 删除 / 隔离版本）：不绑项目，
        # 权限收敛到"超管或任一项目测试负责人"。
        if action in self.GLOBAL_LEAD_ACTIONS:
            return [IsAuthenticated(), IsTestLeadAnywhere()]
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
        if self.action == 'bind_stage':
            return SkillStageBindingSerializer
        return SkillSerializer

    def get_project(self):
        # 解析嵌套路由中的项目 ID；不存在时返回 404。
        project_id = self.kwargs.get('project_pk')
        return get_object_or_404(Project, id=project_id)

    def list(self, request, *args, **kwargs):
        """获取公共 Skill 目录

        **不按项目过滤**：Skill Hub 是公共目录，任何项目进来看到的是同一份内容。

        同名副本归并成一条「正本」展示（存量迁移与商店安装会按项目各落一份，实测
        34 条 Skill 只有 20 个名字），并把副本数一并返回——多重性要显式给出，而不是
        藏起来。归并规则与"为什么不直接合并数据"见 ``skills.canonical``。

        一并给出每个 Skill 的**展示版本**元数据（来源 / 声明阶段 / 版本号），
        供筛选器与卡片标签使用；版本一次批量取齐，不按 Skill 逐个查。
        """
        skills, copies = canonical_skills(self.get_queryset())
        from django.db.models import Count, Q
        version_stats = {
            row['skill_id']: row
            for row in SkillVersion.objects.filter(skill_id__in=[skill.pk for skill in skills])
            .values('skill_id')
            .annotate(total=Count('id'), evolved=Count('id', filter=Q(source_type='evolution')))
        }
        serializer = self.get_serializer(
            skills, many=True, context={
                'versions': self._display_versions(skills),
                'copies': copies,
                'version_counts': {key: value['total'] for key, value in version_stats.items()},
                'evolved_skill_ids': {key for key, value in version_stats.items() if value['evolved']},
            },
        )
        # 随信封带回"调用者能不能管这个公共目录"和可绑定的阶段清单：
        # 放在信封里而不是每行——它与具体哪条 Skill 无关（同名的正本可能落在别的
        # 项目名下，按 URL 项目判角色会判错）。可绑定阶段复用后端能力注册表的真值，
        # 免得前端再抄一份阶段清单、两边慢慢漂移。
        from knowledge_evolution.capability_registry import (
            BUSINESS_CAPABILITY_STAGES, STAGE_LABELS,
        )
        from projects.roles import is_test_lead_anywhere

        # 一条目录项的"可管"= 能补填阶段、能启停、能删除、能隔离版本，四者同一门槛。
        # 前端靠它决定启停开关与删除按钮是否可用 —— 与后端 403 同源，避免出现
        # "按钮在这、点了必然报错"（2026-10-02 的 404 回归就是这么来的）。
        can_manage = is_test_lead_anywhere(getattr(request, 'user', None))
        return Response({
            'code': 200,
            'message': '获取成功',
            'data': serializer.data,
            'meta': {
                'can_bind_stage': can_manage,
                'can_manage': can_manage,
                'stage_options': [
                    {'value': stage, 'label': STAGE_LABELS.get(stage, stage)}
                    for stage in BUSINESS_CAPABILITY_STAGES
                ],
            },
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

    @action(detail=True, methods=['post'], url_path='stage')
    def bind_stage(self, request, *args, **kwargs):
        """补填 / 撤销 Skill 的能力阶段（Skill Hub 上「阶段未声明」的补救入口）。

        为什么阶段只能记在 Skill 上：存量迁移来的包 manifest 里没有 ``stage``，
        而版本包是**不可变产物**——改写 manifest 会让包哈希对不上（``package_tampered``），
        自进化会在"基线包被篡改"处中止。所以补填走 ``Skill.declared_stage``：
        解析时版本 manifest 声明**优先**，它只作回落（见 ``SkillListSerializer._stage_of``）。

        也刻意**不写** ``Skill.capability``：那是能力注册表（kind / evaluation_mode /
        gate_rules / active_release），拿它当一个"阶段标签"用会扭曲语义。

        权限 = ``IsTestLeadAnywhere``（平台超管，或在任一项目里是测试负责人）。
        对象查询走全局（见 ``GLOBAL_LOOKUP_ACTIONS``）：同名的正本可能落在别的
        项目名下，按 URL 项目过滤会让 A 项目的负责人改不到自己上传的那份。

        副作用：无。不改版本包、不改发布单元，对飞轮与自进化链路零影响。
        """
        skill = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stage = serializer.validated_data['stage']
        description = serializer.validated_data.get('description')
        skill.declared_stage = stage
        update_fields = ['declared_stage', 'updated_at']
        if description is not None:
            skill.description = description.strip()
            update_fields.append('description')
        skill.save(update_fields=update_fields)
        logger.info(
            "Skill 阶段声明已更新：skill=%s name=%s stage=%r by=%s",
            skill.pk, skill.name, stage, getattr(request.user, 'username', None),
        )
        return Response({
            'code': 200,
            'message': '已更新阶段声明' if stage else '已撤销阶段声明',
            'data': {'id': skill.pk, 'name': skill.name, 'declared_stage': skill.declared_stage},
        })

    @action(detail=False, methods=['post'], url_path='suggest-metadata')
    def suggest_metadata(self, request, *args, **kwargs):
        """导入前生成分类与短简介；只预览，不入库。"""
        import tempfile
        from pathlib import Path
        from .metadata_generation import generate_skill_metadata
        from .validation import safe_extract_zip

        name = str(request.data.get('name') or '').strip()
        content = str(request.data.get('content') or '').strip()
        git_url = str(request.data.get('git_url') or '').strip()
        branch = str(request.data.get('branch') or 'main').strip() or 'main'
        upload = request.FILES.get('file')

        try:
            if upload is not None:
                with tempfile.TemporaryDirectory() as temp_dir:
                    safe_extract_zip(upload, temp_dir)
                    skill_files = list(Path(temp_dir).rglob('SKILL.md'))
                    if not skill_files:
                        raise ValidationError('zip 文件中未找到 SKILL.md')
                    content = '\n\n'.join(path.read_text(encoding='utf-8') for path in skill_files[:5])
                    name = name or upload.name
            elif git_url:
                with tempfile.TemporaryDirectory() as temp_dir:
                    Skill._clone_repo(git_url, branch, temp_dir)
                    skill_dirs = Skill._find_skill_dirs(temp_dir)
                    if not skill_dirs:
                        raise ValidationError('仓库中未找到 SKILL.md')
                    files = [Path(path) / 'SKILL.md' for path in skill_dirs[:5]]
                    content = '\n\n'.join(path.read_text(encoding='utf-8') for path in files)
                    name = name or git_url.rsplit('/', 1)[-1]
            if not content:
                raise ValidationError('缺少可用于生成元数据的 Skill 内容')
            suggestion = generate_skill_metadata(name=name or 'Skill', content=content)
            return Response({'code': 200, 'message': '已生成，请人工确认', 'data': suggestion})
        except ValidationError as exc:
            return Response(
                {'code': 400, 'message': _validation_message(exc), 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=['post'], url_path='generate-metadata')
    def generate_metadata(self, request, *args, **kwargs):
        """为存量 Skill 生成建议；人工确认前不修改记录。"""
        from .metadata_generation import generate_skill_metadata

        skill = self.get_object()
        try:
            suggestion = generate_skill_metadata(
                name=skill.name,
                content=skill.skill_content or skill.description,
            )
            return Response({'code': 200, 'message': '已生成，请人工确认', 'data': suggestion})
        except ValidationError as exc:
            return Response(
                {'code': 400, 'message': _validation_message(exc), 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
        category = serializer.validated_data['category']
        description = serializer.validated_data['description'].strip()
        project = self.get_project()

        try:
            # 条件：上传包合法；动作：解压并创建一个或多个 Skill；结果：返回新 Skills 的完整信息。
            skills = Skill.create_from_zip(
                zip_file=zip_file,
                project=project,
                creator=request.user,
                api_key=api_key,
            )
            for skill in skills:
                if skill.declared_stage != category:
                    skill.declared_stage = category
                    skill.save(update_fields=['declared_stage', 'updated_at'])
                if skill.description != description:
                    skill.description = description
                    skill.save(update_fields=['description', 'updated_at'])
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
        category = serializer.validated_data['category']
        description = serializer.validated_data['description'].strip()
        project = self.get_project()

        try:
            skills = Skill.create_from_git(
                git_url=git_url,
                branch=branch,
                project=project,
                creator=request.user,
                api_key=api_key,
            )
            for skill in skills:
                if skill.declared_stage != category:
                    skill.declared_stage = category
                    skill.save(update_fields=['declared_stage', 'updated_at'])
                if skill.description != description:
                    skill.description = description
                    skill.save(update_fields=['description', 'updated_at'])
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
        category = serializer.validated_data['category']
        description = serializer.validated_data['description'].strip()
        project = self.get_project()

        try:
            skills = Skill.create_from_zip_url(
                zip_url=zip_url,
                project=project,
                creator=request.user,
                expected_sha256=sha256,
                api_key=api_key,
            )
            for skill in skills:
                if skill.declared_stage != category:
                    skill.declared_stage = category
                    skill.save(update_fields=['declared_stage', 'updated_at'])
                if skill.description != description:
                    skill.description = description
                    skill.save(update_fields=['description', 'updated_at'])
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
