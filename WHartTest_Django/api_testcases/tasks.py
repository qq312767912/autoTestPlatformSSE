import logging
from celery import shared_task
from .services import TestExecutionService

logger = logging.getLogger('testrunner')


@shared_task
def run_api_testcase(testcase_id, environment_config=None, user_id=None):
    """Async task to run a single test case."""
    from .models import ApiTestCase
    from django.contrib.auth.models import User

    try:
        testcase = ApiTestCase.objects.get(id=testcase_id)
        user = User.objects.get(id=user_id) if user_id else None
        report = TestExecutionService.run_testcase(
            testcase=testcase,
            environment=environment_config,
            user=user
        )
        logger.info(f"Async test case [{testcase.name}] completed, report ID: {report.id}")
        return {'report_id': report.id, 'status': report.status}
    except Exception as e:
        logger.error(f"Async test case execution failed: {str(e)}")
        return {'error': str(e)}


@shared_task
def run_api_testcase_batch(testcase_ids, environment_config=None, user_id=None):
    """Async task to run a batch of test cases."""
    from .models import ApiTestCase
    from django.contrib.auth.models import User

    try:
        testcases = ApiTestCase.objects.filter(id__in=testcase_ids)
        user = User.objects.get(id=user_id) if user_id else None
        reports = TestExecutionService.run_batch(
            testcases=testcases,
            environment=environment_config,
            user=user
        )
        statistics = TestExecutionService.get_statistics(reports)
        logger.info(f"Async batch execution completed: {statistics}")
        return {
            'report_ids': [r.id for r in reports],
            'statistics': statistics
        }
    except Exception as e:
        logger.error(f"Async batch execution failed: {str(e)}")
        return {'error': str(e)}
