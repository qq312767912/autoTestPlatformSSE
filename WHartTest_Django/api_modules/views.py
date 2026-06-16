import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from wharttest_django.viewsets import BaseModelViewSet
from wharttest_django.permissions import HasModelPermission
from wharttest_django.api_permissions import IsProjectMemberForResource
from wharttest_django.pagination import StandardPagination

from .models import ApiModule
from .serializers import (
    ApiModuleSerializer,
    ApiModuleCreateSerializer,
    ApiModuleUpdateSerializer,
)

logger = logging.getLogger(__name__)


class ApiModuleViewSet(BaseModelViewSet):
    serializer_class = ApiModuleSerializer

    def get_permissions(self):
        return [IsAuthenticated(), HasModelPermission(), IsProjectMemberForResource()]

    def get_serializer_class(self):
        if self.action == 'create':
            return ApiModuleCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return ApiModuleUpdateSerializer
        return ApiModuleSerializer

    def get_queryset(self):
        project_pk = self.kwargs.get('project_pk')
        queryset = ApiModule.objects.filter(project_id=project_pk)
        if self.action == 'list':
            queryset = queryset.filter(parent=None)
        return queryset

    def perform_create(self, serializer):
        from projects.models import Project
        project = get_object_or_404(Project, pk=self.kwargs.get('project_pk'))
        serializer.save(created_by=self.request.user, project=project)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.children.exists():
            return Response(
                {'detail': 'Cannot delete a module that has child modules.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from api_interfaces.models import ApiInterface

        deleted_interface_ids = list(
            ApiInterface.objects.filter(module=instance).values_list('id', flat=True)
        )
        if deleted_interface_ids:
            ApiInterface.objects.filter(id__in=deleted_interface_ids).delete()

        self.perform_destroy(instance)
        return Response(
            {
                'deleted_interface_ids': deleted_interface_ids,
                'deleted_interface_count': len(deleted_interface_ids),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'])
    def tree(self, request, *args, **kwargs):
        project_pk = self.kwargs.get('project_pk')
        root_modules = ApiModule.objects.filter(
            project_id=project_pk, parent=None
        ).order_by('name')
        serializer = ApiModuleSerializer(root_modules, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def search(self, request, *args, **kwargs):
        project_pk = self.kwargs.get('project_pk')
        keyword = request.query_params.get('keyword', '')
        queryset = ApiModule.objects.filter(
            Q(name__icontains=keyword) | Q(description__icontains=keyword),
            project_id=project_pk,
        )
        serializer = ApiModuleSerializer(queryset, many=True)
        return Response(serializer.data)
