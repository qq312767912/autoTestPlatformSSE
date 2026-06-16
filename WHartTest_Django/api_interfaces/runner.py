from typing import Dict, Optional
import logging
import json
import types

from httprunner import HttpRunner, Config, Step, RunRequest, RunSqlRequest
from httprunner.models import TestCaseSummary
from httprunner.parser import Parser

from api_functions.models import ApiCustomFunction
from .payloads import (
    flatten_key_value_pairs,
    normalize_request_body,
    prepare_request_body_for_runner,
)

logger = logging.getLogger(__name__)


def load_custom_functions(project_id):
    """Load custom functions for a project."""
    functions = {}
    loaded_count = 0
    error_count = 0

    try:
        custom_functions = ApiCustomFunction.objects.filter(
            project_id=project_id,
            is_active=True,
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
                    logger.warning(f"Function [{func.name}] did not define any callable functions")
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

        logger.info(
            f"Project [{project_name}] functions loaded: "
            f"{loaded_count} succeeded, {error_count} failed"
        )

    except Exception as e:
        logger.error(f"Error loading custom functions for project [{project_id}]: {str(e)}")

    return functions


class InterfaceRunner(HttpRunner):
    """Interface runner based on httprunner."""

    def __init__(self, interface_data: Dict):
        super().__init__()
        self.interface_data = interface_data
        self.db_engine = None
        self.functions = {}

        try:
            self.config = Config(self.interface_data.get('name', 'Interface Request'))
            self.base_url = self.interface_data.get('base_url', '')
            self.verify = self.interface_data.get('verify', None)
            self.variables = self.interface_data.get('variables', {})

            if self.base_url:
                self.config.base_url(self.base_url)
            if self.verify is not None:
                self.config.verify(self.verify)
            if self.variables:
                self.config.variables(**self.variables)

            project_id = self.interface_data.get('project_id')
            if project_id:
                try:
                    custom_functions = load_custom_functions(project_id)
                    if custom_functions:
                        self.functions.update(custom_functions)
                        self.parser = Parser(functions_mapping=custom_functions)
                        logger.info(
                            f"Interface [{self.interface_data.get('name')}] "
                            f"loaded custom functions: {list(custom_functions.keys())}"
                        )
                except Exception as e:
                    logger.error(f"Failed to load custom functions: {str(e)}")

            logger.info(
                f"Interface config: base_url={self.base_url}, "
                f"verify={self.verify}, variables={self.variables}"
            )
        except Exception as e:
            logger.error(f"Interface config initialization failed: {str(e)}")
            raise

        interface_type = self.interface_data.get('type', 'http')
        if interface_type == 'sql':
            self._init_sql_step()
        else:
            self._init_http_step()

    def _add_hooks_to_step(self, step_obj, hooks, hook_type='setup'):
        """Add setup or teardown hooks to a step object."""
        method_name = 'setup_hook' if hook_type == 'setup' else 'teardown_hook'
        for hook in hooks:
            if isinstance(hook, dict):
                hook_json = json.dumps(hook, ensure_ascii=False)
                step_obj = getattr(step_obj, method_name)(hook_json)
            else:
                step_obj = getattr(step_obj, method_name)(hook)
        return step_obj

    def _init_http_step(self):
        """Initialize HTTP step."""
        step_obj = RunRequest(self.interface_data.get('name', 'Interface Request'))

        method = self.interface_data.get('method', 'GET').lower()
        url = self.interface_data.get('url', '')

        if not url.startswith(('http://', 'https://')):
            url = f"{self.base_url.rstrip('/')}/{url.lstrip('/')}"

        # Setup hooks
        if self.interface_data.get('setup_hooks'):
            step_obj = self._add_hooks_to_step(
                step_obj, self.interface_data['setup_hooks'], 'setup'
            )

        # HTTP method
        step_obj = getattr(step_obj, method)(url)

        # Teardown hooks
        if self.interface_data.get('teardown_hooks'):
            step_obj = self._add_hooks_to_step(
                step_obj, self.interface_data['teardown_hooks'], 'teardown'
            )

        # Query params
        if self.interface_data.get('params'):
            params = {}
            for k, v in flatten_key_value_pairs(self.interface_data['params']).items():
                if isinstance(v, str) and v.startswith('$'):
                    var_name = v[1:]
                    params[k] = self.variables.get(var_name, v)
                else:
                    params[k] = v
            step_obj = step_obj.with_params(**params)

        # Headers (global + interface-specific)
        headers_dict = {}
        global_headers = self._get_global_headers()
        if global_headers:
            headers_dict.update(global_headers)

        if self.interface_data.get('headers'):
            headers_dict.update(flatten_key_value_pairs(self.interface_data['headers']))

        if headers_dict:
            step_obj = step_obj.with_headers(**headers_dict)

        # Request body
        if self.interface_data.get('body'):
            normalized_body = normalize_request_body(self.interface_data['body'])
            body = prepare_request_body_for_runner(normalized_body)
            if isinstance(body, str) and body.startswith('$'):
                var_name = body[1:]
                if var_name in self.variables:
                    body = self.variables[var_name]
            if body is not None:
                if normalized_body['type'] in {'form-data', 'x-www-form-urlencoded', 'binary'}:
                    step_obj = step_obj.with_data(body)
                else:
                    step_obj = step_obj.with_json(body)

        # Extract
        if self.interface_data.get('extract'):
            extract_obj = step_obj.extract()
            for var_name, expr in self.interface_data['extract'].items():
                extract_obj = extract_obj.with_jmespath(expr, var_name)
            step_obj = extract_obj

        # Validators
        if self.interface_data.get('validators'):
            step_obj = self._add_validators(step_obj, self.interface_data['validators'])

        step = Step(step_obj)
        self.teststeps.append(step)

    def _init_sql_step(self):
        """Initialize SQL step."""
        step_obj = RunSqlRequest(self.interface_data.get('name', 'SQL Request'))

        sql_method = self.interface_data.get('method', 'fetchone').lower()
        sql = self.interface_data.get('sql', '')

        # Setup hooks
        if self.interface_data.get('setup_hooks'):
            step_obj = self._add_hooks_to_step(
                step_obj, self.interface_data['setup_hooks'], 'setup'
            )

        # SQL method dispatch
        if sql_method == 'fetchone':
            step_obj = step_obj.fetchone(sql)
        elif sql_method == 'fetchmany':
            size = self.interface_data.get('size', 10)
            step_obj = step_obj.fetchmany(sql, size)
        elif sql_method == 'fetchall':
            step_obj = step_obj.fetchall(sql)
        elif sql_method == 'insert':
            step_obj = step_obj.insert(sql)
        elif sql_method == 'update':
            step_obj = step_obj.update(sql)
        elif sql_method == 'delete':
            step_obj = step_obj.delete(sql)
        else:
            logger.warning(f"Unsupported SQL method: {sql_method}, falling back to fetchone")
            step_obj = step_obj.fetchone(sql)

        # Teardown hooks
        if self.interface_data.get('teardown_hooks'):
            step_obj = self._add_hooks_to_step(
                step_obj, self.interface_data['teardown_hooks'], 'teardown'
            )

        # Extract
        if self.interface_data.get('extract'):
            for var_name, expr in self.interface_data['extract'].items():
                step_obj = step_obj.with_jmespath(expr, var_name)

        # Validators
        if self.interface_data.get('validators'):
            step_obj = self._add_validators_sql(step_obj, self.interface_data['validators'])

        step = Step(step_obj)
        self.teststeps.append(step)

    def _add_validators(self, step_obj, validators):
        """Add validators to an HTTP step."""
        validate_obj = step_obj.validate()
        for validator in validators:
            if not isinstance(validator, dict):
                logger.warning(f"Unsupported validator format: {validator}")
                continue

            if "check" in validator and "expect" in validator:
                validate_obj = validate_obj.assert_equal(
                    validator["check"], validator["expect"]
                )
            elif len(validator) == 1:
                comparator = list(validator.keys())[0]
                check_item, expected_value = validator[comparator]
                validate_obj = self._apply_comparator(
                    validate_obj, comparator, check_item, expected_value
                )
            elif "eq" in validator:
                check_value = validator["eq"][0]
                expect_value = validator["eq"][1]
                validate_obj = validate_obj.assert_equal(check_value, expect_value)

        return validate_obj

    def _add_validators_sql(self, step_obj, validators):
        """Add validators to a SQL step."""
        validate_obj = step_obj.validate()
        for validator in validators:
            if not isinstance(validator, dict):
                logger.warning(f"Unsupported validator format: {validator}")
                continue
            for comparator, (check_item, expected_value) in validator.items():
                try:
                    validate_obj = self._apply_comparator(
                        validate_obj, comparator, check_item, expected_value
                    )
                except AttributeError as e:
                    logger.warning(f"SQL validator method not found: {str(e)}")
                except Exception as e:
                    logger.warning(f"Failed to add validator: {str(e)}")
        return validate_obj

    def _apply_comparator(self, validate_obj, comparator, check_item, expected_value):
        """Apply a single comparator to a validation object."""
        comparator_map = {
            'eq': 'assert_equal',
            'ne': 'assert_not_equal',
            'lt': 'assert_less_than',
            'le': 'assert_less_or_equals',
            'lte': 'assert_less_or_equals',
            'gt': 'assert_greater_than',
            'ge': 'assert_greater_or_equals',
            'gte': 'assert_greater_or_equals',
            'str_eq': 'assert_string_equals',
            'contains': 'assert_contains',
            'contained_by': 'assert_contained_by',
            'type_match': 'assert_type_match',
            'regex_match': 'assert_regex_match',
            'startswith': 'assert_startswith',
            'endswith': 'assert_endswith',
            'length_equal': 'assert_length_equal',
            'length_greater_than': 'assert_length_greater_than',
            'length_less_than': 'assert_length_less_than',
            'length_greater_or_equals': 'assert_length_greater_or_equals',
            'length_less_or_equals': 'assert_length_less_or_equals',
        }
        method_name = comparator_map.get(comparator)
        if method_name and hasattr(validate_obj, method_name):
            return getattr(validate_obj, method_name)(check_item, expected_value)
        else:
            logger.warning(f"Unsupported comparator: {comparator}")
            return validate_obj

    def run_interface(self, environment: Optional[Dict] = None) -> "InterfaceRunner":
        """Run the interface request."""
        logger.info(f"Executing interface [{self.interface_data.get('name', 'Interface Request')}]")

        interface_type = self.interface_data.get('type', 'http')

        # Process environment variables
        try:
            if environment and isinstance(environment, dict) and environment.get('variables'):
                self.variables.update(environment['variables'])
                self.config.variables = self.variables
        except Exception as e:
            logger.error(f"Failed to update environment variables: {str(e)}")

        # Process DB config for SQL interfaces
        try:
            if (
                interface_type == 'sql'
                and environment
                and isinstance(environment, dict)
                and environment.get('db_config')
            ):
                db_config = environment['db_config']
                if 'db_config' not in self.interface_data:
                    self.interface_data['db_config'] = {}
                for key in ['user', 'password', 'ip', 'port', 'database']:
                    if key in db_config and db_config[key]:
                        self.interface_data['db_config'][key] = db_config[key]
        except Exception as e:
            logger.error(f"Failed to update DB config: {str(e)}")

        # Execute
        try:
            self.test_start()
        except Exception as e:
            logger.error(f"Interface execution failed: {str(e)}")
            raise

        return self

    def get_summary(self) -> TestCaseSummary:
        """Get execution summary."""
        return super().get_summary()

    def get_response(self) -> Dict:
        """Get interface response result."""
        summary = self.get_summary()
        if not summary.step_results:
            return {"success": False, "error": "No execution results"}

        step_result = summary.step_results[0]
        data = step_result.data  # type: ignore

        req_resp = None
        stat = None
        validators = {}

        if hasattr(data, 'req_resp'):
            req_resp = data.req_resp  # type: ignore
        elif hasattr(data, 'req_resps') and isinstance(data.req_resps, list):  # type: ignore
            req_resps = data.req_resps  # type: ignore
            if req_resps:
                req_resp = req_resps[-1]

        if hasattr(data, 'stat'):
            stat = data.stat  # type: ignore

        if hasattr(data, 'validators'):
            validators = data.validators  # type: ignore

        # 提取 validate_extractor 列表（原项目 views.py 的做法）
        validation_results = []
        if isinstance(validators, dict):
            validation_results = validators.get("validate_extractor", [])
        elif isinstance(validators, list):
            validation_results = validators

        result = {
            "success": step_result.success,
            "name": step_result.name,
            "validation_results": validation_results,
            "extracted_variables": step_result.export_vars,  # type: ignore
        }

        if req_resp:
            status_code = (
                req_resp.response.status_code
                if hasattr(req_resp, 'response') else None
            )  # type: ignore
            elapsed = stat.response_time_ms if stat else 0  # type: ignore
            result.update({
                "status_code": status_code,
                "elapsed": elapsed,
                "request": {
                    "method": (
                        req_resp.request.method
                        if hasattr(req_resp, 'request') else None
                    ),  # type: ignore
                    "url": (
                        req_resp.request.url
                        if hasattr(req_resp, 'request') else None
                    ),  # type: ignore
                    "headers": (
                        req_resp.request.headers
                        if hasattr(req_resp, 'request') else {}
                    ),  # type: ignore
                    "body": (
                        req_resp.request.body
                        if hasattr(req_resp, 'request') else None
                    ),  # type: ignore
                },
                "response": {
                    "status_code": status_code,
                    "headers": (
                        req_resp.response.headers
                        if hasattr(req_resp, 'response') else {}
                    ),  # type: ignore
                    "content": (
                        req_resp.response.body
                        if hasattr(req_resp, 'response') else None
                    ),  # type: ignore
                    "content_size": stat.content_size if stat else 0,  # type: ignore
                },
            })
        else:
            result.update({
                "status_code": None,
                "elapsed": 0,
                "request": {"method": None, "url": None, "headers": {}, "body": None},
                "response": {"status_code": None, "headers": {}, "content": None, "content_size": 0},
            })

        return result

    def _get_global_headers(self):
        """Get global request headers for the project."""
        global_headers = {}
        try:
            project_id = self.interface_data.get('project_id')
            if project_id:
                from api_environments.models import ApiGlobalRequestHeader as GlobalRequestHeader

                headers = GlobalRequestHeader.objects.filter(
                    project_id=project_id,
                    is_enabled=True,
                )
                for header in headers:
                    header_value = header.value
                    if isinstance(header_value, str):
                        if header_value.startswith('${') and header_value.endswith('}'):
                            var_name = header_value[2:-1]
                            if hasattr(self, 'variables') and var_name in self.variables:
                                header_value = self.variables[var_name]
                        elif header_value.startswith('$') and len(header_value) > 1:
                            var_name = header_value[1:]
                            if hasattr(self, 'variables') and var_name in self.variables:
                                header_value = self.variables[var_name]
                    global_headers[header.name] = header_value

            return global_headers
        except Exception as e:
            logger.error(f"Failed to get global headers: {str(e)}")
            return {}
