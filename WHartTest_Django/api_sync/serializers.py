from rest_framework import serializers

from .models import ApiSyncConfig, ApiSyncHistory, ApiGlobalSyncConfig

VALID_SYNC_FIELDS = [
    'method', 'url', 'headers', 'params', 'body',
    'setup_hooks', 'teardown_hooks', 'variables',
    'validators', 'extract',
]


class ApiSyncConfigSerializer(serializers.ModelSerializer):
    interface_info = serializers.SerializerMethodField()
    testcase_info = serializers.SerializerMethodField()
    step_info = serializers.SerializerMethodField()
    created_by_info = serializers.SerializerMethodField()

    class Meta:
        model = ApiSyncConfig
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at', 'created_by']

    def get_interface_info(self, obj):
        return {
            'id': obj.interface.id,
            'name': obj.interface.name,
            'method': obj.interface.method,
            'url': obj.interface.url,
        }

    def get_testcase_info(self, obj):
        return {
            'id': obj.testcase.id,
            'name': obj.testcase.name,
        }

    def get_step_info(self, obj):
        return {
            'id': obj.step.id,
            'name': obj.step.name,
            'order': obj.step.order,
        }

    def get_created_by_info(self, obj):
        if obj.created_by:
            return {'id': obj.created_by.id, 'username': obj.created_by.username}
        return None

    def validate(self, attrs):
        step = attrs.get('step')
        testcase = attrs.get('testcase')
        interface = attrs.get('interface')

        # Validate all referenced objects belong to the current project
        view = self.context.get('view')
        if view:
            project_pk = view.kwargs.get('project_pk')
            if project_pk:
                if interface and str(interface.project_id) != str(project_pk):
                    raise serializers.ValidationError('Interface does not belong to this project.')
                if testcase and str(testcase.project_id) != str(project_pk):
                    raise serializers.ValidationError('Test case does not belong to this project.')

        if step and testcase and step.testcase != testcase:
            raise serializers.ValidationError('Step does not belong to the specified test case.')

        if step and interface and step.origin_interface != interface:
            raise serializers.ValidationError('Step is not linked to the specified interface.')

        sync_fields = attrs.get('sync_fields', [])
        invalid = [f for f in sync_fields if f not in VALID_SYNC_FIELDS]
        if invalid:
            raise serializers.ValidationError(f'Invalid sync fields: {invalid}')

        return attrs


class ApiSyncHistorySerializer(serializers.ModelSerializer):
    sync_config_info = serializers.SerializerMethodField()
    operator_info = serializers.SerializerMethodField()

    class Meta:
        model = ApiSyncHistory
        fields = '__all__'
        read_only_fields = ['sync_time']

    def get_sync_config_info(self, obj):
        return {
            'id': obj.sync_config.id,
            'name': obj.sync_config.name,
            'interface': {
                'id': obj.sync_config.interface.id,
                'name': obj.sync_config.interface.name,
            },
            'testcase': {
                'id': obj.sync_config.testcase.id,
                'name': obj.sync_config.testcase.name,
            },
            'step': {
                'id': obj.sync_config.step.id,
                'name': obj.sync_config.step.name,
            },
        }

    def get_operator_info(self, obj):
        if obj.operator:
            return {'id': obj.operator.id, 'username': obj.operator.username}
        return None

    def validate(self, attrs):
        sync_config = attrs.get('sync_config')
        sync_fields = attrs.get('sync_fields', [])
        if sync_config:
            invalid = [f for f in sync_fields if f not in sync_config.sync_fields]
            if invalid:
                raise serializers.ValidationError(
                    f'Sync fields not in config allowed set: {invalid}'
                )
        return attrs


class ApiGlobalSyncConfigSerializer(serializers.ModelSerializer):
    created_by_info = serializers.SerializerMethodField()
    is_current = serializers.SerializerMethodField()
    sync_mode_display = serializers.SerializerMethodField()
    sync_fields_count = serializers.SerializerMethodField()

    class Meta:
        model = ApiGlobalSyncConfig
        fields = '__all__'
        read_only_fields = ['project', 'created_at', 'updated_at', 'created_by']

    def get_created_by_info(self, obj):
        if obj.created_by:
            return {'id': obj.created_by.id, 'username': obj.created_by.username}
        return None

    def get_is_current(self, obj):
        return obj.is_active

    def get_sync_mode_display(self, obj):
        return obj.get_sync_mode_display()

    def get_sync_fields_count(self, obj):
        return len(obj.sync_fields)

    def validate_sync_fields(self, value):
        invalid = [f for f in value if f not in VALID_SYNC_FIELDS]
        if invalid:
            raise serializers.ValidationError(f'Invalid sync fields: {invalid}')
        return value
