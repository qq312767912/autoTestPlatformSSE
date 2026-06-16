import logging

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from wharttest_django.viewsets import BaseModelViewSet
from wharttest_django.permissions import HasModelPermission
from wharttest_django.api_permissions import IsProjectMemberForResource
from wharttest_django.pagination import StandardPagination

from .models import ApiSyncConfig, ApiSyncHistory, ApiGlobalSyncConfig
from .serializers import (
    ApiSyncConfigSerializer,
    ApiSyncHistorySerializer,
    ApiGlobalSyncConfigSerializer,
)
from .tasks import sync_interface_data, batch_sync_interface_data

logger = logging.getLogger(__name__)


class ApiSyncConfigViewSet(BaseModelViewSet):
    serializer_class = ApiSyncConfigSerializer

    def get_permissions(self):
        return [
            permissions.IsAuthenticated(),
            HasModelPermission(),
            IsProjectMemberForResource(),
        ]

    def get_queryset(self):
        project_pk = self.kwargs.get('project_pk')
        qs = ApiSyncConfig.objects.filter(
            interface__project_id=project_pk,
        ).select_related('interface', 'testcase', 'step', 'created_by')

        interface_id = self.request.query_params.get('interface_id')
        testcase_id = self.request.query_params.get('testcase_id')
        step_id = self.request.query_params.get('step_id')

        if interface_id:
            qs = qs.filter(interface_id=interface_id)
        if testcase_id:
            qs = qs.filter(testcase_id=testcase_id)
        if step_id:
            qs = qs.filter(step_id=step_id)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def sync_now(self, request, **kwargs):
        """Trigger an immediate sync for this config."""
        sync_config = self.get_object()

        if sync_config.sync_enabled:
            task = sync_interface_data.delay(sync_config.id, request.user.id, sync_type='manual')
            return Response({
                'task_id': task.id,
                'config_id': sync_config.id,
                'used_global_config': False,
            })

        global_config = ApiGlobalSyncConfig.objects.filter(
            project=sync_config.interface.project,
            is_active=True, sync_enabled=True,
        ).first()
        if global_config:
            task = sync_interface_data.delay(
                None, request.user.id,
                interface_id=sync_config.interface_id,
                step_id=sync_config.step_id,
                sync_type='manual',
            )
            return Response({
                'task_id': task.id,
                'global_config_id': global_config.id,
                'used_global_config': True,
            })

        return Response(
            {"detail": "Sync config is disabled and no active global config found."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=False, methods=['post'])
    def batch_sync(self, request, **kwargs):
        """Trigger batch sync for multiple configs or interface-step pairs."""
        config_ids = request.data.get('config_ids', [])
        interface_step_pairs = request.data.get('interface_step_pairs', [])

        if not config_ids and not interface_step_pairs:
            return Response(
                {"detail": "Provide config_ids or interface_step_pairs."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate config_ids belong to current project
        project_pk = self.kwargs.get('project_pk')
        if config_ids:
            valid_count = ApiSyncConfig.objects.filter(
                id__in=config_ids, interface__project_id=project_pk,
            ).count()
            if valid_count != len(config_ids):
                return Response(
                    {"detail": "Some config IDs do not exist or do not belong to this project."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Validate interface_step_pairs belong to current project
        if interface_step_pairs:
            from api_interfaces.models import ApiInterface
            iface_ids = [p.get('interface_id') for p in interface_step_pairs if p.get('interface_id')]
            if iface_ids:
                valid_count = ApiInterface.objects.filter(
                    id__in=iface_ids, project_id=project_pk,
                ).count()
                if valid_count != len(iface_ids):
                    return Response(
                        {"detail": "Some interface IDs do not exist or do not belong to this project."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        task = batch_sync_interface_data.delay(
            config_ids, request.user.id, interface_step_pairs,
        )
        return Response({
            'task_id': task.id,
            'config_count': len(config_ids),
            'interface_step_pair_count': len(interface_step_pairs),
        })


class ApiSyncHistoryViewSet(BaseModelViewSet):
    """Read-heavy viewset for sync history with rollback support."""

    serializer_class = ApiSyncHistorySerializer
    pagination_class = StandardPagination
    http_method_names = ['get', 'post', 'head', 'options']

    def get_permissions(self):
        return [
            permissions.IsAuthenticated(),
            HasModelPermission(),
            IsProjectMemberForResource(),
        ]

    def get_queryset(self):
        project_pk = self.kwargs.get('project_pk')
        qs = ApiSyncHistory.objects.filter(
            sync_config__interface__project_id=project_pk,
        ).select_related('sync_config', 'sync_config__interface', 'operator')

        config_id = self.request.query_params.get('config_id')
        sync_type = self.request.query_params.get('sync_type')
        sync_status_param = self.request.query_params.get('sync_status')
        start_time = self.request.query_params.get('start_time')
        end_time = self.request.query_params.get('end_time')
        interface_id = self.request.query_params.get('interface_id')

        if config_id:
            qs = qs.filter(sync_config_id=config_id)
        if sync_type:
            qs = qs.filter(sync_type=sync_type)
        if sync_status_param:
            qs = qs.filter(sync_status=sync_status_param)
        if start_time:
            qs = qs.filter(sync_time__gte=start_time)
        if end_time:
            qs = qs.filter(sync_time__lte=end_time)
        if interface_id:
            qs = qs.filter(sync_config__interface_id=interface_id)
        return qs

    @action(detail=True, methods=['post'])
    def rollback(self, request, **kwargs):
        """Rollback a step to the state captured in this history entry."""
        history = self.get_object()
        try:
            with transaction.atomic():
                step = history.sync_config.step

                current_data = {
                    field: step.interface_data.get(field)
                    for field in history.sync_fields
                }

                step.interface_data.update(history.old_data)
                step.last_sync_time = timezone.now()
                step.save()

                new_history = ApiSyncHistory.objects.create(
                    sync_config=history.sync_config,
                    sync_type='manual',
                    sync_status='success',
                    sync_fields=history.sync_fields,
                    old_data=current_data,
                    new_data=history.old_data,
                    operator=request.user,
                )

                return Response(ApiSyncHistorySerializer(new_history).data)

        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return Response(
                {"detail": f"Rollback failed: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ApiGlobalSyncConfigViewSet(BaseModelViewSet):
    serializer_class = ApiGlobalSyncConfigSerializer

    def get_permissions(self):
        return [
            permissions.IsAuthenticated(),
            HasModelPermission(),
            IsProjectMemberForResource(),
        ]

    def get_queryset(self):
        project_pk = self.kwargs.get('project_pk')
        return ApiGlobalSyncConfig.objects.filter(
            project_id=project_pk,
        ).select_related('project', 'created_by')

    def perform_create(self, serializer):
        from projects.models import Project
        project = get_object_or_404(Project, pk=self.kwargs.get('project_pk'))
        serializer.save(created_by=self.request.user, project=project)

    @action(detail=True, methods=['post'])
    def set_active(self, request, **kwargs):
        """Mark this config as the active one for its project."""
        config = self.get_object()

        if config.is_active:
            return Response(self.get_serializer(config).data)

        with transaction.atomic():
            ApiGlobalSyncConfig.objects.filter(
                project=config.project,
            ).exclude(id=config.id).update(is_active=False)
            config.is_active = True
            config.save()

        return Response(self.get_serializer(config).data)

    @action(detail=False)
    def current_config(self, request, **kwargs):
        """Return the currently active global config for this project."""
        project_pk = self.kwargs.get('project_pk')
        config = ApiGlobalSyncConfig.objects.filter(
            project_id=project_pk, is_active=True,
        ).first()

        if not config:
            return Response(
                {"detail": "No active global sync config for this project."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(self.get_serializer(config).data)
