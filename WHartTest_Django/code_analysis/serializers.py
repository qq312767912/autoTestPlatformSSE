from rest_framework import serializers

from .models import AnalysisTask, GitLabConnection, ProjectRepository, TestRequirementDraft, UserGitLabCredential


class GitLabConnectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = GitLabConnection
        fields = "__all__"


class ProjectRepositorySerializer(serializers.ModelSerializer):
    connection_name = serializers.CharField(source="connection.name", read_only=True)
    class Meta:
        model = ProjectRepository
        fields = "__all__"


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
        read_only_fields = ["task", "created_at"]


class AnalysisTaskSerializer(serializers.ModelSerializer):
    repository_name = serializers.CharField(source="repository.name", read_only=True)
    creator_name = serializers.CharField(source="creator.username", read_only=True)
    test_requirement_drafts = TestRequirementDraftSerializer(many=True, read_only=True)
    class Meta:
        model = AnalysisTask
        exclude = ["raw_diff"]
        read_only_fields = ["creator", "status", "progress", "current_step", "error_message", "change_report", "test_report", "machine_coverage", "ai_coverage", "token_usage", "celery_task_id", "completed_at"]

    def validate(self, data):
        repository, project = data.get("repository"), data.get("project")
        if repository and project and repository.project_id != project.id:
            raise serializers.ValidationError("代码仓库不属于当前平台项目")
        if data.get("source_type") == "merge_request" and not data.get("merge_request_iid"):
            raise serializers.ValidationError("请选择Merge Request")
        if data.get("source_type") == "commits" and (not data.get("base_sha") or not data.get("head_sha")):
            raise serializers.ValidationError("请输入基准Commit和目标Commit")
        return data
