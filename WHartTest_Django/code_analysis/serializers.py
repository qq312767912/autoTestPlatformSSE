from rest_framework import serializers

from .models import AnalysisTask, AnalysisTaskExecutionLog, CodeAnalysisLLMConfig, GitLabConnection, ProjectRepository, TestRequirementDraft, UserGitLabCredential


class CodeAnalysisLLMConfigSerializer(serializers.ModelSerializer):
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_api_key = serializers.BooleanField(read_only=True)

    class Meta:
        model = CodeAnalysisLLMConfig
        fields = [
            "id", "config_name", "name", "api_url", "api_key", "has_api_key",
            "request_timeout", "max_retries", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "has_api_key", "created_at", "updated_at"]

    def validate(self, attrs):
        timeout = attrs.get("request_timeout", getattr(self.instance, "request_timeout", 600))
        retries = attrs.get("max_retries", getattr(self.instance, "max_retries", 2))
        if not 30 <= timeout <= 7200:
            raise serializers.ValidationError({"request_timeout": "超时时间必须在 30~7200 秒之间"})
        if retries > 10:
            raise serializers.ValidationError({"max_retries": "最大重试次数不能超过 10"})
        if not self.instance and not attrs.get("api_key"):
            raise serializers.ValidationError({"api_key": "首次配置必须填写 API Key"})
        return attrs

    def create(self, validated_data):
        api_key = validated_data.pop("api_key", "")
        instance = CodeAnalysisLLMConfig(**validated_data)
        instance.set_api_key(api_key)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        api_key = validated_data.pop("api_key", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if api_key:
            instance.set_api_key(api_key)
        instance.save()
        return instance


class GitLabConnectionSerializer(serializers.ModelSerializer):
    repository_count = serializers.SerializerMethodField()

    class Meta:
        model = GitLabConnection
        fields = "__all__"
        read_only_fields = ["verify_ssl"]

    def get_repository_count(self, obj):
        annotated = getattr(obj, "repository_count", None)
        return annotated if annotated is not None else obj.repositories.count()


class ProjectRepositorySerializer(serializers.ModelSerializer):
    connection_name = serializers.CharField(source="connection.name", read_only=True, default=None)
    analysis_task_count = serializers.SerializerMethodField()
    connection = serializers.PrimaryKeyRelatedField(
        queryset=GitLabConnection.objects.all(), required=False, allow_null=True, default=None,
    )
    gitlab_project_id = serializers.CharField(required=False, allow_blank=True, default="")
    class Meta:
        model = ProjectRepository
        fields = "__all__"
        # 本地 Git 不存在 GitLab 项目 ID；必须先允许字段缺省，随后再根据
        # source_type 执行条件校验，否则 DRF 会在 validate() 前直接返回 400。
        extra_kwargs = {
            "gitlab_project_id": {"required": False, "allow_blank": True},
            "connection": {"required": False, "allow_null": True},
        }

    def get_analysis_task_count(self, obj):
        return obj.analysis_tasks.count()

    def validate(self, attrs):
        source_type = attrs.get("source_type", getattr(self.instance, "source_type", "gitlab"))
        if source_type == "local_git":
            local_path = (attrs.get("local_path") or getattr(self.instance, "local_path", "")).strip()
            if not local_path or local_path.startswith("/") or ".." in local_path.split("/"):
                raise serializers.ValidationError({"local_path": "本地仓库路径必须是 /workspace 下的相对路径"})
            attrs["connection"] = None
            attrs["gitlab_project_id"] = ""
        elif not (attrs.get("connection") or getattr(self.instance, "connection", None)):
            raise serializers.ValidationError({"connection": "GitLab 仓库必须选择连接"})
        elif not (attrs.get("gitlab_project_id", getattr(self.instance, "gitlab_project_id", "")) or "").strip():
            raise serializers.ValidationError({"gitlab_project_id": "请输入 GitLab 项目数字 ID 或完整路径"})
        return attrs


class CredentialSerializer(serializers.ModelSerializer):
    token = serializers.CharField(write_only=True, required=False, allow_blank=False)
    has_token = serializers.SerializerMethodField()
    class Meta:
        model = UserGitLabCredential
        fields = ["id", "project", "connection", "token", "has_token", "updated_at"]
        read_only_fields = ["id", "has_token", "updated_at"]
    def get_has_token(self, obj): return bool(obj.encrypted_token)
    def create(self, validated_data):
        token = validated_data.pop("token")
        obj, _ = UserGitLabCredential.objects.get_or_create(user=self.context["request"].user, **validated_data)
        obj.set_token(token); obj.save(); return obj
    def update(self, instance, validated_data):
        token = validated_data.pop("token", None)
        if token: instance.set_token(token)
        instance.save(); return instance


class TestRequirementDraftSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestRequirementDraft
        fields = "__all__"
        read_only_fields = ["task", "converted_test_case", "created_at"]


class AnalysisTaskExecutionLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True, default="系统")

    class Meta:
        model = AnalysisTaskExecutionLog
        fields = ["id", "event", "message", "detail", "actor_name", "created_at"]


class AnalysisTaskSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    repository_name = serializers.CharField(source="repository.name", read_only=True)
    creator_name = serializers.CharField(source="creator.username", read_only=True)
    executor_name = serializers.CharField(source="executor.username", read_only=True, default=None)
    test_requirement_drafts = TestRequirementDraftSerializer(many=True, read_only=True)
    class Meta:
        model = AnalysisTask
        exclude = ["raw_diff"]
        read_only_fields = ["creator", "executor", "status", "progress", "current_step", "error_message", "change_report", "test_report", "machine_coverage", "ai_coverage", "token_usage", "celery_task_id", "completed_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # 历史 OCR 任务曾将审查意见直接保存为测试点；读取时统一展示为可执行测试目标。
        from .services import normalize_finding_for_display, normalize_test_point_for_display
        change_report = dict(data.get("change_report") or {})
        change_report["findings"] = [normalize_finding_for_display(item) for item in change_report.get("findings", [])]
        data["change_report"] = change_report
        report = dict(data.get("test_report") or {})
        report["test_requirements"] = [
            normalize_test_point_for_display(item, change_report.get("findings", []))
            for item in report.get("test_requirements", [])
        ]
        data["test_report"] = report
        return data

    def validate(self, data):
        repository, project = data.get("repository"), data.get("project")
        if repository and project and repository.project_id != project.id:
            raise serializers.ValidationError("代码仓库不属于当前平台项目")
        if data.get("source_type") == "merge_request" and not data.get("merge_request_iid"):
            raise serializers.ValidationError("请选择Merge Request")
        if data.get("source_type") == "commits" and (not data.get("base_sha") or not data.get("head_sha")):
            raise serializers.ValidationError("请输入基准Commit和目标Commit")
        project = data.get("project", getattr(self.instance, "project", None))
        if project:
            from requirements.models import RequirementDocument
            for field in ("requirement_document_ids", "api_document_ids"):
                ids = data.get(field)
                if ids is None:
                    continue
                if not isinstance(ids, list) or len(ids) > 20:
                    raise serializers.ValidationError({field: "文档需以列表提交，最多选择 20 篇"})
                normalized = list(dict.fromkeys(str(item) for item in ids if item))
                if len(normalized) != len(ids) or RequirementDocument.objects.filter(project=project, id__in=normalized).count() != len(normalized):
                    raise serializers.ValidationError({field: "所选文档不存在或不属于当前项目"})
                data[field] = normalized
        return data
