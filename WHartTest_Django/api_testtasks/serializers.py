from rest_framework import serializers
from django.db import models

from .models import (
    ApiTestTaskSuite,
    ApiTestTaskCase,
    ApiTestTaskExecution,
    ApiTestTaskCaseResult,
)
from api_testcases.serializers import ApiTestReportSerializer


class ApiTestTaskCaseSimpleSerializer(serializers.ModelSerializer):
    testcase_id = serializers.IntegerField(source='testcase.id', read_only=True)
    testcase_name = serializers.CharField(source='testcase.name', read_only=True)
    description = serializers.CharField(source='testcase.description', read_only=True)
    priority = serializers.CharField(source='testcase.priority', read_only=True)

    class Meta:
        model = ApiTestTaskCase
        fields = ['id', 'testcase_id', 'testcase_name', 'description', 'priority', 'order']


class ApiTestTaskSuiteSerializer(serializers.ModelSerializer):
    task_cases = ApiTestTaskCaseSimpleSerializer(
        source='api_task_cases', many=True, read_only=True
    )
    created_by_name = serializers.CharField(
        source='created_by.username', read_only=True, default=''
    )
    project_name = serializers.CharField(
        source='project.name', read_only=True, default=''
    )
    test_cases = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        write_only=True,
        help_text='List of test case IDs to associate with this suite',
    )

    class Meta:
        model = ApiTestTaskSuite
        fields = [
            'id', 'name', 'description', 'priority', 'fail_fast',
            'project', 'project_name', 'created_by', 'created_by_name',
            'created_at', 'updated_at', 'task_cases', 'test_cases',
        ]
        read_only_fields = ['id', 'project', 'created_by', 'created_at', 'updated_at']

    def create(self, validated_data):
        test_cases = validated_data.pop('test_cases', [])
        instance = super().create(validated_data)
        if test_cases:
            from .services import ApiTestTaskService
            ApiTestTaskService.add_testcases(instance, test_cases)
        return instance

    def update(self, instance, validated_data):
        test_cases = validated_data.pop('test_cases', None)
        instance = super().update(instance, validated_data)
        if test_cases is not None:
            instance.api_task_cases.all().delete()
            from .services import ApiTestTaskService
            ApiTestTaskService.add_testcases(instance, test_cases)
        return instance


class ApiTestTaskCaseCreateSerializer(serializers.Serializer):
    testcase_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text='List of test case IDs to add',
    )

    def validate_testcase_ids(self, value):
        from api_testcases.models import ApiTestCase
        project_pk = self.context.get('project_pk')
        qs = ApiTestCase.objects.filter(id__in=value)
        if project_pk:
            qs = qs.filter(project_id=project_pk)
        count = qs.count()
        if count != len(value):
            raise serializers.ValidationError("Some test cases do not exist or do not belong to this project.")
        return value


class ApiTestTaskCaseResultSerializer(serializers.ModelSerializer):
    testcase_name = serializers.CharField(source='testcase.name', read_only=True)
    report = ApiTestReportSerializer(read_only=True)

    class Meta:
        model = ApiTestTaskCaseResult
        fields = [
            'id', 'testcase', 'testcase_name', 'status',
            'start_time', 'end_time', 'duration',
            'error_message', 'report',
        ]
        read_only_fields = fields


class ApiTestTaskExecutionSerializer(serializers.ModelSerializer):
    task_suite_name = serializers.CharField(source='task_suite.name', read_only=True)
    executed_by_name = serializers.CharField(
        source='executed_by.username', read_only=True, default=''
    )
    environment_name = serializers.CharField(
        source='environment.name', read_only=True, default=''
    )
    case_results = ApiTestTaskCaseResultSerializer(
        source='api_case_results', many=True, read_only=True
    )
    duration = serializers.FloatField(read_only=True)
    success_rate = serializers.FloatField(read_only=True)

    class Meta:
        model = ApiTestTaskExecution
        fields = [
            'id', 'task_suite', 'task_suite_name', 'status',
            'environment', 'environment_name',
            'start_time', 'end_time', 'duration',
            'total_count', 'success_count', 'fail_count', 'error_count',
            'success_rate', 'executed_by', 'executed_by_name',
            'created_at', 'case_results',
        ]
        read_only_fields = [
            'id', 'status', 'start_time', 'end_time',
            'total_count', 'success_count', 'fail_count', 'error_count',
            'executed_by', 'created_at',
        ]


class ApiTestTaskExecutionListSerializer(serializers.ModelSerializer):
    task_suite_name = serializers.CharField(
        source='task_suite.name', read_only=True, default=''
    )
    environment_name = serializers.CharField(
        source='environment.name', read_only=True, default=''
    )
    executed_by_name = serializers.CharField(
        source='executed_by.username', read_only=True, default=''
    )
    duration = serializers.FloatField(read_only=True)
    success_rate = serializers.FloatField(read_only=True)

    class Meta:
        model = ApiTestTaskExecution
        fields = [
            'id', 'task_suite', 'task_suite_name',
            'status', 'environment', 'environment_name',
            'start_time', 'end_time', 'duration',
            'total_count', 'success_rate',
            'executed_by', 'executed_by_name', 'created_at',
        ]
        read_only_fields = fields


class ApiTestTaskExecutionCreateSerializer(serializers.Serializer):
    task_suite_id = serializers.IntegerField(help_text='Task suite ID')
    environment_id = serializers.IntegerField(
        required=False, allow_null=True, help_text='Environment ID'
    )

    def validate_task_suite_id(self, value):
        try:
            lookup = {'id': value}
            project_pk = self.context.get('project_pk')
            if project_pk:
                lookup['project_id'] = project_pk
            suite = ApiTestTaskSuite.objects.get(**lookup)
            if suite.api_task_cases.count() == 0:
                raise serializers.ValidationError("Task suite has no test cases.")
            return value
        except ApiTestTaskSuite.DoesNotExist:
            raise serializers.ValidationError("Task suite does not exist.")
