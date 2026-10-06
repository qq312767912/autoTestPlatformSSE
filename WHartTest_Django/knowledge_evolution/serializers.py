from rest_framework import serializers

from .models import (
    AssetCandidateEvent,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    FeedbackEvent,
    GenerationOutput,
    KnowledgeCandidate,
    RetrievalTrace,
)
from .capability_models import (
    CapabilityDefinition, CapabilityRelease, PromotionDecision, ReleaseObservation,
)
from .gold_models import (
    AnnotationConflict,
    GoldAnnotation,
    GoldCase,
    GoldDataset,
    GoldDatasetVersion,
    TestAssetTaxonomy,
)
from .evaluation_v2_models import EvaluationRubric, JudgeResult
from .trace_models import ExecutionSpan, FailureAttribution
from .optimization_models import OptimizationExperiment, OptimizationProposal
from .workflow_models import FlywheelRun, StageExecutionContext, StageExecutionAttempt
from .history_models import (
    HistoryImportBatch, HistoryImportItem, HistoryReplay,
    HistoryReplayDifference, ProjectFlywheelSetting,
)
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
        read_only_fields = [f.name for f in model._meta.get_fields() if getattr(f, "name", None)]


class FlywheelRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = FlywheelRun
        fields = [
            "id", "project", "workflow_id", "entry_type", "intent",
            "requirement_document_ids", "status", "created_by", "metadata",
            "created_at", "updated_at",
        ]
        read_only_fields = ["created_by", "created_at", "updated_at"]

    def validate_requirement_document_ids(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("requirement_document_ids 必须是数组")
        return [str(item) for item in value]


class StageExecutionAttemptSerializer(serializers.ModelSerializer):
    """执行尝试的只读视图（T01）。

    带出 ``skill_name`` / ``skill_version_label`` / ``is_terminal`` 三个派生字段，
    是为了让飞轮页不必自己 join ``SkillVersion`` 才能显示"这一轮用的哪份包"，
    也不必自己重算终态——终态判定只有 ``ATTEMPT_TERMINAL_STATES`` 一个真值。
    """

    skill_name = serializers.SerializerMethodField()
    skill_version_label = serializers.SerializerMethodField()
    requested_by_username = serializers.SerializerMethodField()
    is_terminal = serializers.BooleanField(read_only=True)

    class Meta:
        model = StageExecutionAttempt
        fields = [
            "id", "project", "flywheel_run", "workflow_id", "stage",
            "skill_version", "skill_name", "skill_version_label", "skill_package_sha256",
            "parent_output_ids", "session_id", "entry_type", "status",
            "output", "retry_of", "idempotency_key", "error_code", "error_summary",
            "detail", "requested_by", "requested_by_username", "is_terminal",
            "created_at", "dispatched_at", "started_at", "output_published_at",
            "finished_at", "updated_at",
        ]
        read_only_fields = fields

    def get_skill_name(self, obj) -> str:
        version = obj.skill_version
        if version is None or not version.skill_id:
            return ""
        return version.skill.name

    def get_skill_version_label(self, obj) -> str:
        return obj.skill_version.version if obj.skill_version_id else ""

    def get_requested_by_username(self, obj) -> str:
        return obj.requested_by.username if obj.requested_by_id else ""


class StageExecutionContextSerializer(serializers.ModelSerializer):
    """执行上下文的只读视图（T02 / R3）。

    与 ``StageExecutionAttemptSerializer`` 的区别很关键：attempt 讲"这一轮跑成什么样"，
    context 讲"这一轮被授权用什么参数跑"。因此这里**只**带出参数与版本，
    不带输出正文、不带失败堆栈——那些属于 attempt / output 的查询口径，
    混进来会让"解析上下文"变成一个顺带能读产出的宽接口。
    """

    stage_label = serializers.SerializerMethodField()
    skill_name = serializers.SerializerMethodField()
    skill_version_label = serializers.SerializerMethodField()
    attempt_status = serializers.SerializerMethodField()
    expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = StageExecutionContext
        fields = [
            "id", "project", "flywheel_run", "attempt", "attempt_status",
            "workflow_id", "stage", "stage_label", "entry_type",
            "channel", "module_key", "skill_version", "skill_name",
            "skill_version_label", "skill_package_sha256", "parent_output_ids",
            "payload", "issued_to", "expires_at", "expired",
            "last_resolved_at", "resolve_count", "created_at", "updated_at",
        ]
        read_only_fields = fields

    def get_stage_label(self, obj) -> str:
        from .operations import WorkflowGateService
        return WorkflowGateService.STAGE_LABELS.get(obj.stage, obj.stage)

    def get_skill_name(self, obj) -> str:
        version = obj.skill_version
        if version is None or not version.skill_id:
            return ""
        return version.skill.name

    def get_skill_version_label(self, obj) -> str:
        return obj.skill_version.version if obj.skill_version_id else ""

    def get_attempt_status(self, obj) -> str:
        return obj.attempt.status if obj.attempt_id else ""


class ProjectFlywheelSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectFlywheelSetting
        fields = ["project", "enabled", "rollout_note", "updated_by", "updated_at"]
        read_only_fields = ["updated_by", "updated_at"]


class HistoryImportItemSerializer(serializers.ModelSerializer):
    file_name = serializers.CharField(source="file.original_name", read_only=True)

    class Meta:
        model = HistoryImportItem
        fields = ["id", "role", "file", "file_name", "file_hash", "metadata"]
        read_only_fields = fields


class HistoryImportBatchSerializer(serializers.ModelSerializer):
    items = HistoryImportItemSerializer(many=True, read_only=True)

    class Meta:
        model = HistoryImportBatch
        fields = ["id", "project", "name", "status", "manifest_hash", "preflight",
                  "candidate_count", "created_by", "confirmed_by", "confirmed_at",
                  "created_at", "updated_at", "items"]
        read_only_fields = fields


class HistoryReplayDifferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = HistoryReplayDifference
        fields = "__all__"
        read_only_fields = [field.name for field in model._meta.fields]


class HistoryReplaySerializer(serializers.ModelSerializer):
    differences = HistoryReplayDifferenceSerializer(many=True, read_only=True)

    class Meta:
        model = HistoryReplay
        fields = ["id", "project", "batch", "flywheel_run", "gold_version", "status",
                  "execution_lock", "config_hash", "stage_scores", "summary", "gate_report",
                  "created_by", "created_at", "updated_at", "differences"]
        read_only_fields = fields


class ReleaseObservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReleaseObservation
        fields = "__all__"
        read_only_fields = [f.name for f in model._meta.get_fields() if getattr(f, "name", None)]


class CapabilityReleaseBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = CapabilityRelease
        fields = ["id", "kind", "name", "version", "state", "created_at"]
        read_only_fields = fields


class CapabilityReleaseSerializer(serializers.ModelSerializer):
    decisions = PromotionDecisionSerializer(many=True, read_only=True)
    observations = ReleaseObservationSerializer(many=True, read_only=True)

    class Meta:
        model = CapabilityRelease
        fields = [
            "id", "project", "kind", "name", "version", "config", "artifact_hash",
            "state", "candidate", "previous_release", "baseline_run", "candidate_run",
            "gate_report", "created_by", "approved_by", "approved_at", "activated_at",
            "created_at", "updated_at", "decisions", "observations",
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


class GoldDatasetSerializer(serializers.ModelSerializer):
    version_count = serializers.IntegerField(source="versions.count", read_only=True)

    class Meta:
        model = GoldDataset
        fields = [
            "id", "project", "name", "task_type", "description", "status",
            "scope_type", "scope_key", "governance", "taxonomy_version", "approver",
            "owner", "created_by", "version_count", "created_at", "updated_at",
        ]


class TestAssetTaxonomySerializer(serializers.ModelSerializer):
    class Meta:
        model = TestAssetTaxonomy
        fields = [
            "id", "project", "scope_key", "version", "state", "categories",
            "critical_scenarios", "maintained_by", "approved_by", "approved_at",
            "content_hash", "created_at", "updated_at",
        ]
        read_only_fields = [
            "state", "maintained_by", "approved_by", "approved_at", "content_hash",
            "created_at", "updated_at",
        ]

    def validate_categories(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("categories 必须是数组")
        return value

    def validate_critical_scenarios(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("critical_scenarios 必须是数组")
        return value
        read_only_fields = ["created_by", "version_count", "created_at", "updated_at"]


class GoldDatasetVersionSerializer(serializers.ModelSerializer):
    case_count = serializers.IntegerField(source="cases.count", read_only=True)

    class Meta:
        model = GoldDatasetVersion
        fields = [
            "id", "dataset", "version", "state", "content_hash", "sample_stats",
            "governance_snapshot", "parent_version", "created_by", "frozen_by",
            "frozen_at", "case_count", "created_at", "updated_at",
        ]
        read_only_fields = [
            "state", "content_hash", "sample_stats", "governance_snapshot",
            "created_by", "frozen_by", "frozen_at", "case_count", "created_at",
            "updated_at",
        ]


class GoldAnnotationSerializer(serializers.ModelSerializer):
    annotator_name = serializers.CharField(source="annotator.username", read_only=True)

    class Meta:
        model = GoldAnnotation
        fields = [
            "id", "case", "round", "answer", "rubric_scores", "evidence",
            "conclusion", "comment", "tags", "split", "category", "review_snapshot",
            "annotator", "annotator_name", "created_at",
        ]
        read_only_fields = fields


class GoldCaseSerializer(serializers.ModelSerializer):
    annotations = GoldAnnotationSerializer(many=True, read_only=True)

    class Meta:
        model = GoldCase
        fields = [
            "id", "version", "source_output", "source_feedback", "task_type", "title",
            "input_snapshot", "expected_output", "rubric", "required_items",
            "forbidden_items", "evidence", "tags", "split", "state", "difficulty",
            "risk_level", "privacy_level", "allow_optimization", "source_hash",
            "candidate_origin", "candidate_score", "recommended_split",
            "recommended_tags", "dedup_fingerprint", "review_checklist",
            "created_by", "annotations", "created_at", "updated_at",
        ]
        read_only_fields = [
            "source_hash", "created_by", "annotations", "created_at", "updated_at",
        ]


class AssetCandidateEventSerializer(serializers.ModelSerializer):
    """候选沉淀事件（T04）。

    只读：入队由业务入口写入、处理由统一服务驱动，任何"客户端直接改状态"的入口
    都会绕过预检与死信阈值——需要干预时走 ``retry`` 动作。
    """

    source_type_label = serializers.CharField(source="get_source_type_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = AssetCandidateEvent
        fields = [
            "id", "project", "source_type", "source_type_label", "source_id", "signal",
            "status", "status_label", "attempts", "max_attempts", "last_error",
            "payload", "preflight", "candidate", "processed_at", "created_at", "updated_at",
        ]
        read_only_fields = fields


class AnnotationConflictSerializer(serializers.ModelSerializer):
    primary_annotation = GoldAnnotationSerializer(read_only=True)
    review_annotation = GoldAnnotationSerializer(read_only=True)

    class Meta:
        model = AnnotationConflict
        fields = [
            "id", "case", "primary_annotation", "review_annotation", "differing_fields",
            "state", "resolution", "resolved_by", "resolved_at", "created_at",
        ]
        read_only_fields = fields


class EvaluationRubricSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvaluationRubric
        fields = [
            "id", "project", "task_type", "name", "version", "dimensions",
            "required_items", "forbidden_items", "jury_config", "is_active",
            "created_by", "created_at", "updated_at",
        ]
        read_only_fields = ["created_by", "created_at", "updated_at"]


class JudgeResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = JudgeResult
        fields = "__all__"
        read_only_fields = [f.name for f in model._meta.get_fields() if getattr(f, "name", None)]


class ExecutionSpanSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExecutionSpan
        fields = "__all__"
        read_only_fields = [f.name for f in model._meta.get_fields() if getattr(f, "name", None)]


class FailureAttributionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FailureAttribution
        fields = "__all__"
        read_only_fields = [f.name for f in model._meta.get_fields() if getattr(f, "name", None)]


class OptimizationProposalSerializer(serializers.ModelSerializer):
    attribution_ids = serializers.SerializerMethodField()

    class Meta:
        model = OptimizationProposal
        fields = [
            "id", "project", "capability", "baseline_release", "attribution_ids",
            "proposal_type", "title", "summary", "change_patch", "expected_benefit",
            "impact_scope", "risk_notes", "rollback_plan", "state", "fingerprint",
            "generated_by", "created_by", "created_at", "updated_at",
        ]
        read_only_fields = fields

    def get_attribution_ids(self, obj):
        return [str(value) for value in obj.attributions.values_list("id", flat=True)]


class OptimizationExperimentSerializer(serializers.ModelSerializer):
    class Meta:
        model = OptimizationExperiment
        fields = "__all__"
        read_only_fields = [f.name for f in model._meta.get_fields() if getattr(f, "name", None)]


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
            "id", "suite", "suite_name", "gold_dataset_version", "capability_release",
            "replay_hash", "name", "config", "status",
            "metrics_summary", "cost_summary", "result_summary",
            "l0_score", "l1_score", "l2_score", "l3_score",
            "policy_version", "model_name", "cost_usd",
            "triggered_by", "started_at", "finished_at", "created_at", "updated_at",
        ]
        read_only_fields = [
            "status", "replay_hash", "metrics_summary", "cost_summary", "result_summary",
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
