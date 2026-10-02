# 导入路径工具用于路径归一化输出。
from pathlib import Path

# 导入 Django 配置读取工具。
from django.conf import settings

# 导入 DRF 序列化器基类。
from rest_framework import serializers

# 导入 Skill 模型。
from .models import Skill, SkillVersion


class SkillSerializer(serializers.ModelSerializer):
    """Skill 序列化器"""
    creator_name = serializers.CharField(source='creator.username', read_only=True, allow_null=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    script_path = serializers.SerializerMethodField()

    class Meta:
        model = Skill
        fields = [
            'id', 'name', 'description', 'skill_content',
            'skill_path', 'script_path', 'is_active',
            'project', 'project_name',
            'creator', 'creator_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'name', 'description', 'skill_content',
            'skill_path', 'creator', 'creator_name',
            'project_name', 'created_at', 'updated_at'
        ]

    def get_script_path(self, obj):
        script_path = obj.get_script_path()
        if not script_path:
            return None
        try:
            # 条件：脚本路径位于 MEDIA_ROOT 下；动作：转为相对路径；结果：前端拿到可拼接的统一路径格式。
            media_root = Path(settings.MEDIA_ROOT).resolve(strict=False)
            candidate = Path(script_path).resolve(strict=False)
            rel = candidate.relative_to(media_root)
            return str(rel).replace('\\', '/')
        except Exception:
            # 非法路径或越界路径统一返回 None，避免泄露服务端绝对路径。
            return None


class SkillUploadSerializer(serializers.Serializer):
    """Skill 上传序列化器"""
    file = serializers.FileField(
        help_text='包含一个或多个 SKILL.md 的 zip 文件'
    )
    api_key = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
        write_only=True,
        help_text='内部平台 Skill 安装时用户确认的 API Key',
    )

    def validate_file(self, value):
        # 只允许 zip 包导入，避免上传任意文件类型触发后续解析异常。
        if not value.name.endswith('.zip'):
            raise serializers.ValidationError('只支持 .zip 文件')
        # 条件：包体积超过 10MB；动作：拒绝；结果：限制单次上传资源消耗。
        if value.size > 10 * 1024 * 1024:  # 10MB
            raise serializers.ValidationError('文件大小不能超过 10MB')
        return value


class SkillGitImportSerializer(serializers.Serializer):
    """从 Git 仓库导入 Skill 的序列化器"""
    git_url = serializers.URLField(
        help_text='Git 仓库 HTTPS URL'
    )
    branch = serializers.CharField(
        required=False,
        default='main',
        allow_blank=True,
        max_length=256,
        help_text='分支名（默认 main）'
    )
    api_key = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
        write_only=True,
        help_text='内部平台 Skill 安装时用户确认的 API Key',
    )

    def validate_git_url(self, value):
        from urllib.parse import urlparse
        parsed = urlparse(value)
        # 条件：非 HTTPS 协议；动作：拒绝；结果：降低中间人攻击与明文传输风险。
        if parsed.scheme != 'https':
            raise serializers.ValidationError('仅支持 HTTPS 协议')
        if not parsed.hostname:
            raise serializers.ValidationError('无效的仓库地址')
        return value

    def validate_branch(self, value):
        import re
        value = (value or '').strip()
        # 空分支名回退 main，保证调用方可省略该字段。
        if not value:
            return 'main'
        # 防止分支参数被解析为命令选项，减少注入面。
        if value.startswith('-'):
            raise serializers.ValidationError('分支名不能以 - 开头')
        if not re.match(r'^[a-zA-Z0-9_./-]+$', value):
            raise serializers.ValidationError('分支名包含非法字符')
        return value


class SkillZipUrlImportSerializer(serializers.Serializer):
    """从远程 zip URL 导入 Skill 的序列化器（用于 Skill 商店）"""
    zip_url = serializers.URLField(
        help_text='zip 包的 HTTPS URL'
    )
    sha256 = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=64,
        help_text='可选：zip 包的 SHA256 校验和（64 位小写 16 进制）'
    )
    api_key = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
        write_only=True,
        help_text='内部平台 Skill 安装时用户确认的 API Key',
    )

    def validate_zip_url(self, value):
        from urllib.parse import urlparse
        parsed = urlparse(value)
        # 仅放行 HTTPS，更细粒度的私网/回环 IP 拦截在 model 层下载前做。
        if parsed.scheme != 'https':
            raise serializers.ValidationError('仅支持 HTTPS 协议')
        if not parsed.hostname:
            raise serializers.ValidationError('无效的 URL')
        return value

    def validate_sha256(self, value):
        import re
        value = (value or '').strip().lower()
        if not value:
            return ''
        # 校验和必须是 64 位 16 进制字符，避免传入异常值后续比对永远失败。
        if not re.match(r'^[0-9a-f]{64}$', value):
            raise serializers.ValidationError('SHA256 必须是 64 位小写 16 进制字符')
        return value


class SkillListSerializer(serializers.ModelSerializer):
    """Skill 列表序列化器（轻量）。

    除基本信息外，额外给出**描述性元数据**字段，供 Skill Hub 的筛选器与卡片标签
    使用：``source_type``(+label) / ``stage``(+label, +``stage_source``) / ``version``
    / ``copies``。

    ``stage`` 取「版本 manifest 声明优先，缺失回落管理员补填的 ``declared_stage``」，
    ``stage_source`` 告诉前端这个阶段是包自己声明的还是管理员补的。

    它们取自"展示版本"（``context["versions"]``：活跃版本优先，否则最新一版），
    由视图**一次性批量取好**注入 context —— 列表页不许按 Skill 逐个查版本（N+1）。
    取不到版本时统一返回空串，表示"这个 Skill 还没有版本"，不是错误。
    """

    creator_name = serializers.CharField(source='creator.username', read_only=True, allow_null=True)
    source_type = serializers.SerializerMethodField()
    source_type_label = serializers.SerializerMethodField()
    stage = serializers.SerializerMethodField()
    stage_label = serializers.SerializerMethodField()
    stage_source = serializers.SerializerMethodField()
    version = serializers.SerializerMethodField()
    copies = serializers.SerializerMethodField()

    class Meta:
        model = Skill
        fields = [
            'id', 'name', 'description', 'is_active',
            'creator_name', 'created_at',
            # 描述性元数据：筛选器与卡片标签用，不参与任何可用性判定。
            'source_type', 'source_type_label',
            'stage', 'stage_label', 'stage_source', 'version',
            # 同名副本数（Skill Hub 是公共目录，同名归并成一条展示后，
            # 要把"库里其实有几份"显式告诉使用者，而不是把多重性藏起来）。
            'copies',
        ]

    # -- 展示版本（元数据来源，不是可用性判据） ---------------------------

    def _version(self, obj):
        versions = self.context.get('versions') or {}
        return versions.get(obj.pk)

    def get_source_type(self, obj):
        version = self._version(obj)
        return version.source_type if version is not None else ''

    def get_source_type_label(self, obj):
        version = self._version(obj)
        if version is None:
            return ''
        # 直接复用模型 choices 的中文标签，避免前端再抄一份来源映射。
        return dict(SkillVersion.SOURCE_TYPE_CHOICES).get(version.source_type, version.source_type)

    def get_stage(self, obj):
        """展示阶段：版本 manifest 声明的优先，缺失时回落到管理员补填的声明。

        存量迁移来的包 manifest 里没有 stage（`needs_manual_stage_binding`），
        而版本包不可改写，所以只能在 Skill 上补一个声明位（`declared_stage`）。
        """
        stage, _ = self._stage_of(obj)
        return stage

    def _stage_of(self, obj):
        """返回 ``(阶段标识符, 来源)``；来源取值 ``manifest`` / ``declared`` / ``''``。"""
        version = self._version(obj)
        declared_in_manifest = str(((version.manifest if version is not None else None) or {}).get('stage') or '')
        if declared_in_manifest:
            return declared_in_manifest, 'manifest'
        declared = str(getattr(obj, 'declared_stage', '') or '')
        if declared:
            return declared, 'declared'
        return '', ''

    def get_stage_source(self, obj):
        """阶段是从哪来的，供前端区分「包自己声明的」和「管理员补的」。"""
        return self._stage_of(obj)[1]

    def get_stage_label(self, obj):
        stage = self.get_stage(obj)
        if not stage:
            return ''
        # 阶段中文名真值在 capability_registry；未登记的自定义阶段原样返回，
        # 不编造标签——"看到裸标识符"比"看到编出来的名字"更好排查。
        from knowledge_evolution.capability_registry import STAGE_LABELS

        return STAGE_LABELS.get(stage, stage)

    def get_version(self, obj):
        version = self._version(obj)
        return version.version if version is not None else ''

    def get_copies(self, obj):
        """该名字在库里的副本数（1 表示只有一条，>1 说明是归并展示）。"""
        copies = self.context.get('copies') or {}
        return int(copies.get(obj.name, 1))


class SkillToggleSerializer(serializers.ModelSerializer):
    """Skill 启用/禁用切换序列化器"""

    class Meta:
        model = Skill
        fields = ['is_active']


class SkillStageBindingSerializer(serializers.Serializer):
    """给 Skill 补填 / 撤销能力阶段（Skill Hub「阶段未声明」的补救入口）。

    为什么要在服务端卡取值：写进 ``declared_stage`` 的裸标识符会一路走进阶段匹配
    逻辑（``task_binding`` 的 ``Q(declared_stage=stage)``）。拼错的阶段**不会报错**，
    只会让这个 Skill 永远匹配不上任何阶段——那种问题在页面上看起来和"没填"一样，
    极难排查。所以只放行登记过的业务能力阶段。

    取值真值 = ``capability_registry.BUSINESS_CAPABILITY_STAGES``（8 类业务能力）。
    **不含** ``knowledge_query``：它属 ``PLATFORM_UTILITY_STAGES``（平台工具），
    不参与业务能力口径，也不该被绑成某个阶段的实现。
    空串表示撤销声明（允许，否则填错了退不回去）。
    """

    stage = serializers.CharField(required=True, allow_blank=True, max_length=64)

    def validate_stage(self, value):
        from knowledge_evolution.capability_registry import BUSINESS_CAPABILITY_STAGES

        value = (value or '').strip()
        if not value:
            return ''
        if value not in BUSINESS_CAPABILITY_STAGES:
            raise serializers.ValidationError(
                f'未知的能力阶段：{value}；可选值见 capability_registry.BUSINESS_CAPABILITY_STAGES'
            )
        return value


# ---------------------------------------------------------------------------
# Skill 版本（T02/T06/T07）
# ---------------------------------------------------------------------------


class SkillVersionSerializer(serializers.ModelSerializer):
    """版本详情。``state`` 来自关联发布单元，不是本模型字段，因此显式声明只读。"""

    state = serializers.CharField(read_only=True)
    # 发布单元主键。控制台要拿它去调 ``capability-releases/{id}/approval-view/``
    # 与 ``bindings/``；没有这个字段，前端只能按 (name, version) 去猜关联关系，
    # 而关联本来就在库里，不该由前端重建。
    release_id = serializers.UUIDField(read_only=True, allow_null=True)
    skill_name = serializers.CharField(source='skill.name', read_only=True)
    created_by_name = serializers.CharField(
        source='created_by.username', read_only=True, allow_null=True,
    )
    stage = serializers.SerializerMethodField()
    entrypoint = serializers.SerializerMethodField()

    class Meta:
        model = SkillVersion
        fields = [
            'id', 'skill', 'skill_name', 'version', 'state', 'release_id',
            'package_sha256', 'manifest', 'validation_report',
            'source_type', 'source_metadata', 'previous_version',
            'stage', 'entrypoint',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'skill', 'skill_name', 'version', 'state', 'release_id',
            'package_sha256', 'manifest', 'validation_report',
            'source_type', 'source_metadata', 'previous_version',
            'stage', 'entrypoint',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]

    def get_stage(self, obj) -> str:
        return obj.manifest_stage()

    def get_entrypoint(self, obj) -> str:
        return obj.manifest_entrypoint()


class SkillVersionListSerializer(serializers.ModelSerializer):
    """版本列表（轻量）：不含 manifest 与校验报告，避免列表接口体积膨胀。"""

    state = serializers.CharField(read_only=True)

    class Meta:
        model = SkillVersion
        fields = ['id', 'version', 'state', 'package_sha256', 'source_type', 'created_at']


class SkillPreflightSerializer(serializers.Serializer):
    """包预检请求：只上传文件，校验结果与短期令牌在响应里返回。"""

    file = serializers.FileField(help_text='包含 SKILL.md 的技能包 zip')

    def validate_file(self, value):
        if not (value.name or '').lower().endswith('.zip'):
            raise serializers.ValidationError('只支持 .zip 文件')
        # 与 ``validation.MAX_TOTAL_UNCOMPRESSED``（50MB）配套：压缩包本身不该更大。
        if value.size > 20 * 1024 * 1024:
            raise serializers.ValidationError('文件大小不能超过 20MB')
        return value


class SkillCandidateCreateSerializer(serializers.Serializer):
    """用预检令牌创建候选版本。"""

    token = serializers.CharField(help_text='预检接口返回的短期令牌')
    change_reason = serializers.CharField(
        required=False, allow_blank=True, max_length=500,
        help_text='变更原因，工作台会展示给审批人',
    )
    expected_benefit = serializers.CharField(
        required=False, allow_blank=True, max_length=500, help_text='预期收益',
    )
    impact_scope = serializers.CharField(
        required=False, allow_blank=True, max_length=500, help_text='影响范围',
    )
    api_key = serializers.CharField(
        required=False, allow_blank=True, max_length=128, write_only=True,
        help_text='内部平台 Skill 安装时用户确认的 API Key',
    )


class SkillVersionDiffQuerySerializer(serializers.Serializer):
    """版本 diff 的查询参数；``base`` 缺省时与上一版本比较。"""

    base = serializers.UUIDField(required=False, allow_null=True)


class SkillQuarantineSerializer(serializers.Serializer):
    """隔离请求：原因必填（R12 要求安全事件必须留痕）。"""

    reason = serializers.CharField(max_length=500)


class SkillVersionValidateSerializer(serializers.Serializer):
    """提交静态校验请求。

    ``reason`` 可选：校验本身会产出逐条报告，报告就是最好的说明，
    不像隔离那种安全事件必须由人补上"为什么"。把它做成必填只会逼出
    "校验""提交"这类无信息量的原因。
    """

    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=500,
        help_text='可选：提交校验的说明',
    )

