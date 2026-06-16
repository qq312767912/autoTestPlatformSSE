from rest_framework import serializers
from django.db import transaction, models
from .models import (
    ApiTestCase, ApiTestCaseStep, ApiTestReport, ApiTestReportDetail,
    ApiTestCaseTag, ApiTestCaseGroup
)


class ApiTestCaseTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiTestCaseTag
        fields = ['id', 'name', 'color', 'project', 'created_by', 'created_at']
        read_only_fields = ['project', 'created_by', 'created_at']


class ApiTestCaseGroupSerializer(serializers.ModelSerializer):
    full_path = serializers.CharField(source='get_full_path', read_only=True)
    children = serializers.SerializerMethodField()

    class Meta:
        model = ApiTestCaseGroup
        fields = [
            'id', 'name', 'parent', 'project',
            'created_by', 'created_at', 'full_path', 'children'
        ]
        read_only_fields = ['project', 'created_by', 'created_at']

    def get_children(self, obj):
        children = obj.children.all()
        if children:
            return ApiTestCaseGroupSerializer(children, many=True).data
        return []


class ApiTestCaseStepSerializer(serializers.ModelSerializer):
    interface_id = serializers.IntegerField(write_only=True, required=False)
    interface_info = serializers.SerializerMethodField(read_only=True)
    interface_data = serializers.JSONField(required=False)

    class Meta:
        model = ApiTestCaseStep
        fields = [
            'id', 'name', 'order', 'interface_id',
            'interface_info', 'interface_data', 'config',
            'sync_fields', 'last_sync_time'
        ]
        read_only_fields = ['last_sync_time']

    def get_interface_info(self, obj):
        if obj.origin_interface:
            return {
                'id': obj.origin_interface.id,
                'name': obj.origin_interface.name,
                'method': obj.origin_interface.method,
                'url': obj.origin_interface.url,
                'module': {
                    'id': obj.origin_interface.module.id,
                    'name': obj.origin_interface.module.name
                } if obj.origin_interface.module else None,
                'project': {
                    'id': obj.origin_interface.project.id,
                    'name': obj.origin_interface.project.name
                }
            }
        return None

    def _build_interface_data(self, interface):
        return {
            'method': interface.method,
            'url': interface.url,
            'headers': interface.headers or [],
            'params': interface.params or [],
            'body': interface.body or {'type': 'raw', 'content': ''},
            'extract': {},
            'variables': {},
            'validators': [],
            'setup_hooks': [],
            'teardown_hooks': []
        }

    def _create_sync_config(self, step, interface):
        from api_sync.models import ApiSyncConfig, ApiGlobalSyncConfig

        existing_config = ApiSyncConfig.objects.filter(
            interface=interface,
            step=step
        ).first()

        if not existing_config:
            global_config = ApiGlobalSyncConfig.objects.filter(
                project=interface.project,
                is_active=True,
                sync_enabled=True
            ).first()

            if global_config:
                sync_fields = global_config.sync_fields
                sync_trigger = {
                    "fields_to_watch": sync_fields if global_config.sync_mode == 'auto' else []
                }
                ApiSyncConfig.objects.create(
                    name=f'Auto-created - {interface.name}',
                    description='Auto-created from global sync config',
                    interface=interface,
                    testcase=step.testcase,
                    step=step,
                    sync_fields=sync_fields,
                    sync_enabled=global_config.sync_enabled,
                    sync_mode=global_config.sync_mode,
                    sync_trigger=sync_trigger,
                    created_by=self.context['request'].user if 'request' in self.context else None
                )
                return True
        return False

    def _get_project_pk(self):
        """Get project_pk from serializer context (passed by DRF ViewSet)."""
        view = self.context.get('view')
        return view.kwargs.get('project_pk') if view else None

    def create(self, validated_data):
        from api_interfaces.models import ApiInterface

        interface_id = validated_data.pop('interface_id')
        project_pk = self._get_project_pk()
        interface = ApiInterface.objects.get(id=interface_id, project_id=project_pk)
        validated_data['interface_data'] = self._build_interface_data(interface)
        validated_data['origin_interface'] = interface
        step = super().create(validated_data)
        self._create_sync_config(step, interface)
        return step

    def update(self, instance, validated_data):
        from api_interfaces.models import ApiInterface

        old_interface = instance.origin_interface
        new_interface = None

        if 'interface_id' in validated_data:
            interface_id = validated_data.pop('interface_id')
            project_pk = self._get_project_pk()
            new_interface = ApiInterface.objects.get(id=interface_id, project_id=project_pk)
            current_data = instance.interface_data or {}
            new_data = self._build_interface_data(new_interface)
            new_data.update({
                'extract': current_data.get('extract', {}),
                'variables': current_data.get('variables', {}),
                'validators': current_data.get('validators', []),
                'setup_hooks': current_data.get('setup_hooks', []),
                'teardown_hooks': current_data.get('teardown_hooks', [])
            })
            validated_data['interface_data'] = new_data
            validated_data['origin_interface'] = new_interface

        step = super().update(instance, validated_data)

        if new_interface and new_interface != old_interface:
            from api_sync.models import ApiSyncConfig
            ApiSyncConfig.objects.filter(step=step).delete()
            self._create_sync_config(step, new_interface)

        return step


class ApiTestCaseSerializer(serializers.ModelSerializer):
    steps = ApiTestCaseStepSerializer(many=True, read_only=True)
    steps_info = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False,
    )
    tags_info = ApiTestCaseTagSerializer(source='tags', many=True, read_only=True)
    group_info = ApiTestCaseGroupSerializer(source='group', read_only=True)
    related_interfaces = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(
        source='created_by.username', read_only=True, default=''
    )

    def get_related_interfaces(self, obj):
        interfaces = {}
        for step in obj.steps.all().select_related(
            'origin_interface__module', 'origin_interface__project'
        ):
            if step.origin_interface:
                interface = step.origin_interface
                module_name = interface.module.name if interface.module else "Uncategorized"
                if module_name not in interfaces:
                    interfaces[module_name] = []
                interfaces[module_name].append({
                    'id': interface.id,
                    'name': interface.name,
                    'method': interface.method,
                    'url': interface.url,
                    'step_name': step.name,
                    'step_order': step.order
                })
        return [
            {
                'module': module_name,
                'interfaces': sorted(interface_list, key=lambda x: x['step_order'])
            }
            for module_name, interface_list in interfaces.items()
        ]

    class Meta:
        model = ApiTestCase
        fields = [
            'id', 'name', 'description', 'priority',
            'config', 'project', 'group',
            'tags', 'tags_info', 'group_info',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
            'steps', 'steps_info', 'related_interfaces'
        ]
        read_only_fields = ['project', 'created_by', 'created_at', 'updated_at']

    def _get_project_pk(self):
        """Get project_pk from serializer context (passed by DRF ViewSet)."""
        view = self.context.get('view')
        return view.kwargs.get('project_pk') if view else None

    def _build_interface_data(self, interface):
        return {
            'method': interface.method,
            'url': interface.url,
            'headers': interface.headers or [],
            'params': interface.params or [],
            'body': interface.body or {'type': 'raw', 'content': ''},
            'extract': {},
            'variables': {},
            'validators': [],
            'setup_hooks': [],
            'teardown_hooks': []
        }

    def _create_sync_config(self, step, interface):
        from api_sync.models import ApiSyncConfig, ApiGlobalSyncConfig

        existing_config = ApiSyncConfig.objects.filter(
            interface=interface,
            step=step
        ).first()

        if not existing_config:
            global_config = ApiGlobalSyncConfig.objects.filter(
                project=interface.project,
                is_active=True,
                sync_enabled=True
            ).first()

            if global_config:
                sync_fields = global_config.sync_fields
                sync_trigger = {
                    "fields_to_watch": sync_fields if global_config.sync_mode == 'auto' else []
                }
                ApiSyncConfig.objects.create(
                    name=f'Auto-created - {interface.name}',
                    description='Auto-created from global sync config',
                    interface=interface,
                    testcase=step.testcase,
                    step=step,
                    sync_fields=sync_fields,
                    sync_enabled=global_config.sync_enabled,
                    sync_mode=global_config.sync_mode,
                    sync_trigger=sync_trigger,
                    created_by=self.context['request'].user if 'request' in self.context else None
                )

    def _create_step(self, instance, step_data, order):
        from api_interfaces.models import ApiInterface

        interface_id = step_data.pop('interface_id')
        project_pk = self._get_project_pk()
        interface = ApiInterface.objects.get(id=interface_id, project_id=project_pk)
        user_interface_data = step_data.pop('interface_data', None)

        if user_interface_data:
            interface_data = {
                'method': user_interface_data.get('method', interface.method),
                'url': user_interface_data.get('url', interface.url),
                'headers': user_interface_data.get('headers', []),
                'params': user_interface_data.get('params', []),
                'body': user_interface_data.get('body', {'type': 'raw', 'content': ''}),
                'extract': user_interface_data.get('extract', {}),
                'variables': user_interface_data.get('variables', {}),
                'validators': user_interface_data.get('validators', []),
                'setup_hooks': user_interface_data.get('setup_hooks', []),
                'teardown_hooks': user_interface_data.get('teardown_hooks', [])
            }
        else:
            interface_data = self._build_interface_data(interface)

        step_data.pop('order', None)
        step = ApiTestCaseStep.objects.create(
            testcase=instance,
            order=order,
            origin_interface=interface,
            interface_data=interface_data,
            **step_data
        )
        self._create_sync_config(step, interface)
        return step

    def create(self, validated_data):
        steps_info = validated_data.pop('steps_info', [])
        validated_data['created_by'] = self.context['request'].user
        instance = super().create(validated_data)

        for step_data in steps_info:
            if 'order' not in step_data:
                max_order = instance.steps.aggregate(
                    max_order=models.Max('order')
                )['max_order'] or 0
                step_data['order'] = max_order + 1
            self._create_step(
                instance=instance,
                step_data=step_data.copy(),
                order=step_data['order']
            )

        instance.refresh_from_db()
        return instance

    def update(self, instance, validated_data):
        steps_info = validated_data.pop('steps_info', None)
        update_mode = validated_data.pop('update_mode', 'auto')

        instance = super().update(instance, validated_data)

        if steps_info is not None:
            from api_interfaces.models import ApiInterface

            with transaction.atomic():
                existing_steps = {step.order: step for step in instance.steps.all()}
                existing_orders = set(existing_steps.keys())

                for step_data in steps_info:
                    if 'order' not in step_data:
                        max_order = max(existing_orders) if existing_orders else 0
                        step_data['order'] = max_order + 1

                    current_order = step_data['order']

                    if current_order in existing_orders:
                        existing_step = existing_steps[current_order]
                        is_same_interface = (
                            'interface_id' in step_data
                            and existing_step.origin_interface
                            and existing_step.origin_interface.id == step_data['interface_id']
                        )
                        is_same_name = step_data.get('name') == existing_step.name

                        if is_same_interface and is_same_name:
                            continue

                        if update_mode == 'update':
                            update_data = step_data.copy()
                            interface_id = update_data.pop('interface_id', None)

                            if interface_id:
                                project_pk = self._get_project_pk()
                                interface = ApiInterface.objects.get(id=interface_id, project_id=project_pk)
                                current_data = existing_step.interface_data or {}
                                user_interface_data = update_data.pop('interface_data', {})

                                if user_interface_data:
                                    interface_data = self._build_interface_data(interface)
                                    interface_data.update({
                                        'extract': user_interface_data.get('extract', current_data.get('extract', {})),
                                        'variables': user_interface_data.get('variables', current_data.get('variables', {})),
                                        'validators': user_interface_data.get('validators', current_data.get('validators', [])),
                                        'setup_hooks': user_interface_data.get('setup_hooks', current_data.get('setup_hooks', [])),
                                        'teardown_hooks': user_interface_data.get('teardown_hooks', current_data.get('teardown_hooks', []))
                                    })
                                else:
                                    interface_data = self._build_interface_data(interface)
                                    interface_data.update({
                                        'extract': current_data.get('extract', {}),
                                        'variables': current_data.get('variables', {}),
                                        'validators': current_data.get('validators', []),
                                        'setup_hooks': current_data.get('setup_hooks', []),
                                        'teardown_hooks': current_data.get('teardown_hooks', [])
                                    })

                                existing_step.interface_data = interface_data
                                existing_step.origin_interface = interface

                            if 'name' in update_data:
                                existing_step.name = update_data['name']
                            if 'sync_fields' in update_data:
                                existing_step.sync_fields = update_data['sync_fields']
                            if 'config' in update_data:
                                existing_step.config = update_data['config']

                            existing_step.save()
                            continue

                        # Auto mode: adjust ordering for conflict
                        steps_to_update = instance.steps.filter(
                            order__gte=current_order
                        ).order_by('-order')
                        for s in steps_to_update:
                            s.order += 1
                            s.save()
                        existing_steps = {
                            (s.order + 1 if s.order >= current_order else s.order): s
                            for s in existing_steps.values()
                        }
                        existing_orders = set(existing_steps.keys())
                        existing_orders.add(current_order)
                    else:
                        existing_orders.add(current_order)

                    step = self._create_step(
                        instance=instance,
                        step_data=step_data.copy(),
                        order=current_order
                    )
                    existing_steps[current_order] = step

            instance.refresh_from_db()

        return instance


class ApiTestReportDetailSerializer(serializers.ModelSerializer):
    step_name = serializers.CharField(source='step.name', read_only=True)

    class Meta:
        model = ApiTestReportDetail
        fields = [
            'id', 'step_name', 'success', 'elapsed',
            'request', 'response', 'validators',
            'extracted_variables', 'attachment'
        ]


class ApiTestReportListSerializer(serializers.ModelSerializer):
    testcase_name = serializers.CharField(source='testcase.name', read_only=True)
    success_rate = serializers.SerializerMethodField()
    environment_name = serializers.CharField(
        source='environment.name', read_only=True, default=''
    )
    executed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ApiTestReport
        fields = [
            'id', 'name', 'testcase_name', 'status',
            'success_count', 'fail_count', 'error_count',
            'success_rate', 'duration', 'start_time',
            'environment_name', 'executed_by_name'
        ]

    def get_success_rate(self, obj):
        total = obj.success_count + obj.fail_count + obj.error_count
        if total == 0:
            return "0"
        rate = obj.success_count / total
        return "1" if rate == 1 else f"{rate:.2f}"

    def get_executed_by_name(self, obj):
        if obj.executed_by:
            return obj.executed_by.username
        return ""


class ApiTestReportSerializer(serializers.ModelSerializer):
    testcase_name = serializers.CharField(source='testcase.name', read_only=True)
    details = ApiTestReportDetailSerializer(many=True, read_only=True)
    success_rate = serializers.SerializerMethodField()
    environment_info = serializers.SerializerMethodField()
    executed_by_info = serializers.SerializerMethodField()

    class Meta:
        model = ApiTestReport
        fields = [
            'id', 'name', 'status', 'success_count',
            'fail_count', 'error_count', 'duration',
            'start_time', 'summary', 'testcase',
            'testcase_name', 'environment', 'environment_info',
            'executed_by', 'executed_by_info', 'details', 'success_rate'
        ]
        read_only_fields = [
            'name', 'status', 'success_count', 'fail_count',
            'error_count', 'duration', 'start_time', 'summary',
            'executed_by', 'success_rate', 'environment_info',
            'executed_by_info'
        ]

    def get_success_rate(self, obj):
        total = obj.success_count + obj.fail_count + obj.error_count
        if total == 0:
            return "0"
        rate = obj.success_count / total
        return "1" if rate == 1 else f"{rate:.2f}"

    def get_environment_info(self, obj):
        if obj.environment:
            return {
                'id': obj.environment.id,
                'name': obj.environment.name,
                'base_url': obj.environment.base_url,
                'description': obj.environment.description,
                'project': {
                    'id': obj.environment.project.id,
                    'name': obj.environment.project.name
                }
            }
        return None

    def get_executed_by_info(self, obj):
        if obj.executed_by:
            return {
                'id': obj.executed_by.id,
                'username': obj.executed_by.username,
                'email': obj.executed_by.email,
                'first_name': obj.executed_by.first_name,
                'last_name': obj.executed_by.last_name
            }
        return None


class InterfaceOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    method = serializers.CharField()
    url = serializers.CharField()
