from rest_framework import serializers

from .models import FeedbackEvent, GenerationOutput, RetrievalTrace


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
