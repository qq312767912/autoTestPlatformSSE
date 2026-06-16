from typing import Dict, List, Optional, Tuple
import logging
from django.utils import timezone
from django.db import transaction
from .models import ApiTestCase, ApiTestCaseStep, ApiTestReport, ApiTestReportDetail
from .runner import TestCaseRunner

logger = logging.getLogger('testrunner')


class TestCaseService:
    """Test case service class."""

    @staticmethod
    def create_testcase(data: Dict, user) -> ApiTestCase:
        from api_interfaces.models import ApiInterface

        steps_data = data.pop('steps_info', [])

        with transaction.atomic():
            testcase = ApiTestCase.objects.create(
                created_by=user,
                **data
            )

            for index, step_data in enumerate(steps_data, 1):
                interface_id = step_data.pop('interface_id')
                interface = ApiInterface.objects.get(id=interface_id, project_id=data.get('project_id') or testcase.project_id)

                interface_data = {
                    'method': interface.method,
                    'url': interface.url,
                    'headers': interface.headers,
                    'params': interface.params,
                    'body': interface.body,
                    'validators': interface.validators,
                    'extract': interface.extract,
                    'setup_hooks': interface.setup_hooks,
                    'teardown_hooks': interface.teardown_hooks,
                    'variables': interface.variables
                }

                ApiTestCaseStep.objects.create(
                    testcase=testcase,
                    order=index,
                    interface_data=interface_data,
                    origin_interface=interface,
                    **step_data
                )

        return testcase

    @staticmethod
    def validate_testcase_data(data: Dict) -> Tuple[bool, Optional[str]]:
        required_fields = ['name', 'project']
        for field in required_fields:
            if field not in data:
                return False, f'Missing required field: {field}'

        steps = data.get('steps_info', [])
        if not steps:
            return False, 'At least one test step is required'

        for index, step in enumerate(steps):
            if 'name' not in step:
                return False, f'Step {index + 1} is missing a name'
            if 'interface_id' not in step:
                return False, f'Step {index + 1} is missing an interface_id'

        return True, None


class TestExecutionService:
    """Test execution service class."""

    @staticmethod
    def _prepare_config(config: Dict, environment: Optional[Dict] = None) -> Dict:
        if not isinstance(config, dict):
            config = {}
        if not isinstance(environment, dict):
            environment = {}

        env_variables = environment.get('variables', {})
        if not isinstance(env_variables, dict):
            if isinstance(env_variables, str) and env_variables.strip():
                try:
                    import json
                    env_variables = json.loads(env_variables)
                    if not isinstance(env_variables, dict):
                        env_variables = {}
                except (json.JSONDecodeError, Exception):
                    env_variables = {}
            else:
                env_variables = {}

        case_variables = config.get('variables', {})
        if not isinstance(case_variables, dict):
            if isinstance(case_variables, str) and case_variables.strip():
                try:
                    import json
                    case_variables = json.loads(case_variables)
                    if not isinstance(case_variables, dict):
                        case_variables = {}
                except (json.JSONDecodeError, Exception):
                    case_variables = {}
            else:
                case_variables = {}

        case_parameters = config.get('parameters', {})
        if not isinstance(case_parameters, dict):
            if isinstance(case_parameters, str) and case_parameters.strip():
                try:
                    import json
                    case_parameters = json.loads(case_parameters)
                    if not isinstance(case_parameters, dict):
                        case_parameters = {}
                except (json.JSONDecodeError, Exception):
                    case_parameters = {}
            else:
                case_parameters = {}

        return {
            "base_url": config.get('base_url') or environment.get('base_url', ''),
            "verify": config.get('verify', environment.get('verify_ssl', True)),
            "variables": {**env_variables, **case_variables},
            "export": config.get('export', []),
            "parameters": case_parameters
        }

    @staticmethod
    def run_testcase(
        testcase: ApiTestCase,
        environment: Optional[Dict] = None,
        user=None
    ) -> ApiTestReport:
        config = TestExecutionService._prepare_config(testcase.config, environment)
        testcase.config = config

        runner = TestCaseRunner(testcase)
        runner.run_testcase(environment)

        summary = runner.get_summary()
        step_results = runner.get_step_results()

        with transaction.atomic():
            report = ApiTestReport.objects.create(
                name=f"{testcase.name}-{timezone.now().strftime('%Y%m%d%H%M%S')}",
                status='success' if summary['success'] else 'failure',
                success_count=len([r for r in step_results if r['success']]),
                fail_count=len([r for r in step_results if not r['success']]),
                error_count=0,
                duration=summary['time']['duration'],
                summary=summary,
                testcase=testcase,
                executed_by=user,
                environment_id=environment.get('id') if environment else None
            )

            steps_by_order = {step.order: step for step in testcase.steps.all()}

            for i, step_result in enumerate(step_results):
                try:
                    step = steps_by_order.get(i + 1)
                    if step is None:
                        ordered_steps = list(testcase.steps.all().order_by('order'))
                        if i < len(ordered_steps):
                            step = ordered_steps[i]
                        else:
                            continue

                    step_success = step_result['success']

                    ApiTestReportDetail.objects.create(
                        report=report,
                        step=step,
                        success=step_success,
                        elapsed=step_result['elapsed'],
                        request=step_result['data']['request'],
                        response=step_result['data']['response'],
                        validators=step_result['data']['validators'],
                        extracted_variables=step_result['data']['extracted_variables'],
                        attachment=step_result['attachment']
                    )
                except Exception as e:
                    logger.error(f"Failed to create test report detail: {str(e)}")
                    continue

        return report

    @staticmethod
    def run_batch(
        testcases: List[ApiTestCase],
        environment: Optional[Dict] = None,
        user=None
    ) -> List[ApiTestReport]:
        reports = []
        for testcase in testcases:
            report = TestExecutionService.run_testcase(testcase, environment, user)
            reports.append(report)
        return reports

    @staticmethod
    def get_statistics(reports: List[ApiTestReport]) -> Dict:
        total = len(reports)
        if not total:
            return {
                'total': 0,
                'success': 0,
                'failure': 0,
                'error': 0,
                'success_rate': '0%'
            }

        success = len([r for r in reports if r.status == 'success'])
        failure = len([r for r in reports if r.status == 'failure'])
        error = len([r for r in reports if r.status == 'error'])

        return {
            'total': total,
            'success': success,
            'failure': failure,
            'error': error,
            'success_rate': f"{(success / total * 100):.2f}%"
        }
