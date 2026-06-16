from rest_framework import serializers
from .models import ApiModule


class ApiModuleSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()

    class Meta:
        model = ApiModule
        fields = '__all__'
        read_only_fields = ['project', 'created_by', 'created_at', 'updated_at']

    def get_children(self, obj):
        children = obj.children.all()
        if children:
            return ApiModuleSerializer(children, many=True).data
        return []


class ApiModuleCreateSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True)

    class Meta:
        model = ApiModule
        fields = ['id', 'name', 'parent', 'description']

    def validate(self, attrs):
        parent = attrs.get('parent')
        # project will be set in perform_create from URL kwargs
        if parent:
            view = self.context.get('view')
            project_pk = view.kwargs.get('project_pk') if view else None
            if project_pk and parent.project_id != int(project_pk):
                raise serializers.ValidationError(
                    {'parent': 'Parent module must belong to the same project.'}
                )
        return attrs


class ApiModuleUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiModule
        fields = ['name', 'description']
