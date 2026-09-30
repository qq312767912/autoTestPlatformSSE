from rest_framework import serializers

from .models import (
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    FeedbackEvent,
    GenerationOutput,
    KnowledgeCandidate,
    RetrievalTrace,
)
from .capability_models import CapabilityDefinition, CapabilityRelease, PromotionDecision
from .knowledge_models import (
    KnowledgeAsset,
    KnowledgeAuditLog,
    KnowledgeConflict,
    KnowledgeEvidence,
    KnowledgeVersion,
    SourceSnapshot,
)


class PromotionDecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromotionDecision
        fields = "__all__"
        read_only_fields = fields


class CapabilityReleaseBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = CapabilityRelease
        fields = ["id", "kind", "name", "version", "state", "created_at"]
        read_only_fields = fields


class CapabilityReleaseSerializer(serializers.ModelSerializer):
    decisions = PromotionDecisionSerializer(many=True, read_only=True)

    class Meta:
        model = CapabilityRelease
        fields = [
            "id", "project", "kind", "name", "version", "config", "artifact_hash",
            "state", "candidate", "previous_release", "baseline_run", "candidate_run",
            "gate_report", "created_by", "approved_by", "approved_at", "activated_at",
            "created_at", "updated_at", "decisions",
        ]
        read_only_fields = [
            "artifact_hash", "state", "previous_release", "gate_report", "created_by",
            "approved_by", "approved_at", "activated_at", "created_at", "updated_at",
        ]


class CapabilityDefinitionSerializer(serializers.ModelSerializer):
    active_release = CapabilityReleaseBriefSerializer(read_only=True)

    class Meta:
        model = CapabilityDefinition
        fields = [
            "id", "project", "kind", "name", "description", "evaluation_mode",
            "stages", "default_suite", "gate_rules", "active_release", "is_active",
            "created_by", "created_at", "updated_at",
        ]
        read_only_fields = ["active_release", "created_by", "created_at", "updated_at"]

    def validate_stages(self, value):
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError("stages 必须是非空列表")
        valid = {c[0] for c in EvaluationSuite.TASK_TYPE_CHOICES}
        unknown = set(value) - valid
        if unknown:
            raise serializers.ValidationError(f"未知阶段: {unknown}")
        return value

    def validate(self, attrs):
        mode = attrs.get("evaluation_mode", "single")
        stages = attrs.get("stages") or []
        if mode == "workflow" and len(stages) < 2:
            raise serializers.ValidationError({"stages": "链路自进化模式至少需要 2 个阶段"})
        return attrs


class RetrievalTraceSerializer(serializers.ModelSerializer):
    output_ids = serializers.SerializerMethodField()

    class Meta:
        model = RetrievalTrace
        fields = [
            "id", "project", "knowledge_base", "user", "task_type", "task_id",
            "query", "rewritten_query", "policy_version", "status", "channels",
            "candidates", "citations", "timings", "token_usage", "error_code",
            "output_ids", "created_at",
        ]
        read_only_fields = fields

    def get_output_ids(self, obj):
        return [str(o.id) for o in obj.generation_outputs.all()]


class GenerationOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = GenerationOutput
        fields = [
            "id", "project", "trace", "task_type", "task_id", "model_version",
            "prompt_version", "content", "output_hash", "metadata", "created_at",
        ]
        read_only_fields = fields


class UUIDListField(serializers.Field):
    """同时支持读写的 UUID 列表字段；用于 ManyToMany 的 id 列表。"""

    def to_representation(self, value):
        return [str(v.id) for v in value.all()]

    def to_internal_value(self, data):
        if data is None:
            return []
        return [str(v) for v in data]


class FeedbackEventSerializer(serializers.ModelSerializer):
    # actor 默认会被渲染成主键 id，前端需要 {id, username} 才能显示操作人
    actor = serializers.SerializerMethodField()
    knowledge_version_ids = UUIDListField(required=False)

    class Meta:
        model = FeedbackEvent
        fields = [
            "id", "project", "output", "trace", "knowledge_version_ids", "signal",
            "value", "reason_code", "comment", "actor", "actor_type",
            "idempotency_key", "occurred_at", "created_at",
        ]
        read_only_fields = ["id", "project", "actor", "actor_type", "created_at"]

    def get_actor(self, obj):
        if not obj.actor_id:
            return None
        return {"id": obj.actor_id, "username": obj.actor.username}

    def validate(self, attrs):
        output = attrs.get("output")
        trace = attrs.get("trace") or (output.trace if output else None)
        if not trace:
            raise serializers.ValidationError("反馈必须关联生成输出或检索轨迹")
        if output and attrs.get("trace") and output.trace_id != attrs["trace"].id:
            raise serializers.ValidationError("输出与检索轨迹不匹配")
        attrs["trace"] = trace
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        trace = validated_data["trace"]
        knowledge_version_ids = validated_data.pop("knowledge_version_ids", [])
        event = FeedbackEvent(
            **validated_data,
            project_id=trace.project_id,
            actor=request.user,
            actor_type="user",
        )
        event.full_clean()
        event.save()
        if knowledge_version_ids:
            from .knowledge_models import KnowledgeVersion

            versions = KnowledgeVersion.objects.filter(id__in=knowledge_version_ids)
            event.knowledge_versions.set(versions)
        return event


class EvaluationSuiteSerializer(serializers.ModelSerializer):
    case_count = serializers.IntegerField(source="cases.count", read_only=True)

    class Meta:
        model = EvaluationSuite
        fields = [
            "id", "project", "name", "suite_type", "task_type", "description",
            "split_ratio", "is_active", "case_count", "created_by", "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class EvaluationRunSerializer(serializers.ModelSerializer):
    suite_name = serializers.CharField(source="suite.name", read_only=True)
    result_summary = serializers.SerializerMethodField()
    l0_score = serializers.SerializerMethodField()
    l1_score = serializers.SerializerMethodField()
    l2_score = serializers.SerializerMethodField()
    l3_score = serializers.SerializerMethodField()
    policy_version = serializers.SerializerMethodField()
    model_name = serializers.SerializerMethodField()
    cost_usd = serializers.SerializerMethodField()

    class Meta:
        model = EvaluationRun
        fields = [
            "id", "suite", "suite_name", "name", "config", "status",
            "metrics_summary", "cost_summary", "result_summary",
            "l0_score", "l1_score", "l2_score", "l3_score",
            "policy_version", "model_name", "cost_usd",
            "triggered_by", "started_at", "finished_at", "created_at", "updated_at",
        ]
        read_only_fields = [
            "status", "metrics_summary", "cost_summary", "result_summary",
            "l0_score", "l1_score", "l2_score", "l3_score",
            "policy_version", "model_name", "cost_usd",
            "started_at", "finished_at", "created_at", "updated_at",
        ]

    def get_result_summary(self, obj):
        qs = obj.results.all()
        total = qs.count()
        completed = qs.filter(status="completed").count()
        failed = qs.filter(status="failed").count()
        return {"total": total, "completed": completed, "failed": failed}

    def _get_metric(self, obj, key):
        metrics = obj.metrics_summary or {}
        return metrics.get(key)

    def get_l0_score(self, obj): return self._get_metric(obj, "l0_score")
    def get_l1_score(self, obj): return self._get_metric(obj, "l1_score")
    def get_l2_score(self, obj): return self._get_metric(obj, "l2_score")
    def get_l3_score(self, obj): return self._get_metric(obj, "l3_score")

    def get_policy_version(self, obj):
        return (obj.config or {}).get("policy_version") or (obj.config or {}).get("policy_id") or ""

    def get_model_name(self, obj):
        return (obj.config or {}).get("model_version") or (obj.config or {}).get("model_name") or ""

    def get_cost_usd(self, obj):
        return (obj.cost_summary or {}).get("total_usd") or (obj.cost_summary or {}).get("estimated_cost_usd")


class EvaluationResultSerializer(serializers.ModelSerializer):
    case_number = serializers.IntegerField(source="case.case_number", read_only=True)
    split = serializers.CharField(source="case.split", read_only=True)

    class Meta:
        model = EvaluationResult
        fields = [
            "id", "run", "case", "case_number", "split", "status",
            "predicted_payload", "latency_ms", "token_usage", "estimated_cost_usd",
            "l0_score", "l1_score", "l2_score", "l3_score", "raw_scores",
            "error_message", "created_at", "updated_at",
        ]
        read_only_fields = fields


class KnowledgeCandidateSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeCandidate
        fields = [
            "id", "project", "kind", "origin", "payload", "level", "confidence",
            "source_snapshot", "evidence", "feedback_event_ids", "state",
            "dedup_key", "promoted_asset", "extracted_by", "prompt_version",
            "review_reason", "reviewed_by", "reviewed_at", "created_by",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "dedup_key", "created_at", "updated_at"]


class SourceSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceSnapshot
        fields = [
            "id", "project", "knowledge_base", "source_type", "source_id",
            "revision", "content_hash", "authority", "status", "location",
            "raw_content", "parsed_text", "parser_version", "captured_by",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class KnowledgeVersionBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeVersion
        fields = ["id", "version", "status", "valid_from", "expires_at"]
        read_only_fields = fields


class KnowledgeAssetBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeAsset
        fields = ["id", "asset_type", "key", "title", "level", "status"]
        read_only_fields = fields


class KnowledgeAssetSerializer(serializers.ModelSerializer):
    current_version = KnowledgeVersionBriefSerializer(read_only=True)

    class Meta:
        model = KnowledgeAsset
        fields = [
            "id", "project", "asset_type", "key", "title", "level", "status",
            "applicability", "acl_tags", "current_version", "owner",
            "created_by", "updated_by", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "current_version", "created_at", "updated_at"]


class KnowledgeVersionSerializer(serializers.ModelSerializer):
    asset = KnowledgeAssetBriefSerializer(read_only=True)
    evidence_count = serializers.IntegerField(source="evidences.count", read_only=True)

    class Meta:
        model = KnowledgeVersion
        fields = [
            "id", "asset", "version", "content", "content_hash",
            "source_snapshot", "previous_version", "status",
            "valid_from", "expires_at", "approved_by", "approved_at",
            "second_approver", "second_approved_at", "change_reason",
            "created_by", "created_at", "updated_at", "evidence_count",
        ]
        read_only_fields = [
            "id", "content_hash", "approved_at", "second_approved_at",
            "created_at", "updated_at", "evidence_count",
        ]


class KnowledgeEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeEvidence
        fields = [
            "id", "version", "snapshot", "relation", "location", "location_hash",
            "excerpt", "weight", "extractor_version", "created_at",
        ]
        read_only_fields = ["id", "location_hash", "created_at"]


class KnowledgeConflictSerializer(serializers.ModelSerializer):
    left_asset = KnowledgeAssetBriefSerializer(read_only=True)
    right_asset = KnowledgeAssetBriefSerializer(read_only=True)
    left_version = KnowledgeVersionBriefSerializer(read_only=True)
    right_version = KnowledgeVersionBriefSerializer(read_only=True)

    class Meta:
        model = KnowledgeConflict
        fields = [
            "id", "project", "left_asset", "right_asset",
            "left_version", "right_version", "conflict_type", "state",
            "resolution", "trust_penalty", "detail", "resolution_note",
            "detected_by", "detector_version", "resolved_by", "resolved_at",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "left_asset", "right_asset", "left_version", "right_version",
            "resolved_at", "created_at", "updated_at",
        ]


class KnowledgeAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeAuditLog
        fields = [
            "id", "project", "actor", "actor_type", "action", "entity_type",
            "entity_id", "from_state", "to_state", "before_version",
            "after_version", "reason", "detail", "created_at",
        ]
        read_only_fields = fields
