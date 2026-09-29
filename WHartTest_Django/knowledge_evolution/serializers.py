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


class RetrievalTraceSerializer(serializers.ModelSerializer):
    output_id = serializers.UUIDField(source="generation_output.id", read_only=True)

    class Meta:
        model = RetrievalTrace
        fields = [
            "id", "project", "knowledge_base", "user", "task_type", "task_id",
            "query", "rewritten_query", "policy_version", "status", "channels",
            "candidates", "citations", "timings", "token_usage", "error_code",
            "output_id", "created_at",
        ]
        read_only_fields = fields


class GenerationOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = GenerationOutput
        fields = [
            "id", "project", "trace", "task_type", "task_id", "model_version",
            "prompt_version", "content", "output_hash", "metadata", "created_at",
        ]
        read_only_fields = fields


class FeedbackEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeedbackEvent
        fields = [
            "id", "project", "output", "trace", "knowledge_version_ids", "signal",
            "value", "reason_code", "comment", "actor", "actor_type",
            "idempotency_key", "occurred_at", "created_at",
        ]
        read_only_fields = ["id", "project", "actor", "actor_type", "created_at"]

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
        event = FeedbackEvent(
            **validated_data,
            project_id=trace.project_id,
            actor=request.user,
            actor_type="user",
        )
        event.full_clean()
        event.save()
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

    class Meta:
        model = EvaluationRun
        fields = [
            "id", "suite", "suite_name", "name", "config", "status",
            "metrics_summary", "cost_summary", "result_summary",
            "triggered_by", "started_at", "finished_at", "created_at", "updated_at",
        ]
        read_only_fields = [
            "status", "metrics_summary", "cost_summary", "result_summary",
            "started_at", "finished_at", "created_at", "updated_at",
        ]

    def get_result_summary(self, obj):
        qs = obj.results.all()
        total = qs.count()
        completed = qs.filter(status="completed").count()
        failed = qs.filter(status="failed").count()
        return {"total": total, "completed": completed, "failed": failed}


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
