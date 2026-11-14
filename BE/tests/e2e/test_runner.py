"""Offline runner-contract tests: never starts servers, containers or browsers."""
import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
import runner
from errors import Blocked
from management import Management
from runtime_safety import safe_environment, pinned_api_arguments
from concurrency_evidence import replay_audit

class RunnerContract(unittest.TestCase):
    def setUp(self):
        self.cases = runner.load_scenarios()

    def validate(self, data):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'test.json').write_text(json.dumps(data))
            return runner.load_scenarios(directory)

    def test_registry_all_cases_resolve(self):
        self.assertGreaterEqual(len(self.cases), 17)
        self.assertTrue(all(c['handler'] in runner.HANDLERS for c in self.cases))

    def test_selection(self):
        self.assertEqual(len(runner.select(self.cases, 'AUTH-JWT')), 1)
        self.assertEqual({c['category'] for c in runner.select(self.cases, category='browser')}, {'browser'})
        for ids in ['DOES-NOT-EXIST', 'AUTH-JWT,AUTH-JWT']:
            with self.assertRaises(ValueError): runner.select(self.cases, ids)
        with self.assertRaises(ValueError): runner.select(self.cases, category='missing')

    def test_reject_duplicate_unknown_and_malformed(self):
        case = self.cases[0]
        with self.assertRaises(ValueError): self.validate([case, case])
        for field, bad in [('handler', 'shell'), ('steps', []), ('profiles', ['H2']), ('expected', {}), ('fixtures', 'alice')]:
            altered = copy.deepcopy(case); altered[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError): self.validate([altered])
        altered = copy.deepcopy(case); altered['shell'] = 'echo forbidden'
        with self.assertRaises(ValueError): self.validate([altered])

    def test_sanitization(self):
        data = 'eyJabc.abc.def password123 Authorization: Bearer sensitive\n-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----'
        safe = runner.sanitize(data, ['password123'])
        for value in ['eyJabc', 'password123', 'Bearer sensitive', 'BEGIN PRIVATE KEY']:
            self.assertNotIn(value, safe)

    def test_snapshot_requires_exact_rows(self):
        raw = '1\tV1__access_schema.sql\t123\t1\n' + ''.join(f'{name}\t{count}\n' for name, count in {
            'users': 4, 'roles': 3, 'policies': 4, 'resources': 4, 'user_roles': 3, 'role_policies': 6, 'policy_resources': 6}.items())
        self.assertEqual(runner.parse_snapshot(raw)['counts']['roles'], 3)
        with self.assertRaises(AssertionError): runner.parse_snapshot(raw.replace('\nroles\t3\n', '\n'))
        with self.assertRaises(AssertionError): runner.parse_snapshot(raw + 'roles\t3\n')
        with self.assertRaises(AssertionError): runner.parse_snapshot(raw.replace('123\t1', '123\t0'))

    def test_no_handoff_blocks_without_subprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = runner.Context(argparse.Namespace(handoff=None, profile='local'), Path(directory))
            with patch('runner.subprocess.run', side_effect=AssertionError('runtime touched')):
                for handler in runner.HANDLERS:
                    with self.subTest(handler=handler), self.assertRaises(runner.Blocked): ctx.execute(handler)
                ctx.close()

    def test_v2_refuses_developer_volume_before_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory, 'handoff.json')
            manifest.write_text(json.dumps({'status': 'TEST_READY', 'runtime_owner': 'TESTER', 'builds_frozen': True,
                'contract': 'v2', 'profile': 'local', 'artifacts': {}, 'compose_project': 'mcp-access-demo',
                'volume': 'mcp-access-demo_access-data', 'fixture_path': 'fixture-name', 'previous_processes': []}))
            ctx = runner.Context(argparse.Namespace(handoff=str(manifest), profile='local', contract='v2'), Path(directory))
            with patch('runner.subprocess.run', side_effect=AssertionError('runtime touched')):
                with self.assertRaisesRegex(runner.Blocked, 'isolated'): ctx.gate()
                ctx.close()

    def test_inherited_datasource_and_java_overrides_never_reach_child(self):
        settings = {'MYSQL_DATABASE': 'isolated_test', 'MYSQL_USER': 'test_app', 'MYSQL_PASSWORD': 'app-fixture',
                    'MYSQL_ROOT_PASSWORD': 'root-fixture', 'DB_PORT': '3308'}
        inherited = {'PATH': '/usr/bin', 'HOME': '/tmp', 'SPRING_DATASOURCE_URL': 'jdbc:mysql://developer:3307/prod',
                     'SPRING_FLYWAY_URL': 'jdbc:mysql://developer:3307/prod', 'SPRING_APPLICATION_JSON': '{"unsafe":true}',
                     'JAVA_TOOL_OPTIONS': '-Dspring.datasource.url=bad', '_JAVA_OPTIONS': '-Dconfig=bad',
                     'JDK_JAVA_OPTIONS': '-Dspring.config.location=/other/config'}
        safe = safe_environment(inherited, settings)
        for key in inherited.keys() - {'PATH', 'HOME'}: self.assertNotIn(key, safe)
        arguments = pinned_api_arguments(settings, 'test_run')
        self.assertTrue(any(arg.startswith('--spring.datasource.url=jdbc:mysql://127.0.0.1:3308/isolated_test?') for arg in arguments))
        self.assertTrue(any(arg.startswith('--spring.flyway.url=jdbc:mysql://127.0.0.1:3308/isolated_test?') for arg in arguments))
        self.assertNotIn('app-fixture', str(arguments)); self.assertNotIn('root-fixture', str(arguments))

    def test_shared_blocked_type_and_actual_cli_report(self):
        self.assertIs(runner.Blocked, Blocked)
        ctx = SimpleNamespace(module=None, runtime=SimpleNamespace(tokens={}), manifest={})
        with self.assertRaises(Blocked): Management(ctx).token('missing-fixture')
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory, 'BE')
            target = backend / 'tests/e2e'
            shutil.copytree(Path(runner.__file__).parent, target, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(runner.SCENARIOS, backend / 'tests/scenarios')
            result = subprocess.run([sys.executable, str(target / 'runner.py'), '--id', 'V2-CATALOG'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            report = next((backend / 'test-results').glob('*/report.json'))
            data = json.loads(report.read_text())
            self.assertEqual(data['status'], 'BLOCKED')
            self.assertEqual(data['counts']['FAIL'], 0)

    def test_audit_replay_validates_response_present_and_multiset(self):
        spec = {'add': 'assign_role_to_user', 'remove': 'remove_role_from_user', 'args': {'userId': 'carol', 'roleId': 'role-analyst'}}
        rows = [{'operation': spec['add'], 'left_id': 'carol', 'right_id': 'role-analyst', 'success': 1, 'changed': 1},
                {'operation': spec['remove'], 'left_id': 'carol', 'right_id': 'role-analyst', 'success': 1, 'changed': 1}]
        responses = [{'operation': spec['add'], 'relationship': spec['args'], 'changed': True, 'present': True, 'impact': {}},
                     {'operation': spec['remove'], 'relationship': spec['args'], 'changed': True, 'present': False, 'impact': {}}]
        self.assertFalse(replay_audit(list(reversed(responses)), rows, spec, False))
        bad = copy.deepcopy(responses); bad[1]['present'] = True
        with self.assertRaises(AssertionError): replay_audit(bad, rows, spec, False)
        bad = copy.deepcopy(rows); bad[0]['changed'] = 0
        with self.assertRaises(AssertionError): replay_audit(responses, bad, spec, False)

    def test_blocked_report_and_immutable_new_run(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'ROOT', Path(directory)):
            # Scenario discovery remains pointed at the checked-in scenario folder.
            for _ in range(2): self.assertEqual(runner.main(['--contract', 'v1', '--id', 'MCP-USERS']), 1)
            runs = list(Path(directory, 'test-results').iterdir())
            self.assertEqual(len(runs), 2)
            for run in runs:
                data = json.loads((run / 'report.json').read_text())
                self.assertEqual(data['status'], 'BLOCKED')
                self.assertEqual(data['counts'], {'PASS': 0, 'FAIL': 0, 'BLOCKED': 1, 'SKIPPED': 0})
                self.assertEqual(data['commands'], [])
                self.assertTrue((run / 'summary.md').exists())

if __name__ == '__main__': unittest.main()
