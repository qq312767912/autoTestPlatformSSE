import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch


class ConstraintCliTests(unittest.TestCase):
    def test_comma_separated_ids_reach_query_without_integer_coercion(self):
        path = Path(__file__).with_name('api_automation_tools.py')
        spec = importlib.util.spec_from_file_location('api_constraints_cli', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        captured = []
        def get_action(args, endpoint):
            captured.append((args.project_id, args.interface_ids, endpoint))
            return {'status': 'success'}
        with patch.object(module, '_get_action', get_action), patch('sys.argv', ['tools.py', '--action', 'get_interface_constraints', '--project_id', '3', '--interface_ids', '10,11']), contextlib.redirect_stdout(io.StringIO()):
            module.main()
        self.assertEqual(captured, [(3, '10,11', 'api-interfaces/constraints/?interface_ids=10,11')])
