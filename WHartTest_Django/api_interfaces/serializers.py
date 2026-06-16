from rest_framework import serializers
from .models import ApiInterface, ApiInterfaceResult
from .payloads import normalize_key_value_pairs, normalize_request_body


class ApiInterfaceModuleInfoSerializer(serializers.Serializer):
    """Lightweight module serializer for embedding in interface detail."""
    id = serializers.IntegerField()
    name = serializers.CharField()
    parent = serializers.IntegerField(source='parent_id', allow_null=True)


class ApiInterfaceSerializer(serializers.ModelSerializer):
    module_info = ApiInterfaceModuleInfoSerializer(source='module', read_only=True)

    class Meta:
        model = ApiInterface
        fields = '__all__'
        read_only_fields = ['project', 'created_by', 'created_at', 'updated_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        try:
            data['headers'] = normalize_key_value_pairs(data.get('headers'), 'headers')
        except ValueError:
            data['headers'] = []
        try:
            data['params'] = normalize_key_value_pairs(data.get('params'), 'params')
        except ValueError:
            data['params'] = []
        try:
            data['body'] = normalize_request_body(data.get('body'))
        except ValueError:
            data['body'] = {'type': 'raw', 'content': data.get('body')}
        # Strip module_info from list responses, only include in detail
        request = self.context.get('request')
        view_kwargs = getattr(self.context.get('view'), 'kwargs', {}) or {}
        if request and not view_kwargs.get('pk'):
            data.pop('module_info', None)
        return data

    def validate(self, attrs):
        instance = getattr(self, 'instance', None)
        name = attrs.get('name')
        project = attrs.get('project') or (instance.project if instance else None)
        view = self.context.get('view')
        view_kwargs = getattr(view, 'kwargs', {}) or {}
        project_id = project.id if project else None

        if project_id is None and view:
            project_pk = view_kwargs.get('project_pk')
            if project_pk is not None:
                project_id = int(project_pk)

        if name and project_id is not None:
            query = ApiInterface.objects.filter(name=name, project_id=project_id)
            if instance:
                query = query.exclude(pk=instance.pk)
            if query.exists():
                raise serializers.ValidationError(
                    {"name": [f"An interface named '{name}' already exists in this project."]}
                )

        module = attrs.get('module', instance.module if instance else None)
        if module and project_id is not None and module.project_id != project_id:
            raise serializers.ValidationError(
                {"module": "Module must belong to the same project."}
            )

        if 'headers' in attrs:
            try:
                attrs['headers'] = normalize_key_value_pairs(attrs.get('headers'), 'headers')
            except ValueError as exc:
                raise serializers.ValidationError({"headers": str(exc)}) from exc
        elif instance is None:
            attrs['headers'] = []

        if 'params' in attrs:
            try:
                attrs['params'] = normalize_key_value_pairs(attrs.get('params'), 'params')
            except ValueError as exc:
                raise serializers.ValidationError({"params": str(exc)}) from exc
        elif instance is None:
            attrs['params'] = []

        if 'body' in attrs:
            try:
                attrs['body'] = normalize_request_body(attrs.get('body'))
            except ValueError as exc:
                raise serializers.ValidationError({"body": str(exc)}) from exc
        elif instance is None:
            attrs['body'] = {'type': 'none', 'content': None}

        setup_hooks = attrs.get('setup_hooks', [])
        if not isinstance(setup_hooks, list):
            raise serializers.ValidationError({"setup_hooks": "Must be a list."})

        teardown_hooks = attrs.get('teardown_hooks', [])
        if not isinstance(teardown_hooks, list):
            raise serializers.ValidationError({"teardown_hooks": "Must be a list."})

        variables = attrs.get('variables', {})
        if not isinstance(variables, dict):
            raise serializers.ValidationError({"variables": "Must be a dict."})

        validators = attrs.get('validators', [])
        if not isinstance(validators, list):
            raise serializers.ValidationError({"validators": "Must be a list."})

        supported_comparators = [
            'eq', 'ne', 'gt', 'ge', 'gte', 'lt', 'le', 'lte',
            'contains', 'contained_by', 'type_match', 'regex_match',
            'startswith', 'endswith', 'str_eq',
            'length_equal', 'length_greater_than', 'length_less_than',
            'length_greater_or_equals', 'length_less_or_equals',
        ]

        for validator in validators:
            if not isinstance(validator, dict):
                raise serializers.ValidationError({"validators": "Each validator must be a dict."})
            if "check" in validator and "expect" in validator:
                continue
            valid_format = False
            for key in validator.keys():
                if key in supported_comparators:
                    if not isinstance(validator[key], list) or len(validator[key]) != 2:
                        raise serializers.ValidationError({
                            "validators": f"Validator '{key}' must be a list of [field, expected_value]."
                        })
                    valid_format = True
                    break
            if not valid_format:
                raise serializers.ValidationError({
                    "validators": (
                        f"Validator must use a supported comparator: "
                        f"{', '.join(supported_comparators)}, or use check/expect format."
                    )
                })

        extract = attrs.get('extract', {})
        if not isinstance(extract, dict):
            raise serializers.ValidationError({"extract": "Must be a dict."})

        return attrs


class ApiInterfaceResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiInterfaceResult
        fields = '__all__'
        read_only_fields = ['executed_by', 'executed_at']
