import json
import logging
from copy import deepcopy
from types import SimpleNamespace

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from wharttest_django.viewsets import BaseModelViewSet
from wharttest_django.permissions import HasModelPermission
from wharttest_django.api_permissions import IsProjectMemberForResource
from wharttest_django.pagination import StandardPagination

from .models import ApiInterface, ApiInterfaceResult
from .serializers import ApiInterfaceSerializer, ApiInterfaceResultSerializer
from .runner import InterfaceRunner

logger = logging.getLogger(__name__)


class ApiInterfaceViewSet(BaseModelViewSet):
    serializer_class = ApiInterfaceSerializer
    pagination_class = StandardPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasModelPermission(), IsProjectMemberForResource()]

    def get_queryset(self):
        project_pk = self.kwargs.get('project_pk')
        queryset = ApiInterface.objects.filter(project_id=project_pk)

        no_module = self.request.query_params.get('no_module')
        if no_module and no_module.lower() in ('true', '1', 'yes'):
            return queryset.filter(module__isnull=True)

        module_id = self.request.query_params.get('module_id')
        if module_id:
            queryset = queryset.filter(module_id=module_id)

        return queryset

    def perform_create(self, serializer):
        from projects.models import Project
        project = get_object_or_404(Project, pk=self.kwargs.get('project_pk'))
        serializer.save(created_by=self.request.user, project=project)

    @action(detail=True, methods=['post'])
    def run(self, request, *args, **kwargs):
        """Run an interface with optional environment configuration."""
        interface = self.get_object()
        environment_id = request.data.get('environment_id')

        interface_type = interface.type
        environment = None
        env_config = {}

        if environment_id:
            try:
                from api_environments.models import ApiEnvironment as Environment
                project_pk = self.kwargs.get('project_pk')
                environment = get_object_or_404(Environment, id=environment_id, project_id=project_pk)

                if hasattr(environment, 'get_all_variables') and callable(
                    environment.get_all_variables
                ):
                    env_config['variables'] = environment.get_all_variables()

                if interface_type == 'sql' and hasattr(environment, 'get_database_config'):
                    db_config = environment.get_database_config()
                    if db_config:
                        env_config['db_config'] = {
                            'user': db_config.username,
                            'password': db_config.password,
                            'ip': db_config.host,
                            'port': db_config.port,
                            'database': db_config.database,
                        }
            except Exception as e:
                logger.error(f"Failed to load environment {environment_id}: {str(e)}")
                return Response(
                    {'detail': f'Failed to load environment: {str(e)}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Build interface data
        interface_data = interface.get_interface_data()
        interface_data['project_id'] = interface.project_id

        if environment:
            interface_data['base_url'] = getattr(environment, 'base_url', '') or ''
            interface_data['verify'] = getattr(environment, 'verify_ssl', None)

        try:
            runner = InterfaceRunner(interface_data)

            if env_config.get('variables'):
                runner.variables = runner.variables or {}
                runner.variables.update(env_config['variables'])

            if env_config.get('db_config') and interface_type == 'sql':
                runner.interface_data['db_config'] = env_config['db_config']

            runner.run_interface()
            response_data = runner.get_response()

            # Save result
            ApiInterfaceResult.objects.create(
                interface=interface,
                environment_id=environment_id,
                success=response_data.get('success', False),
                elapsed=response_data.get('elapsed', 0),
                request_data=response_data.get('request', {}),
                response_data=response_data.get('response', {}),
                validation_results=response_data.get('validation_results', []),
                extracted_variables=response_data.get('extracted_variables', {}),
                executed_by=request.user,
            )

            return Response(response_data)

        except Exception as e:
            logger.error(f"Interface run failed: {str(e)}")
            return Response(
                {'detail': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=['post'])
    def quick_debug(self, request, *args, **kwargs):
        """Quick debug an interface without saving it first."""
        project_pk = self.kwargs.get('project_pk')
        interface_type = request.data.get('type', 'http')

        if interface_type == 'http':
            method = request.data.get('method')
            url = request.data.get('url')
            if not method:
                return Response(
                    {'detail': 'The "method" field is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not url:
                return Response(
                    {'detail': 'The "url" field is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif interface_type == 'sql':
            method = request.data.get('method', request.data.get('sql_method'))
            sql = request.data.get('sql')
            if not method:
                return Response(
                    {'detail': 'The "method" (sql_method) field is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not sql:
                return Response(
                    {'detail': 'The "sql" field is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        environment_id = request.data.get('environment_id')
        environment = None
        env_config = {}

        if environment_id:
            try:
                from api_environments.models import ApiEnvironment as Environment
                environment = get_object_or_404(Environment, id=environment_id, project_id=project_pk)

                if hasattr(environment, 'get_all_variables') and callable(
                    environment.get_all_variables
                ):
                    env_config['variables'] = environment.get_all_variables()

                if interface_type == 'sql' and hasattr(environment, 'get_database_config'):
                    db_config = environment.get_database_config()
                    if db_config:
                        env_config['db_config'] = {
                            'user': db_config.username,
                            'password': db_config.password,
                            'ip': db_config.host,
                            'port': db_config.port,
                            'database': db_config.database,
                        }
            except Exception as e:
                logger.error(f"Failed to load environment {environment_id}: {str(e)}")
                return Response(
                    {'detail': f'Failed to load environment: {str(e)}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Build interface data from request
        interface_data = {
            'name': request.data.get('name', 'Quick Debug'),
            'type': interface_type,
            'project_id': int(project_pk),
            'setup_hooks': request.data.get('setup_hooks', []),
            'teardown_hooks': request.data.get('teardown_hooks', []),
            'variables': request.data.get('variables', {}),
            'validators': request.data.get('validators', []),
            'extract': request.data.get('extract', {}),
        }

        if interface_type == 'http':
            interface_data.update({
                'method': request.data.get('method', 'GET'),
                'url': request.data.get('url', ''),
                'headers': request.data.get('headers', {}),
                'params': request.data.get('params', {}),
                'body': request.data.get('body', {}),
            })
        elif interface_type == 'sql':
            interface_data.update({
                'method': request.data.get('method', request.data.get('sql_method', 'fetchone')),
                'sql': request.data.get('sql', ''),
                'params': request.data.get('sql_params', {}),
                'size': request.data.get('sql_size', 10),
            })

        if environment:
            interface_data['base_url'] = getattr(environment, 'base_url', '') or ''
            interface_data['verify'] = getattr(environment, 'verify_ssl', None)

        try:
            runner = InterfaceRunner(interface_data)

            if env_config.get('variables'):
                runner.variables = runner.variables or {}
                runner.variables.update(env_config['variables'])

            if env_config.get('db_config') and interface_type == 'sql':
                runner.interface_data['db_config'] = env_config['db_config']

            runner.run_interface()
            response_data = runner.get_response()

            return Response(response_data)

        except Exception as e:
            logger.error(f"Quick debug failed: {str(e)}")
            return Response(
                {'detail': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ApiInterfaceResultViewSet(BaseModelViewSet):
    serializer_class = ApiInterfaceResultSerializer
    http_method_names = ['get', 'head', 'options']

    def get_permissions(self):
        return [IsAuthenticated(), HasModelPermission(), IsProjectMemberForResource()]

    def get_queryset(self):
        project_pk = self.kwargs.get('project_pk')
        queryset = ApiInterfaceResult.objects.filter(
            interface__project_id=project_pk
        )
        interface_id = self.request.query_params.get('interface_id')
        if interface_id:
            queryset = queryset.filter(interface_id=interface_id)
        return queryset
