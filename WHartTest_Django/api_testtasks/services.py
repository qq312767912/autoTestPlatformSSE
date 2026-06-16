import logging

from django.db import transaction, models
from django.utils import timezone

from .models import (
    ApiTestTaskSuite,
    ApiTestTaskCase,
    ApiTestTaskExecution,
    ApiTestTaskCaseResult,
)

logger = logging.getLogger(__name__)


class ApiTestTaskService:
    """Service class for test task suite operations."""

    @staticmethod
    def add_testcases(task_suite, testcase_ids, project_pk=None):
        from api_testcases.models import ApiTestCase

        existing_ids = set(
            task_suite.api_task_cases.values_list('testcase_id', flat=True)
        )
        new_ids = [tid for tid in testcase_ids if tid not in existing_ids]
        max_order = (
            task_suite.api_task_cases.aggregate(
                max_order=models.Max('order')
            )['max_order'] or 0
        )

        task_cases = []
        with transaction.atomic():
            for i, testcase_id in enumerate(new_ids, 1):
                try:
                    lookup = {'id': testcase_id}
                    if project_pk:
                        lookup['project_id'] = project_pk
                    testcase = ApiTestCase.objects.get(**lookup)
                    task_case = ApiTestTaskCase.objects.create(
                        task_suite=task_suite,
                        testcase=testcase,
                        order=max_order + i,
                    )
                    task_cases.append(task_case)
                except ApiTestCase.DoesNotExist:
                    logger.warning(f"Test case [ID={testcase_id}] does not exist, skipping")
                    continue
        return task_cases

    @staticmethod
    def remove_testcase(task_suite, testcase_id):
        try:
            task_case = ApiTestTaskCase.objects.get(
                task_suite=task_suite, testcase_id=testcase_id
            )
            task_case.delete()
            for i, tc in enumerate(
                task_suite.api_task_cases.all().order_by('order'), 1
            ):
                tc.order = i
                tc.save()
            return True
        except ApiTestTaskCase.DoesNotExist:
            return False


class ApiTestTaskExecutionService:
    """Service class for test task execution operations."""

    @staticmethod
    def create_execution(task_suite, environment_id=None, user=None):
        with transaction.atomic():
            execution = ApiTestTaskExecution.objects.create(
                task_suite=task_suite,
                environment_id=environment_id,
                executed_by=user,
                total_count=task_suite.api_task_cases.count(),
            )
            for task_case in task_suite.api_task_cases.all().order_by('order'):
                ApiTestTaskCaseResult.objects.create(
                    execution=execution,
                    testcase=task_case.testcase,
                )
        return execution

    @staticmethod
    def execute_task(execution):
        """Execute a test task synchronously."""
        from api_testcases.services import TestExecutionService

        execution.start()

        # Get environment configuration
        environment = None
        if execution.environment:
            try:
                env = execution.environment
                env_variables = env.get_all_variables()
                if isinstance(env_variables, str):
                    try:
                        import json
                        env_variables = json.loads(env_variables)
                    except (json.JSONDecodeError, ValueError):
                        env_variables = {}
                elif not isinstance(env_variables, dict):
                    env_variables = {}

                environment = {
                    'id': env.id,
                    'name': env.name,
                    'base_url': env.base_url,
                    'variables': env_variables,
                    'verify_ssl': env.verify_ssl
                }
            except Exception as e:
                logger.error(f"Error getting environment info: {str(e)}")
                environment = None

        task_suite = execution.task_suite
        case_results = execution.api_case_results.all().order_by('id')

        success_count = 0
        fail_count = 0
        error_count = 0

        for case_result in case_results:
            case_result.status = 'running'
            case_result.start_time = timezone.now()
            case_result.save()

            try:
                testcase = case_result.testcase
                report = TestExecutionService.run_testcase(
                    testcase, environment, execution.executed_by
                )

                case_result.report = report
                case_result.end_time = timezone.now()
                case_result.duration = (
                    case_result.end_time - case_result.start_time
                ).total_seconds()

                if report.status == 'success':
                    case_result.status = 'success'
                    success_count += 1
                else:
                    case_result.status = 'failure'
                    fail_count += 1

                case_result.save()

                if task_suite.fail_fast and report.status != 'success':
                    logger.info(
                        f"Task suite [{task_suite.name}] fail_fast enabled, "
                        f"case [{testcase.name}] failed, stopping"
                    )
                    for remaining in case_results.filter(status='pending'):
                        remaining.status = 'skipped'
                        remaining.save()
                    break

            except Exception as e:
                logger.error(
                    f"Error executing case [{case_result.testcase.name}]: {str(e)}"
                )
                case_result.status = 'error'
                case_result.end_time = timezone.now()
                case_result.duration = (
                    case_result.end_time - case_result.start_time
                ).total_seconds()
                case_result.error_message = str(e)
                case_result.save()
                error_count += 1

                if task_suite.fail_fast:
                    for remaining in case_results.filter(status='pending'):
                        remaining.status = 'skipped'
                        remaining.save()
                    break

        execution.complete(success_count, fail_count, error_count)

    @staticmethod
    def execute_task_async(execution_id):
        try:
            execution = ApiTestTaskExecution.objects.get(id=execution_id)
            ApiTestTaskExecutionService.execute_task(execution)
        except ApiTestTaskExecution.DoesNotExist:
            logger.error(f"Execution [ID={execution_id}] does not exist")
        except Exception as e:
            logger.error(f"Async task execution error: {str(e)}")
            try:
                execution = ApiTestTaskExecution.objects.get(id=execution_id)
                execution.fail()
            except Exception:
                pass
