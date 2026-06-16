import logging

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db import transaction
from django.shortcuts import get_object_or_404

from wharttest_django.viewsets import BaseModelViewSet
from wharttest_django.permissions import HasModelPermission
from wharttest_django.pagination import StandardPagination
from wharttest_django.api_permissions import IsProjectMemberForResource


class _IsProjectMemberForVariable(IsProjectMemberForResource):
    """Resolve project through environment FK for ApiEnvironmentVariable."""

    def get_project_from_object(self, obj):
        env = getattr(obj, 'environment', None)
        if env:
            return getattr(env, 'project', None)
        return None

from .models import ApiEnvironment, ApiEnvironmentVariable, ApiGlobalRequestHeader
from .serializers import (
    ApiEnvironmentSerializer,
    ApiEnvironmentVariableSerializer,
    ApiGlobalRequestHeaderSerializer,
)

logger = logging.getLogger(__name__)


class ApiEnvironmentViewSet(BaseModelViewSet):
    serializer_class = ApiEnvironmentSerializer

    def get_permissions(self):
        return [
            permissions.IsAuthenticated(),
            HasModelPermission(),
            IsProjectMemberForResource(),
        ]

    def get_queryset(self):
        project_pk = self.kwargs.get('project_pk')
        return ApiEnvironment.objects.filter(project_id=project_pk)

    def perform_create(self, serializer):
        from projects.models import Project
        project = get_object_or_404(Project, pk=self.kwargs.get('project_pk'))
        serializer.save(created_by=self.request.user, project=project)

    @action(detail=True, methods=['post'])
    def clone(self, request, **kwargs):
        """Clone an environment and all its variables within the same project."""
        environment = self.get_object()
        project_pk = self.kwargs.get('project_pk')
        name = request.data.get('name')

        from projects.models import Project

        try:
            with transaction.atomic():
                target_project = get_object_or_404(Project, pk=project_pk)

                if not name:
                    name = f"{environment.name}_copy"

                if ApiEnvironment.objects.filter(project=target_project, name=name).exists():
                    return Response(
                        {"detail": "An environment with this name already exists in the target project."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                new_env = ApiEnvironment.objects.create(
                    name=name,
                    base_url=environment.base_url,
                    verify_ssl=environment.verify_ssl,
                    description=environment.description,
                    project=target_project,
                    created_by=request.user,
                )

                for var in environment.api_variables.all():
                    ApiEnvironmentVariable.objects.create(
                        environment=new_env,
                        name=var.name,
                        value=var.value,
                        type=var.type,
                        description=var.description,
                        is_sensitive=var.is_sensitive,
                    )

            serializer = self.get_serializer(new_env)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Environment clone failed: {e}")
            return Response(
                {"detail": "Environment clone failed."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ApiEnvironmentVariableViewSet(BaseModelViewSet):
    serializer_class = ApiEnvironmentVariableSerializer

    def get_permissions(self):
        return [
            permissions.IsAuthenticated(),
            HasModelPermission(),
            _IsProjectMemberForVariable(),
        ]

    def get_queryset(self):
        project_pk = self.kwargs.get('project_pk')
        qs = ApiEnvironmentVariable.objects.filter(
            environment__project_id=project_pk,
        )
        environment_id = self.request.query_params.get('environment_id')
        if environment_id:
            qs = qs.filter(environment_id=environment_id)
        return qs

    def perform_create(self, serializer):
        serializer.save()

    @action(detail=False, methods=['post'])
    def batch_create(self, request, **kwargs):
        """Create multiple variables for a given environment in one request."""
        environment_id = request.data.get('environment_id')
        variables_data = request.data.get('variables', [])

        if not environment_id or not variables_data:
            return Response(
                {"detail": "environment_id and variables are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        project_pk = self.kwargs.get('project_pk')
        environment = get_object_or_404(
            ApiEnvironment, id=environment_id, project_id=project_pk,
        )

        created = []
        with transaction.atomic():
            for var_data in variables_data:
                var_data['environment'] = environment.id
                serializer = self.get_serializer(data=var_data)
                serializer.is_valid(raise_exception=True)
                created.append(serializer.save())

        out = self.get_serializer(created, many=True)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def batch_update(self, request, **kwargs):
        """Update multiple variables in one request."""
        variables_data = request.data.get('variables', [])
        if not variables_data:
            return Response(
                {"detail": "variables list is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        project_pk = self.kwargs.get('project_pk')
        updated = []
        with transaction.atomic():
            for var_data in variables_data:
                var_id = var_data.pop('id', None)
                if not var_id:
                    continue
                variable = get_object_or_404(
                    ApiEnvironmentVariable, id=var_id,
                    environment__project_id=project_pk,
                )
                serializer = self.get_serializer(variable, data=var_data, partial=True)
                serializer.is_valid(raise_exception=True)
                updated.append(serializer.save())

        out = self.get_serializer(updated, many=True)
        return Response(out.data)


class ApiGlobalRequestHeaderViewSet(BaseModelViewSet):
    serializer_class = ApiGlobalRequestHeaderSerializer

    def get_permissions(self):
        return [
            permissions.IsAuthenticated(),
            HasModelPermission(),
            IsProjectMemberForResource(),
        ]

    def get_queryset(self):
        project_pk = self.kwargs.get('project_pk')
        return ApiGlobalRequestHeader.objects.filter(project_id=project_pk)

    def perform_create(self, serializer):
        from projects.models import Project
        project = get_object_or_404(Project, pk=self.kwargs.get('project_pk'))
        serializer.save(created_by=self.request.user, project=project)
