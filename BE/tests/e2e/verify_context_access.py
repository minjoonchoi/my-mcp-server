#!/usr/bin/env python3
"""Real contextual API/MCP checks against fixture or a caller-owned local MySQL.

Build the applications first. Run with a JDK 21 on PATH:
  python3 tests/e2e/verify_context_access.py --profile fixture
  python3 tests/e2e/verify_context_access.py --profile local --mysql-home /path/to/mysql
The local mode creates and removes only its own temporary MySQL data directory.
No Docker daemon, existing database or user volume is touched.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from urllib.parse import urlencode
import uuid

from runtime_safety import safe_environment
from runner import sanitize
from temporary_mysql import TemporaryMySQL

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('context_verify_base', ROOT / 'scripts/verify.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
NEW_TOOLS = {
    'list_my_pages': {},
    'list_my_page_endpoints': {'pageId': 'page-customers'},
    'list_my_endpoint_fields': {'pageId': 'page-customers', 'endpointId': 'endpoint-customer-get', 'direction': 'RESPONSE'},
    'check_my_context_access': {'targetType': 'PAGE', 'pageId': 'page-customers'},
}
LEGACY_TOOLS = {'get_my_access', 'check_my_resource_access', 'explain_authorization',
                'list_access_entities', 'get_access_entity', 'get_requestable_roles',
                'assign_role_to_user', 'remove_role_from_user', 'attach_policy_to_role',
                'detach_policy_from_role', 'attach_resource_to_policy', 'detach_resource_from_policy'}
CUSTOMERS, AUDIT, ENDPOINT = 'page-customers', 'page-audit', 'endpoint-customer-get'
ID_FIELD, NAME_FIELD, EMAIL_FIELD = 'field-customer-id-request', 'field-customer-name-response', 'field-customer-email-response'


class ContextAccessIntegration(unittest.TestCase):
    profile = 'fixture'
    database = None
    output = None

    @classmethod
    def setUpClass(cls):
        base.API_PROFILE = cls.profile
        base.REPORT = cls.output / 'runtime'
        def start(runtime, module, name, *args):
            config = 'classpath:/application.properties'
            if module == 'api-server':
                config += ',classpath:/application-' + cls.profile + '.properties'
            return base.Integration.start.__func__(runtime, module, name, *args,
                                                   '--spring.config.location=' + config)
        cls.runtime = type('ContextRuntime', (base.Integration,), {'start': classmethod(start)})
        cls.addClassCleanup(cls.runtime.doClassCleanups)
        cls.runtime.setUpClass()
        cls.helper = cls.runtime('test_01_initialize_tools_and_three_users')

    def setUp(self):
        self.runtime.refresh_tokens()

    def call(self, name, args=None, actor='alice'):
        value = self.helper.result(self.helper.call(name, args, user=actor))
        self.assertLess(len(json.dumps(value, ensure_ascii=False).encode()), 16384)
        return value

    def tool_error(self, name, args, code, actor='alice', token=None):
        self.helper.error(self.helper.call(name, args, user=actor, token=token), code)

    def api(self, path, actor='alice', expected=200, port=None):
        fixture = 'api-valid' if actor == 'alice' else 'api-admin-read' if actor == 'admin' else 'api-' + actor
        status, _, raw = base.request(port or self.runtime.api, path, self.runtime.tokens[fixture])
        self.assertEqual(status, expected, path)
        if expected == 200:
            self.assertLess(len(raw.encode()), 16384)
        return json.loads(raw) if raw else None

    def decision(self, page=CUSTOMERS, endpoint=None, field=None, actor='alice'):
        args = {'targetType': 'FIELD' if field else 'ENDPOINT' if endpoint else 'PAGE', 'pageId': page}
        if endpoint is not None:
            args['endpointId'] = endpoint
        if field is not None:
            args['fieldId'] = field
        result = self.call('check_my_context_access', args, actor)
        self.assertEqual(set(result), {'targetType', 'pageId', 'endpointId', 'fieldId', 'allowed', 'reason', 'stages'})
        self.assertEqual(result['targetType'], args['targetType'])
        self.assertEqual((result['pageId'], result['endpointId'], result['fieldId']), (page, endpoint, field))
        self.assertEqual(result, self.api('/api/me/context-access?' + urlencode(args), actor))
        return result

    def mutation(self, name, args, actor='admin'):
        return self.call(name, args, actor)

    def test_01_additive_tool_contract_and_page_discovery(self):
        status, _, payload = base.rpc(self.runtime.mcp, self.runtime.tokens['mcp-alice'], 'tools/list')
        self.assertEqual(status, 200)
        tools = payload['result']['tools']
        self.assertEqual({tool['name'] for tool in tools}, LEGACY_TOOLS | set(NEW_TOOLS))
        for tool in tools:
            if tool['name'] in NEW_TOOLS:
                self.assertFalse(tool['inputSchema']['additionalProperties'])
                self.assertTrue(tool['annotations']['readOnlyHint'])
                self.assertFalse(tool['annotations']['destructiveHint'])
                self.assertTrue(tool['annotations']['idempotentHint'])
        pages = self.call('list_my_pages')
        self.assertEqual([page['id'] for page in pages['items']], [AUDIT, CUSTOMERS])
        self.assertIsNone(pages['nextCursor'])
        self.assertEqual(pages, self.api('/api/me/pages'))
        for actor in ['bob', 'carol', 'local-id-demo', 'admin']:
            with self.subTest(actor=actor):
                self.assertEqual(self.call('list_my_pages', actor=actor), {'items': [], 'nextCursor': None})
                self.assertEqual(self.call('list_my_pages', actor=actor), self.api('/api/me/pages', actor))

    def test_02_decisions_have_real_same_context_role_policy_provenance(self):
        page = self.decision()
        self.assertEqual(page['reason'], 'ALLOWED')
        self.assertTrue(page['allowed'])
        self.assertEqual(page['stages'], [{'stage': 'PAGE', 'allowed': True,
            'grants': [{'roleId': 'role-analyst', 'policyId': 'ctx-page-customers'}], 'grantsTruncated': False}])
        customer_name = self.decision(endpoint=ENDPOINT, field=NAME_FIELD)
        self.assertTrue(customer_name['allowed'])
        self.assertEqual([stage['stage'] for stage in customer_name['stages']], ['PAGE', 'ENDPOINT', 'FIELD'])
        audit_email = self.decision(AUDIT, ENDPOINT, EMAIL_FIELD)
        self.assertTrue(audit_email['allowed'])
        self.assertEqual([stage['grants'][0]['roleId'] for stage in audit_email['stages']],
                         ['role-editor', 'role-editor', 'role-analyst'])
        self.assertEqual(audit_email['stages'][-1]['grants'][0]['policyId'], 'ctx-field-audit-email')
        customer_email = self.decision(CUSTOMERS, ENDPOINT, EMAIL_FIELD)
        self.assertFalse(customer_email['allowed'])
        self.assertEqual(customer_email['reason'], 'FIELD_NOT_GRANTED')
        self.assertEqual(customer_email['stages'][-1], {'stage': 'FIELD', 'allowed': False, 'grants': [], 'grantsTruncated': False})

    def test_03_endpoint_and_request_response_field_discovery(self):
        for page in [CUSTOMERS, AUDIT]:
            endpoints = self.call('list_my_page_endpoints', {'pageId': page})
            self.assertEqual([item['id'] for item in endpoints['items']], [ENDPOINT])
            self.assertEqual(endpoints, self.api(f'/api/me/pages/{page}/endpoints'))
            for direction, expected in [('REQUEST', [ID_FIELD]), ('RESPONSE', [NAME_FIELD] if page == CUSTOMERS else [EMAIL_FIELD, NAME_FIELD])]:
                args = {'pageId': page, 'endpointId': ENDPOINT, 'direction': direction}
                fields = self.call('list_my_endpoint_fields', args)
                self.assertEqual([item['id'] for item in fields['items']], expected)
                self.assertTrue(all(item['direction'] == direction and item['endpointId'] == ENDPOINT for item in fields['items']))
                self.assertEqual(fields, self.api(f'/api/me/pages/{page}/endpoints/{ENDPOINT}/fields?direction={direction}'))
                self.assertIsNone(fields['nextCursor'])

    def test_04_denied_and_missing_targets_have_identical_safe_shape(self):
        known = self.decision(CUSTOMERS, ENDPOINT, EMAIL_FIELD, 'bob')
        missing = self.decision('page-missing', 'endpoint-missing', 'field-missing', 'bob')
        for value in [known, missing]:
            self.assertFalse(value['allowed'])
            self.assertEqual(value['reason'], 'PAGE_NOT_GRANTED')
            self.assertEqual(value['stages'], [{'stage': 'PAGE', 'allowed': False, 'grants': [], 'grantsTruncated': False}])
        self.assertEqual(self.api(f'/api/me/pages/{CUSTOMERS}/endpoints', 'bob', 404),
                         self.api('/api/me/pages/page-missing/endpoints', 'bob', 404))
        for page in [CUSTOMERS, 'page-missing']:
            self.tool_error('list_my_page_endpoints', {'pageId': page}, 'NOT_FOUND', 'bob')
        missing_endpoint = self.decision(CUSTOMERS, 'endpoint-missing', NAME_FIELD)
        self.assertEqual(missing_endpoint['reason'], 'ENDPOINT_NOT_GRANTED')
        self.assertEqual([stage['stage'] for stage in missing_endpoint['stages']], ['PAGE', 'ENDPOINT'])
        missing_field = self.decision(CUSTOMERS, ENDPOINT, 'field-missing')
        self.assertEqual(missing_field['stages'], self.decision(CUSTOMERS, ENDPOINT, EMAIL_FIELD)['stages'])

    def test_05_strict_arguments_and_duplicate_query_parameters(self):
        invalid = [
            ('list_my_pages', {'userId': 'bob'}), ('list_my_pages', {'after': ''}),
            ('list_my_pages', {'after': '../users'}), ('list_my_pages', {'after': 1}),
            *[('list_my_pages', {'limit': limit}) for limit in [0, 6, True, 1.5, '2', None]],
            ('list_my_page_endpoints', {}), ('list_my_page_endpoints', {'pageId': ''}),
            ('list_my_endpoint_fields', {'pageId': CUSTOMERS, 'endpointId': ENDPOINT}),
            ('list_my_endpoint_fields', {'pageId': CUSTOMERS, 'endpointId': ENDPOINT, 'direction': 'response'}),
            ('check_my_context_access', {}),
            ('check_my_context_access', {'targetType': 'PAGE', 'pageId': CUSTOMERS, 'endpointId': ENDPOINT}),
            ('check_my_context_access', {'targetType': 'ENDPOINT', 'pageId': CUSTOMERS}),
            ('check_my_context_access', {'targetType': 'FIELD', 'pageId': CUSTOMERS, 'endpointId': ENDPOINT}),
            ('check_my_context_access', {'targetType': 'PAGE', 'pageId': CUSTOMERS, 'userId': 'bob'}),
            ('check_my_context_access', {'targetType': 'page', 'pageId': CUSTOMERS}),
        ]
        for tool, args in invalid:
            with self.subTest(tool=tool, args=args):
                self.tool_error(tool, args, 'INVALID_ARGUMENT')
        for path in ['/api/me/pages?limit=1&limit=2', '/api/me/pages?after=a&after=b',
                     '/api/me/pages?limit=true', '/api/me/pages?limit=1.5', '/api/me/pages?limit=6',
                     '/api/me/pages?after=', '/api/me/pages?userId=bob',
                     f'/api/me/context-access?targetType=PAGE&pageId={CUSTOMERS}&fieldId={NAME_FIELD}',
                     f'/api/me/context-access?targetType=PAGE&targetType=FIELD&pageId={CUSTOMERS}',
                     f'/api/me/pages/{CUSTOMERS}/endpoints/{ENDPOINT}/fields?direction=RESPONSE&direction=REQUEST']:
            with self.subTest(path=path):
                self.api(path, expected=400)

    def test_06_scope_identity_and_admin_are_not_domain_grants(self):
        for tool, args in NEW_TOOLS.items():
            for fixture in ['mcp-connect-only', 'mcp-admin-manage-only']:
                with self.subTest(tool=tool, fixture=fixture):
                    self.tool_error(tool, args, 'FORBIDDEN', token=self.runtime.tokens[fixture])
            self.tool_error(tool, args, 'UPSTREAM_ACCESS_DENIED', token=self.runtime.tokens['mcp-unknown'])
        self.assertEqual(base.request(self.runtime.api, '/api/me/pages')[0], 401)
        self.assertEqual(base.request(self.runtime.api, '/api/me/pages', self.runtime.tokens['api-admin-manage'])[0], 403)
        manipulated = self.helper.result(self.helper.call('list_my_pages', token=self.runtime.tokens['mcp-manipulated']))
        self.assertEqual(manipulated, {'items': [], 'nextCursor': None})
        self.assertFalse(self.decision(actor='admin')['allowed'])

    def test_07_keyset_pagination_is_complete_and_deterministic(self):
        first = self.call('list_my_pages', {'limit': 1})
        self.assertEqual([item['id'] for item in first['items']], [AUDIT])
        self.assertEqual(first['nextCursor'], AUDIT)
        second = self.call('list_my_pages', {'after': first['nextCursor'], 'limit': 1})
        self.assertEqual([item['id'] for item in second['items']], [CUSTOMERS])
        self.assertIsNone(second['nextCursor'])
        self.assertEqual(self.call('list_my_pages', {'after': CUSTOMERS}), {'items': [], 'nextCursor': None})
        fields = {'pageId': AUDIT, 'endpointId': ENDPOINT, 'direction': 'RESPONSE', 'limit': 1}
        first_field = self.call('list_my_endpoint_fields', fields)
        self.assertEqual(first_field['nextCursor'], EMAIL_FIELD)
        second_field = self.call('list_my_endpoint_fields', {**fields, 'after': EMAIL_FIELD})
        self.assertEqual([item['id'] for item in second_field['items']], [NAME_FIELD])
        self.assertIsNone(second_field['nextCursor'])

    def test_08_existing_audited_management_updates_context_immediately(self):
        additions = []
        try:
            for policy in ['ctx-page-customers', 'ctx-endpoint-customers', 'ctx-field-customers-name']:
                args = {'roleId': 'role-billing', 'policyId': policy}
                self.assertTrue(self.mutation('attach_policy_to_role', args)['changed'])
                additions.append(args)
                result = self.decision(CUSTOMERS, ENDPOINT, NAME_FIELD, 'bob')
                expected = {'ctx-page-customers': 'ENDPOINT_NOT_GRANTED', 'ctx-endpoint-customers': 'FIELD_NOT_GRANTED',
                            'ctx-field-customers-name': 'ALLOWED'}[policy]
                self.assertEqual(result['reason'], expected)
        finally:
            for args in reversed(additions):
                self.mutation('detach_policy_from_role', args)
        email_args = {'roleId': 'role-analyst', 'policyId': 'ctx-field-audit-email'}
        try:
            self.assertTrue(self.mutation('detach_policy_from_role', email_args)['changed'])
            self.assertEqual(self.decision(AUDIT, ENDPOINT, EMAIL_FIELD)['reason'], 'FIELD_NOT_GRANTED')
        finally:
            self.mutation('attach_policy_to_role', email_args)
        self.assertTrue(self.decision(AUDIT, ENDPOINT, EMAIL_FIELD)['allowed'])
        user_args = {'userId': 'carol', 'roleId': 'role-analyst'}
        try:
            self.mutation('assign_role_to_user', user_args)
            self.assertTrue(self.decision(CUSTOMERS, ENDPOINT, NAME_FIELD, 'carol')['allowed'])
            self.assertFalse(self.decision(AUDIT, ENDPOINT, EMAIL_FIELD, 'carol')['allowed'])
        finally:
            self.mutation('remove_role_from_user', user_args)
        self.assertEqual(self.call('list_my_pages', actor='carol')['items'], [])
        self.tool_error('attach_policy_to_role', {'roleId': 'role-billing', 'policyId': 'ctx-page-customers'},
                        'FORBIDDEN', token=self.runtime.tokens['mcp-alice-manage'])
        self.tool_error('assign_role_to_user', {'userId': 'carol', 'roleId': 'SYSTEM_ADMIN'}, 'FORBIDDEN', 'admin')
        for tool in ['attach_resource_to_policy', 'detach_resource_from_policy']:
            self.tool_error(tool, {'policyId': 'ctx-page-customers', 'resourceId': 'R100'}, 'INVALID_ARGUMENT', 'admin')

    def test_09_actual_mysql_migrations_constraints_audit_and_restart(self):
        if self.database is None:
            self.skipTest('Real MySQL checks are separate from fixture verification')
        db = self.database
        history_sql = 'SELECT version,script,checksum,success FROM flyway_schema_history ORDER BY installed_rank;'
        before = db.query(history_sql)
        rows = [row.split('\t') for row in before.splitlines()]
        self.assertEqual([row[0] for row in rows], ['1', '2', '3', '4', '5', '6', '7'])
        self.assertTrue(all(row[-1] == '1' for row in rows))
        counts = {'users': 5, 'roles': 4, 'policies': 14, 'resources': 5, 'user_roles': 4,
                  'role_policies': 16, 'policy_resources': 7, 'access_pages': 2, 'access_endpoints': 1,
                  'access_fields': 3, 'page_endpoints': 2, 'context_policy_rules': 9, 'login_accounts': 0}
        for table, count in counts.items():
            self.assertEqual(int(db.query(f'SELECT COUNT(*) FROM {table};')), count, table)
        self.assertGreater(int(db.query('SELECT COUNT(*) FROM access_audit WHERE actor_user_id=\'admin\' AND success=TRUE;')), 0)
        policy = "INSERT INTO policies(id,name,description) VALUES ('test-invalid','Test','Test');"
        attempts = [
            policy + "INSERT INTO context_policy_rules(policy_id,kind,page_id,endpoint_id,field_id) VALUES ('test-invalid','PAGE','page-customers','endpoint-customer-get',NULL);",
            "INSERT INTO access_endpoints(id,service,method,path,name) VALUES ('test-other','customer-api','GET','/other','Other');" + policy +
            "INSERT INTO context_policy_rules(policy_id,kind,page_id,endpoint_id,field_id) VALUES ('test-invalid','ENDPOINT','page-customers','test-other',NULL);",
            "INSERT INTO access_endpoints(id,service,method,path,name) VALUES ('test-other','customer-api','GET','/other','Other');" +
            "INSERT INTO page_endpoints(page_id,endpoint_id) VALUES ('page-customers','test-other');" + policy +
            "INSERT INTO context_policy_rules(policy_id,kind,page_id,endpoint_id,field_id) VALUES ('test-invalid','FIELD','page-customers','test-other','field-customer-name-response');",
            "INSERT INTO access_fields(id,endpoint_id,direction,location,field_path,name) VALUES ('test-invalid-field','endpoint-customer-get','RESPONSE','BODY',' ','Invalid');",
            "INSERT INTO access_pages(id,application,path,name) VALUES (CONCAT('test-page',CHAR(10)),'backoffice','/test-page','Invalid');",
            "INSERT INTO access_pages(id,application,path,name) VALUES ('test-page','backoffice',CONCAT('/test-page',CHAR(10)),'Invalid');",
            "INSERT INTO access_endpoints(id,service,method,path,name) VALUES ('test-endpoint','customer-api','GET',CONCAT('/test',CHAR(10)),'Invalid');",
            "INSERT INTO access_fields(id,endpoint_id,direction,location,field_path,name) VALUES ('test-invalid-field','endpoint-customer-get','RESPONSE','BODY',CONCAT('email',CHAR(10)),'Invalid');",
            "INSERT INTO access_endpoints(id,service,method,path,name) VALUES ('test-endpoint','customer-api',CONCAT('GET',CONVERT(0xE2808D USING utf8mb4)),'/test','Invalid');",
            "INSERT INTO access_fields(id,endpoint_id,direction,location,field_path,name) VALUES ('test-invalid-field','endpoint-customer-get',CONCAT('REQUEST',CONVERT(0xE2808D USING utf8mb4)),'BODY','test','Invalid');",
            "INSERT INTO access_fields(id,endpoint_id,direction,location,field_path,name) VALUES ('test-invalid-field','endpoint-customer-get','RESPONSE',CONCAT('BODY',CONVERT(0xE2808D USING utf8mb4)),'test','Invalid');",
            policy + "INSERT INTO context_policy_rules(policy_id,kind,page_id,endpoint_id,field_id) VALUES ('test-invalid',CONCAT('PAGE',CONVERT(0xE2808D USING utf8mb4)),'page-customers',NULL,NULL);",
        ]
        for sql in attempts:
            self.assertNotEqual(db.execute('START TRANSACTION;' + sql + 'COMMIT;').returncode, 0)
            self.assertEqual(db.query("SELECT COUNT(*) FROM policies WHERE id='test-invalid';"), '0')
        for table, count in counts.items():
            self.assertEqual(int(db.query(f'SELECT COUNT(*) FROM {table};')), count, table)
        port = self.runtime.start('api-server', 'context-same-db-restart', '--spring.profiles.active=local')
        self.assertEqual(self.api('/api/me/pages', port=port), self.api('/api/me/pages'))
        self.assertEqual(db.query(history_sql), before)


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.rows.append({'id': test._testMethodName, 'status': 'PASS'})

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.rows.append({'id': test._testMethodName, 'status': 'SKIPPED', 'reason': reason})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.rows.append({'id': str(test), 'status': 'FAIL', 'reason': self._exc_info_to_string(err, test)})

    def addError(self, test, err):
        super().addError(test, err)
        self.rows.append({'id': str(test), 'status': 'FAIL', 'reason': self._exc_info_to_string(err, test)})

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.rows.append({'id': str(subtest), 'status': 'FAIL', 'reason': self._exc_info_to_string(err, test)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['fixture', 'local'], default='fixture')
    parser.add_argument('--mysql-home', type=Path, help='Existing extracted MySQL distribution; local mode only')
    parser.add_argument('--mysql-library-path', help='Optional existing shared-library directory for supplied MySQL')
    args = parser.parse_args()
    if args.profile == 'local' and not args.mysql_home:
        parser.error('--profile local requires --mysql-home; existing databases are never used')
    original = dict(os.environ)
    clean = safe_environment(original)
    os.environ.clear()
    os.environ.update(clean)
    output = ROOT / 'test-results' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-context-') + uuid.uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    database = None
    secrets = []
    stream = io.StringIO()
    report = {'profile': args.profile, 'data_source': 'real MySQL' if args.profile == 'local' else 'explicit fixture',
              'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'build_sha256': {}, 'results': []}
    try:
        report['build_sha256'] = {name: hashlib.sha256((ROOT / name / 'build/libs' / f'{name}-0.1.0.jar').read_bytes()).hexdigest()
                                  for name in ['api-server', 'mcp-server']}
        if args.profile == 'local':
            database = TemporaryMySQL(args.mysql_home, args.mysql_library_path)
            database.start()
            secrets.append(database.password)
            os.environ.update(database.environment())
            report['mysql'] = {'version': database.version, 'host': '127.0.0.1', 'port': database.port,
                               'database': database.database, 'user': database.user, 'data_directory': 'owned temporary directory'}
        ContextAccessIntegration.profile = args.profile
        ContextAccessIntegration.database = database
        ContextAccessIntegration.output = output
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContextAccessIntegration)
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordedResult).run(suite)
        report['results'] = result.rows
        report['status'] = 'PASS' if result.wasSuccessful() else 'FAIL'
        report['tests_run'] = result.testsRun
        if hasattr(ContextAccessIntegration, 'runtime'):
            secrets.extend(getattr(ContextAccessIntegration.runtime, 'tokens', {}).values())
    except Exception as error:
        report['status'] = 'BLOCKED'
        report['results'].append({'id': 'runtime_setup', 'status': 'BLOCKED', 'reason': str(error)})
    finally:
        if database is not None:
            secrets.append(database.password)
            try:
                database.close()
            except Exception as error:
                report['status'] = 'FAIL'
                report['results'].append({'id': 'runtime_cleanup', 'status': 'FAIL', 'reason': str(error)})
        os.environ.clear()
        os.environ.update(original)
        for path in (output / 'runtime').glob('*.log'):
            path.write_text(sanitize(path.read_text(errors='replace'), secrets))
        report['counts'] = {status: sum(row['status'] == status for row in report['results'])
                            for status in ['PASS', 'FAIL', 'BLOCKED', 'SKIPPED']}
        text = sanitize(stream.getvalue(), secrets)
        (output / 'tests.txt').write_text(text)
        (output / 'report.json').write_text(sanitize(json.dumps(report, indent=2, ensure_ascii=False), secrets))
        print(text)
        print(output / 'report.json')
    return 0 if report.get('status') == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
