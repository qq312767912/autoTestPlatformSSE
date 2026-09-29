from django.contrib import admin

from .models import FeedbackEvent, GenerationOutput, RetrievalTrace


@admin.register(RetrievalTrace)
class RetrievalTraceAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "task_type", "status", "created_at")
    list_filter = ("task_type", "status", "created_at")
    search_fields = ("query", "task_id")


@admin.register(GenerationOutput)
class GenerationOutputAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "task_type", "output_hash", "created_at")
    list_filter = ("task_type", "created_at")


@admin.register(FeedbackEvent)
class FeedbackEventAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "signal", "actor_type", "occurred_at")
    list_filter = ("signal", "actor_type", "occurred_at")
