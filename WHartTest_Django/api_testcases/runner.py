from typing import Dict, List, Optional
import json
import logging
import types
from httprunner import HttpRunner, Config, Step, RunRequest, RunSqlRequest
from api_interfaces.payloads import (
    flatten_key_value_pairs,
    normalize_request_body,
    prepare_request_body_for_runner,
)
from .models import ApiTestCase, ApiTestCaseStep

logger = logging.getLogger('testrunner')


def load_custom_functions(project_id):
    """Load custom functions for a project."""
    functions = {}
    loaded_count = 0
    error_count = 0

    try:
        from api_functions.models import ApiCustomFunction
        custom_functions = ApiCustomFunction.objects.filter(
            project_id=project_id,
            is_active=True
        ).select_related('project')

        project_name = custom_functions[0].project.name if custom_functions else "Unknown"
        logger.info(f"Loading custom functions for project [{project_name}]")

        for func in custom_functions:
            try:
                module = types.ModuleType(func.name)
                code = compile(func.code, func.name, 'exec')
                exec(code, module.__dict__)

                module_functions = {
                    name: obj for name, obj in module.__dict__.items()
                    if isinstance(obj, types.FunctionType)
                }

                if not module_functions:
                    logger.warning(f"Function [{func.name}] has no callable functions")
                    error_count += 1
                    continue

                for name in module_functions:
                    if name in functions:
                        logger.warning(f"Function name conflict: {name}, using latest definition")

                functions.update(module_functions)
                loaded_count += 1
                logger.debug(f"Loaded function: {func.name}, methods: {list(module_functions.keys())}")

            except SyntaxError as e:
                logger.error(f"Function [{func.name}] syntax error: {str(e)}")
                error_count += 1
            except Exception as e:
                logger.error(f"Failed to load function [{func.name}]: {str(e)}")
                error_count += 1

        logger.info(f"Project [{project_name}] function loading complete: {loaded_count} success, {error_count} failed")

    except Exception as e:
        logger.error(f"Error loading custom functions for project [{project_id}]: {str(e)}")

    return functions


class TestCaseRunner(HttpRunner):
    """Test case runner extending HttpRunner."""

    @staticmethod
    def _validators_indicate_failure(validators: Optional[Dict]) -> bool:
        """Return True when validators explicitly mark the step as failed."""
        if not isinstance(validators, dict) or not validators:
            return False

        if validators.get('success') is False:
            return True

        validate_extractor = validators.get('validate_extractor')
        if not isinstance(validate_extractor, list):
            return False

        return any(
            isinstance(validator, dict) and validator.get('check_result') == 'fail'
            for validator in validate_extractor
        )

    def _create_http_step(self, step_name: str, interface_data: Dict) -> RunRequest:
        """Create an HTTP request step."""
        step_obj = RunRequest(step_name)

        method = interface_data['method'].lower()
        url = interface_data['url']
        if not url.startswith(('http://', 'https://')):
            url = f"{self.base_url.rstrip('/')}/{url.lstrip('/')}"

        if interface_data.get('setup_hooks'):
            for hook in interface_data['setup_hooks']:
                step_obj = step_obj.setup_hook(hook)

        step_obj = getattr(step_obj, method)(url)

        if interface_data.get('teardown_hooks'):
            for hook in interface_data['teardown_hooks']:
                step_obj = step_obj.teardown_hook(hook)

        return step_obj

    def _create_sql_step(self, step_name: str, interface_data: Dict) -> RunSqlRequest:
        """Create a SQL request step."""
        step_obj = RunSqlRequest(step_name)

        if interface_data.get('db_config'):
            db_config = interface_data['db_config']
            step_obj = step_obj.with_db_config(
                user=db_config.get('user'),
                password=db_config.get('password'),
                ip=db_config.get('ip'),
                port=db_config.get('port'),
                database=db_config.get('database'),
                psm=db_config.get('psm')
            )

        if interface_data.get('setup_hooks'):
            for hook in interface_data['setup_hooks']:
                step_obj = step_obj.setup_hook(hook)

        sql_method = interface_data.get('sql_method', 'fetchone').upper()
        sql = interface_data.get('sql', '')

        if sql_method == 'FETCHONE':
            step_obj = step_obj.fetchone(sql)
        elif sql_method == 'FETCHMANY':
            size = interface_data.get('sql_size', 10)
            step_obj = step_obj.fetchmany(sql, size)
        elif sql_method == 'FETCHALL':
            step_obj = step_obj.fetchall(sql)
        elif sql_method == 'INSERT':
            step_obj = step_obj.insert(sql)
        elif sql_method == 'UPDATE':
            step_obj = step_obj.update(sql)
        elif sql_method == 'DELETE':
            step_obj = step_obj.delete(sql)
        else:
            logger.warning(f"Unsupported SQL method: {sql_method}, using fetchone")
            step_obj = step_obj.fetchone(sql)

        if interface_data.get('teardown_hooks'):
            for hook in interface_data['teardown_hooks']:
                step_obj = step_obj.teardown_hook(hook)

        return step_obj

    def __init__(self, testcase: ApiTestCase):
        super().__init__()
        self.testcase = testcase
        self.teststeps = []

        # Load and register custom functions
        try:
            custom_functions = load_custom_functions(testcase.project_id)
            if custom_functions:
                self.functions = custom_functions
                from httprunner.parser import Parser
                self.parser = Parser(functions_mapping=custom_functions)
                logger.info(f"Test case [{testcase.name}] loaded custom functions: {list(custom_functions.keys())}")
            else:
                logger.warning(f"Project [{testcase.project.name}] has no available custom functions")
        except Exception as e:
            logger.error(f"Failed to load custom functions for project [{testcase.project.name}]: {str(e)}")

        # Build config
        try:
            self.config = Config(self.testcase.name)

            if isinstance(testcase.config, str):
                try:
                    testcase.config = json.loads(testcase.config)
                except (json.JSONDecodeError, ValueError):
                    testcase.config = {}
            elif not isinstance(testcase.config, dict):
                testcase.config = {}

            self.base_url = self.testcase.config.get('base_url', '')
            self.verify = self.testcase.config.get('verify', None)
            variables = self.testcase.config.get('variables', {})
            if isinstance(variables, str):
                try:
                    variables = json.loads(variables)
                except (json.JSONDecodeError, ValueError):
                    variables = {}
            elif not isinstance(variables, dict):
                variables = {}
            self.variables = variables

            if self.base_url:
                self.config.base_url(self.base_url)
            if self.verify is not None:
                self.config.verify(self.verify)
            if self.variables and isinstance(self.variables, dict):
                self.config.variables(**self.variables)

        except Exception as e:
            logger.error(f"Test case [{testcase.name}] config initialization failed: {str(e)}")
            raise

        # Build test steps
        steps = []
        for step in self.testcase.steps.all().order_by('order'):
            interface_data = step.interface_data
            step_type = interface_data.get('type', 'http').lower()

            if step_type == 'sql':
                step_obj = self._create_sql_step(step.name, interface_data)
            else:
                step_obj = self._create_http_step(step.name, interface_data)

            # Add params for HTTP requests
            if step_type != 'sql' and interface_data.get('params'):
                params = {}
                if not isinstance(self.variables, dict):
                    self.variables = {}
                for k, v in flatten_key_value_pairs(interface_data['params']).items():
                    if isinstance(v, str) and v.startswith('$'):
                        var_name = v[1:]
                        if var_name in self.variables:
                            params[k] = self.variables[var_name]
                        else:
                            params[k] = v
                    else:
                        params[k] = v
                step_obj = step_obj.with_params(**params)

            # Add headers for HTTP requests
            if step_type != 'sql':
                try:
                    headers = interface_data.get('headers')
                    if headers:
                        headers_dict = flatten_key_value_pairs(headers)

                        if headers_dict:
                            step_obj = step_obj.with_headers(**headers_dict)
                except Exception as e:
                    logger.error(f"Error processing headers for step [{step.name}]: {str(e)}")

            # Add request body for HTTP requests
            if step_type != 'sql' and interface_data.get('body'):
                normalized_body = normalize_request_body(interface_data['body'])
                body = prepare_request_body_for_runner(normalized_body)
                if isinstance(body, str) and body.startswith('$'):
                    var_name = body[1:]
                    if isinstance(self.variables, dict) and var_name in self.variables:
                        body = self.variables[var_name]

                if body is not None:
                    if normalized_body['type'] in {'form-data', 'x-www-form-urlencoded', 'binary'}:
                        step_obj = step_obj.with_data(body)
                    else:
                        step_obj = step_obj.with_json(body)

            # Set export variables
            if interface_data.get('export'):
                step_obj.struct().export = interface_data['export']

            # Add variable extractors
            if interface_data.get('extract'):
                step_obj = step_obj.extract()
                for var_name, expr in interface_data['extract'].items():
                    step_obj = step_obj.with_jmespath(expr, var_name)

            # Create Step object
            step = Step(step_obj)

            # Add validators
            if interface_data.get('validators'):
                step_obj = step_obj.validate()
                for validator in interface_data['validators']:
                    if isinstance(validator, dict):
                        if "check" in validator and "expect" in validator:
                            step_obj = step_obj.assert_equal(validator["check"], validator["expect"])
                        elif "eq" in validator:
                            check_value = validator["eq"][0]
                            expect_value = validator["eq"][1]
                            step_obj = step_obj.assert_equal(check_value, expect_value)
                        elif len(validator) == 1:
                            comparator = list(validator.keys())[0]
                            check_item, expected_value = validator[comparator]
                            comparator_map = {
                                "eq": "assert_equal",
                                "lt": "assert_less_than",
                                "le": "assert_less_or_equals",
                                "gt": "assert_greater_than",
                                "ge": "assert_greater_or_equals",
                                "ne": "assert_not_equal",
                                "str_eq": "assert_string_equals",
                                "contains": "assert_contains",
                                "contained_by": "assert_contained_by",
                                "type_match": "assert_type_match",
                                "regex_match": "assert_regex_match",
                            }
                            method_name = comparator_map.get(comparator)
                            if method_name:
                                step_obj = getattr(step_obj, method_name)(check_item, expected_value)
                            else:
                                logger.warning(f"Unsupported comparator: {comparator}")

            self.teststeps.append(step)

    def run_testcase(self, environment: Optional[Dict] = None) -> "TestCaseRunner":
        """Execute the test case."""
        logger.info(f"Starting test case execution: {self.testcase.name}")

        if environment:
            if environment.get('base_url'):
                self.config.base_url(environment['base_url'])
                logger.info(f"Using environment base_url: {environment['base_url']}")

            if environment.get('variables'):
                env_variables = environment.get('variables', {})
                if not isinstance(env_variables, dict):
                    env_variables = {}
                if not isinstance(self.variables, dict):
                    self.variables = {}
                case_variables = self.variables.copy()
                case_variables.update(env_variables)
                self.config.variables(**case_variables)

        # Apply global request headers
        try:
            from api_environments.models import ApiGlobalRequestHeader
            if self.testcase.project_id:
                global_headers = ApiGlobalRequestHeader.objects.filter(
                    project_id=self.testcase.project_id,
                    is_enabled=True
                )

                for header in global_headers:
                    header_name = header.name
                    header_value = header.value

                    try:
                        if header_value.startswith('${') and header_value.endswith('}'):
                            var_name = header_value[2:-1]
                            if var_name in case_variables:
                                header_value = case_variables[var_name]
                        elif header_value.startswith('$') and len(header_value) > 1:
                            var_name = header_value[1:]
                            if var_name in case_variables:
                                header_value = case_variables[var_name]
                    except Exception:
                        pass

                    for step in self.testcase.steps.all().order_by('order'):
                        interface_data = step.interface_data
                        if 'headers' not in interface_data:
                            interface_data['headers'] = {}
                        headers_dict = interface_data['headers']
                        if isinstance(headers_dict, dict) and header_name not in headers_dict:
                            headers_dict[header_name] = header_value
        except Exception as e:
            logger.error(f"Error applying global request headers: {str(e)}")

        try:
            self.test_start()
            logger.info(f"Test case execution complete: {self.testcase.name}")
        except Exception as e:
            logger.error(f"Test case execution error: {str(e)}")
            raise

        return self

    def get_step_results(self) -> List[Dict]:
        """Get step execution results."""
        summary = super().get_summary()
        results = []

        for step_result in summary.step_results:
            step_type = step_result.step_type
            validators = getattr(step_result.data, 'validators', {})
            success = step_result.success and not self._validators_indicate_failure(validators)

            result = {
                'name': step_result.name,
                'success': success,
                'elapsed': step_result.elapsed,
                'step_type': step_type,
                'data': {
                    'extracted_variables': step_result.export_vars,
                    'validators': validators
                },
                'attachment': step_result.attachment
            }

            if step_type == 'request':
                req_resp = step_result.data.req_resps[-1] if step_result.data.req_resps else None
                result['data'].update({
                    'request': {
                        'method': req_resp.request.method if req_resp else None,
                        'url': req_resp.request.url if req_resp else None,
                        'headers': req_resp.request.headers if req_resp else {},
                        'body': req_resp.request.body if req_resp else None
                    },
                    'response': {
                        'status_code': req_resp.response.status_code if req_resp else None,
                        'headers': req_resp.response.headers if req_resp else {},
                        'body': req_resp.response.body if req_resp else None,
                        'content_size': step_result.data.stat.content_size,
                        'response_time_ms': step_result.data.stat.response_time_ms,
                    }
                })
            elif step_type == 'sql':
                sql_result = None
                if hasattr(step_result.data, 'sql_response'):
                    sql_result = step_result.data.sql_response
                result['data'].update({
                    'sql_request': {
                        'sql': getattr(step_result.data, 'sql', None),
                        'method': getattr(step_result.data, 'method', None),
                        'db_config': getattr(step_result.data, 'db_config', {})
                    },
                    'sql_response': sql_result
                })

            results.append(result)

        return results

    def get_summary(self) -> Dict:
        """Get execution summary."""
        summary = super().get_summary()
        step_results = self.get_step_results()

        success = True
        export_vars = {}
        for step in step_results:
            if not step['success']:
                success = False
                break
            if step['data'].get('extracted_variables'):
                export_vars.update(step['data']['extracted_variables'])

        return {
            'success': success,
            'name': summary.name,
            'time': {
                'start_at': summary.time.start_at,
                'duration': summary.time.duration
            },
            'in_out': {
                'config_vars': summary.in_out.config_vars,
                'export_vars': export_vars
            },
            'log': summary.log,
            'step_results': step_results
        }


class BatchRunner:
    """Batch test case runner."""

    def __init__(self, testcases: List[ApiTestCase]):
        self.testcases = testcases
        self.results = []

    def run(self, environment: Optional[Dict] = None) -> List[Dict]:
        for testcase in self.testcases:
            runner = TestCaseRunner(testcase)
            runner.run_testcase(environment)
            self.results.append({
                'testcase_id': testcase.id,
                'testcase_name': testcase.name,
                'summary': runner.get_summary()
            })
        return self.results

    def get_statistics(self) -> Dict:
        if not self.results:
            return {}
        total = len(self.results)
        success = len([r for r in self.results if r['summary']['success']])
        return {
            'total': total,
            'success': success,
            'fail': total - success,
            'success_rate': f"{(success / total * 100):.2f}%"
        }
