import importlib.util
import pathlib
import unittest

path = pathlib.Path(__file__).parent / 'deploy_env/check_actuators.py'
spec = importlib.util.spec_from_file_location('check_actuators', path)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class ActuatorResponseTests(unittest.TestCase):
    def test_rendered_view_and_unified_response(self):
        items = [{'id': 'a', 'is_open': True}]
        view = {'status': 'success', 'data': {'items': items, 'count': 1}}
        rendered = {'status': 'success', 'code': 200, 'data': view}
        self.assertEqual(check.parse_items(rendered), items)
        self.assertEqual(check.parse_items(view), items)

    def test_reject_error_and_invalid_schema(self):
        for payload in [
            {'status': 'error', 'data': {'items': []}},
            {'status': 'success', 'code': 503, 'data': {'items': []}},
            {'data': {'items': {}}}, {'data': {'items': [{'id': 'a'}]}},
            {'data': {'items': [{'id': 'a', 'is_open': 'false'}]}},
            {'data': {'count': 3}},
        ]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                check.parse_items(payload)

    def test_wait_for_all_ids_online(self):
        samples = iter([
            {'data': {'items': [{'id': 'a', 'is_open': False}]}},
            {'data': {'items': [{'id': i, 'is_open': True} for i in ['a', 'b', 'c']]}},
        ])
        check.verify(['a', 'b', 'c'], lambda: next(samples), attempts=2, pause=lambda _: None)

    def test_offline_missing_or_malformed_must_fail(self):
        for payload in [{'data': {'items': []}},
                        {'data': {'items': [{'id': 'a', 'is_open': False}]}},
                        {'data': {'count': 3}}]:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                check.verify(['a'], lambda: payload, attempts=1)


if __name__ == '__main__':
    unittest.main()
