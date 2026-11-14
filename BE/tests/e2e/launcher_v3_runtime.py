#!/usr/bin/env python3
"""Merged launcher/login/context checks on the explicitly assigned fresh v3 DB."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace

from launcher_runtime import Runtime, BE, REPO, sha, now, free_port, request, rpc, PROTOCOL
from errors import Blocked
from runtime_safety import safe_environment
from runner import sanitize
from verify_auth import AuthenticationIntegration
from verify_context_access import ContextAccessIntegration, RecordedResult, base

TASK = 'task_6e2418395f8e'
TOOLS = {'get_my_access', 'check_my_resource_access', 'explain_authorization',
         'list_access_entities', 'get_access_entity', 'get_requestable_roles',
         'assign_role_to_user', 'remove_role_from_user', 'attach_policy_to_role',
         'detach_policy_from_role', 'attach_resource_to_policy', 'detach_resource_from_policy',
         'list_my_pages', 'list_my_page_endpoints', 'list_my_endpoint_fields', 'check_my_context_access'}
TABLE_COUNTS = {'users': 5, 'roles': 4, 'policies': 14, 'resources': 5, 'user_roles': 4,
                'role_policies': 16, 'policy_resources': 7, 'access_pages': 2, 'access_endpoints': 1,
                'access_fields': 3, 'page_endpoints': 2, 'context_policy_rules': 9, 'login_accounts': 5}
TABLES = set(TABLE_COUNTS) | {'access_audit'}
MIGRATIONS = ['V1__access_schema.sql', 'V2__local_demo_seed.sql', 'V3__management_schema.sql',
              'V4__local_management_seed.sql', 'V5__context_access_schema.sql',
              'V6__local_context_access_seed.sql', 'V7__login_accounts_schema.sql']
HISTORY_SQL = 'SELECT version,script,checksum,success FROM flyway_schema_history ORDER BY installed_rank;'
ACCOUNT_SQL = 'SELECT HEX(username),user_id,password_hash,enabled FROM login_accounts ORDER BY username;'


class MergedRuntime(Runtime):
    def gate(self):
        if not self.args.authorization.startswith('msg_'):
            raise Blocked('Explicit MANAGER TEST_READY required')
        assert sys.version_info >= (3, 9)
        manifest = json.loads(self.args.handoff.read_text())
        for name in ['BE/scripts/run_server.py', 'BE/api-server/build/libs/api-server-0.1.0.jar',
                     'BE/mcp-server/build/libs/mcp-server-0.1.0.jar']:
            assert manifest.get(name) == sha(REPO / name), 'Approved artifact hash mismatch: ' + name
            self.source_hashes[name] = manifest[name]
        for name in ['BE/tools/DemoCredentials.java', 'BE/scripts/verify.py', 'FE/chat.html', 'FE/chat.js', 'FE/chat.css',
                     'FE/tests/chat.test.mjs', 'BE/tests/e2e/launcher_v3_runtime.py', 'BE/tests/e2e/launcher_runtime.py',
                     'BE/tests/e2e/verify_auth.py', 'BE/tests/e2e/verify_context_access.py', 'BE/tests/e2e/browser_auth.mjs',
                     'BE/tests/e2e/runtime_safety.py', 'BE/tests/e2e/test_env_launcher.py']:
            self.source_hashes[name] = sha(REPO / name)
        self.owner = BE / '.dev' / ('env-v3-' + TASK)
        self.args.compose_env = self.owner / 'mysql.env'
        assert self.args.project == 'mcp-access-env-v3' and self.args.volume == 'mcp-access-env-v3_access-data'
        self.preserved = {str(p): sha(p) for p in [BE / '.env', BE / '.env.e2e', BE / '.dev/tester-v2.env',
                          *(BE / '.dev/v2-handoff-6gckqms6').glob('*')] if p.is_file()}
        receipt = self.owner / 'ownership.json'
        self.reusing = receipt.exists()
        if self.reusing:
            assert json.loads(receipt.read_text()) == {'task': TASK, 'project': self.args.project, 'volume': self.args.volume}
        else:
            volumes = self.command(['docker', 'volume', 'ls', '--format', '{{.Name}}']).splitlines()
            containers = self.command(['docker', 'ps', '-a', '--filter', 'label=com.docker.compose.project=' + self.args.project, '--format', '{{.ID}}']).strip()
            if self.args.volume in volumes or containers:
                raise Blocked('Assigned new project/volume already exists without task ownership receipt')
            with socket.socket() as sock:
                try:
                    sock.bind(('127.0.0.1', 3309))
                except OSError:
                    raise Blocked('Assigned port 3309 occupied; no existing process touched') from None
            self.owner.mkdir(mode=0o700)
            settings = {'MYSQL_DATABASE': 'access_env_v3', 'MYSQL_USER': 'access_env_v3',
                        'MYSQL_PASSWORD': secrets.token_hex(24), 'MYSQL_ROOT_PASSWORD': secrets.token_hex(24), 'DB_PORT': '3309'}
            self.args.compose_env.write_text('\n'.join(k + '=' + v for k, v in settings.items()) + '\n')
            self.args.compose_env.chmod(0o600)
            receipt.write_text(json.dumps({'task': TASK, 'project': self.args.project, 'volume': self.args.volume}))
        self.settings = dict(line.split('=', 1) for line in self.args.compose_env.read_text().splitlines())
        assert set(self.settings) == {'MYSQL_DATABASE', 'MYSQL_USER', 'MYSQL_PASSWORD', 'MYSQL_ROOT_PASSWORD', 'DB_PORT'}
        assert {key: self.settings[key] for key in ['MYSQL_DATABASE', 'MYSQL_USER', 'DB_PORT']} == {
            'MYSQL_DATABASE': 'access_env_v3', 'MYSQL_USER': 'access_env_v3', 'DB_PORT': '3309'}
        self.secrets.extend([self.settings['MYSQL_PASSWORD'], self.settings['MYSQL_ROOT_PASSWORD']])
        self.legacy_before = self.legacy_snapshot()
        return {'authorization': self.args.authorization, 'python': sys.version.split()[0],
                'project': self.args.project, 'database': 'access_env_v3', 'port': 3309, 'volume': self.args.volume,
                'five_key_contract': True, 'task_receipt_reused': self.reusing, 'original_files': len(self.preserved),
                'legacy_readonly_snapshots': self.legacy_before}

    def legacy_snapshot(self):
        snapshots = {}
        for project, envfile in [('mcp-access-demo', BE / '.env'), ('mcp-access-e2e-v2', BE / '.dev/tester-v2.env')]:
            if not envfile.exists():
                raise Blocked('Legacy DB env unavailable for preservation proof')
            for line in envfile.read_text().splitlines():
                if '=' in line and 'PASSWORD' in line.split('=', 1)[0]:
                    self.secrets.append(line.split('=', 1)[1])
            prefix = ['docker', 'compose', '--env-file', str(envfile), '-p', project]
            query = prefix + ['exec', '-T', 'access-db', 'sh', '-c',
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --user=root --database="$MYSQL_DATABASE" --batch --skip-column-names']
            names = self.command(query, input='SHOW TABLES;').splitlines()
            assert all(re.fullmatch(r'[A-Za-z0-9_]+', name) for name in names)
            sql = HISTORY_SQL
            for table in names:
                sql += 'SHOW CREATE TABLE `' + table + '`;SELECT COUNT(*) FROM `' + table + '`;'
            raw = self.command(query, input=sql)
            volumes = self.command(['docker', 'volume', 'inspect', project + '_access-data', '--format', '{{.Name}}']).strip()
            snapshots[project] = {'schema_history_counts_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'volume': volumes}
        return snapshots

    def prepare(self):
        self.compose('config', '--quiet')
        self.compose('up', '--build', '--wait', 'access-db', timeout=600)
        raw = self.compose('ps', '--format', 'json', 'access-db')
        entries = json.loads(raw) if raw.lstrip().startswith('[') else [json.loads(x) for x in raw.splitlines() if x]
        assert len(entries) == 1 and entries[0]['Health'] == 'healthy'
        mounts = json.loads(self.command(['docker', 'inspect', '--format', '{{json .Mounts}}', entries[0]['ID']]))
        assert any(m.get('Name') == self.args.volume for m in mounts), 'Unexpected MySQL volume'
        self.baseline = self.sql("SELECT PROCESSLIST_ID FROM performance_schema.threads WHERE PROCESSLIST_USER='access_env_v3';")
        self.tmp = tempfile.TemporaryDirectory(prefix='copied-', dir=self.owner)
        self.private = Path(self.tmp.name).resolve()
        self.root = self.private / 'copied project with spaces' / 'BE'
        self.root.mkdir(parents=True)
        for relative in ['BE/scripts/run_server.py', 'BE/api-server/build/libs/api-server-0.1.0.jar', 'BE/mcp-server/build/libs/mcp-server-0.1.0.jar']:
            dest = self.root / Path(relative).relative_to('BE')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / relative, dest)
            assert sha(dest) == self.source_hashes[relative]
        self.credentials = self.owner / 'credentials'
        if not self.credentials.exists():
            self.command(['java', str(BE / 'tools/DemoCredentials.java'), str(self.credentials), '--fixtures'])
        else:
            self.command(['java', str(BE / 'tools/DemoCredentials.java'), str(self.credentials), '--refresh-fixtures'])
        self.load_secrets()
        self.passwords = json.loads((self.credentials / 'login-passwords.json').read_text())
        self.accounts = json.loads((self.credentials / 'login-accounts.json').read_text())
        self.secrets.extend(self.passwords.values())
        self.secrets.extend(row['passwordHash'] for row in self.accounts)
        self.api_port, self.mcp_port, self.unused_port = free_port(), free_port(), free_port()
        assert len({self.api_port, self.mcp_port, self.unused_port}) == 3
        self.api_env = {key: self.settings[key] for key in ['MYSQL_DATABASE', 'MYSQL_USER', 'MYSQL_PASSWORD', 'DB_PORT']}
        self.api_env.update(DEMO_PUBLIC_KEY=str(self.credentials / 'delegation-public.pem'),
                            AUTH_PUBLIC_KEY=str(self.credentials / 'issuer-public.pem'), AUTH_PRIVATE_KEY=str(self.credentials / 'issuer-private.pem'),
                            SERVER_PORT=str(self.api_port))
        self.mcp_env = {'DEMO_PUBLIC_KEY': str(self.credentials / 'issuer-public.pem'),
                        'DELEGATION_PRIVATE_KEY': str(self.credentials / 'delegation-private.pem'),
                        'DELEGATION_PUBLIC_KEY': str(self.credentials / 'delegation-public.pem'),
                        'SERVER_PORT': str(self.mcp_port), 'ACCESS_BASE_URL': 'http://127.0.0.1:' + str(self.api_port)}
        self.api_file = self.write_env(self.root / '.env.api', self.api_env)
        self.mcp_file = self.write_env(self.root / 'env with spaces/.env.mcp', self.mcp_env)
        self.sentinel = 'IGNORED_V3_CONFIG_9371'
        props = 'server.address=0.0.0.0\nserver.port=' + str(self.unused_port) + '\nspring.datasource.url=jdbc:mysql://127.0.0.1:1/untrusted\nspring.flyway.url=jdbc:mysql://127.0.0.1:1/untrusted\nlogging.pattern.console=' + self.sentinel + '%msg%n\nlogging.file.name=' + str(self.private / 'hostile-config.log') + '\n'
        yaml = 'server:\n  address: 0.0.0.0\n  port: ' + str(self.unused_port) + '\nlogging:\n  file:\n    name: ' + str(self.private / 'hostile-yaml.log') + '\n'
        for name, value in {'application.properties': props, 'application.yml': yaml, 'config/application.properties': props, 'config/nested/application.yaml': yaml}.items():
            file = self.root / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_text(value)
        self.hostile = {key: self.sentinel for key in ['JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'SPRING_APPLICATION_JSON',
            'SPRING_CONFIG_LOCATION', 'SPRING_CONFIG_IMPORT', 'SPRING_DATASOURCE_URL', 'SPRING_FLYWAY_URL', 'MYSQL_ROOT_PASSWORD',
            'AUTH_ISSUER', 'AUTH_AUDIENCE', 'AUTH_TOKEN_TTL_SECONDS', 'AUTH_FIXTURE_ACCOUNTS']}
        return {'project': self.args.project, 'volume': self.args.volume, 'healthy': True, 'fixture_directory': str(self.credentials),
                'copy_root': str(self.root), 'byte_identical_artifacts': True, 'hostile_config': ['properties', 'yaml', 'config/nested']}

    def app_sql(self, statement, allow_failure=False):
        argv = ['docker', 'compose', '--env-file', str(self.args.compose_env), '-p', self.args.project,
                'exec', '-T', 'access-db', 'sh', '-c',
                'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --host=127.0.0.1 --user="$MYSQL_USER" --database="$MYSQL_DATABASE" --batch --skip-column-names']
        row = {'argv': argv, 'cwd': str(BE), 'stdin': 'SQL passed privately; never command arguments'}
        self.commands.append(row)
        process = subprocess.run(argv, input=statement, env=self.parent, cwd=BE, capture_output=True, text=True, timeout=60)
        row['exit_code'] = process.returncode
        if not allow_failure:
            assert process.returncode == 0, 'App-account SQL failed; private SQL not printed'
        return process

    @staticmethod
    def snapshot_sql():
        sql = HISTORY_SQL
        for table in sorted(TABLE_COUNTS):
            sql += "SELECT '" + table + "',COUNT(*) FROM " + table + ';'
        return sql

    def provision_smoke(self):
        self.api = self.start('api', 'api')
        self.ready(self.api, self.api_port)
        self.refresh()
        history = [row.split('\t') for row in self.sql(HISTORY_SQL).splitlines()]
        assert [row[0] for row in history] == list(map(str, range(1, 8))) and [row[1] for row in history] == MIGRATIONS
        assert all(row[-1] == '1' for row in history)
        assert set(self.sql('SHOW TABLES;').splitlines()) == TABLES | {'flyway_schema_history'}
        count = int(self.sql('SELECT COUNT(*) FROM login_accounts;'))
        if count == 0:
            status, raw = request(self.api_port, '/api/auth/login', body={'username': 'alice', 'password': self.passwords['alice']})
            assert status == 401 and json.loads(raw) == {'code': 'INVALID_CREDENTIALS'}
            sql = (self.credentials / 'login-accounts.sql').read_text()
            assert not any(password in sql for password in self.passwords.values())
            assert 'ON DUPLICATE' not in sql.upper() and 'REPLACE INTO' not in sql.upper()
            self.app_sql(sql)
            provision = 'new task hash-only SQL inserted once by app account after V7'
        else:
            assert self.reusing and count == 5, 'Refuse to overwrite existing provisioned accounts'
            provision = 'same task accounts reused without insert or overwrite'
        rows = [line.split('\t') for line in self.app_sql(ACCOUNT_SQL).stdout.splitlines()]
        assert len(rows) == 5
        for username, user_id, password_hash, enabled in rows:
            account = next(a for a in self.accounts if a['username'] == bytes.fromhex(username).decode())
            assert (user_id, password_hash, enabled) == (account['userId'], account['passwordHash'], '1')
        account_before = self.app_sql(ACCOUNT_SQL).stdout
        duplicate = self.app_sql((self.credentials / 'login-accounts.sql').read_text(), allow_failure=True)
        assert duplicate.returncode != 0 and self.app_sql(ACCOUNT_SQL).stdout == account_before
        self.account_before = account_before
        for table, expected in TABLE_COUNTS.items():
            assert int(self.sql('SELECT COUNT(*) FROM ' + table + ';')) == expected, 'Named table seed count differs: ' + table
        self.db_before = self.sql(self.snapshot_sql())
        self.audit_before = int(self.sql('SELECT COUNT(*) FROM access_audit;'))
        self.mcp = self.start('mcp', 'mcp', self.mcp_file.relative_to(self.root), cwd=self.root)
        self.ready(self.mcp, self.mcp_port)
        init = rpc(self.mcp_port, self.tokens['mcp-alice'], 'initialize', {'protocolVersion': PROTOCOL, 'capabilities': {}, 'clientInfo': {'name': 'launcher-v3', 'version': '1'}})
        assert init['protocolVersion'] == PROTOCOL
        assert request(self.mcp_port, '/mcp', self.tokens['mcp-alice'], {'jsonrpc': '2.0', 'method': 'notifications/initialized'})[0] == 202
        names = {tool['name'] for tool in rpc(self.mcp_port, self.tokens['mcp-alice'], 'tools/list')['tools']}
        assert names == TOOLS, 'Named tool contract differs'
        self.helper = base.Integration('test_01_initialize_tools_and_three_users')
        self.helper.api, self.helper.mcp, self.helper.tokens = self.api_port, self.mcp_port, self.tokens
        return {'named_tools': sorted(names), 'migrations': MIGRATIONS, 'named_tables': sorted(TABLES),
                'seed_counts': TABLE_COUNTS, 'provisioning': provision, 'duplicate_insert_exit': duplicate.returncode,
                'account_overwrite': False, 'account_users': sorted(a['username'] for a in self.accounts)}

    def attached_suite(self, kind):
        ctx = self
        parent = AuthenticationIntegration if kind == 'auth' else ContextAccessIntegration
        class Attached(parent):
            @classmethod
            def setUpClass(cls):
                cls.profile = 'local'; cls.output = ctx.output; cls.database = None
                cls.helper = ctx.helper
                cls.runtime = SimpleNamespace(api=ctx.api_port, mcp=ctx.mcp_port, credentials=ctx.credentials, tokens=ctx.tokens)
                def refresh_tokens():
                    ctx.refresh(); cls.runtime.tokens = ctx.tokens; cls.helper.tokens = ctx.tokens
                cls.runtime.refresh_tokens = refresh_tokens
                cls.passwords = ctx.passwords; cls.accounts = ctx.accounts; cls.secrets = ctx.secrets; cls.evidence = {}
            def setUp(self):
                self.runtime.refresh_tokens()
        names = unittest.defaultTestLoader.getTestCaseNames(parent)
        selected = [name for name in names if int(name.split('_')[1]) in (range(1, 7) if kind == 'auth' else range(1, 9))]
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordedResult).run(unittest.TestSuite(Attached(name) for name in selected))
        (self.output / (kind + '-attached-tests.txt')).write_text(sanitize(stream.getvalue(), self.secrets))
        evidence = {'selected_methods': selected, 'tests_run': result.testsRun, 'results': result.rows, 'evidence': Attached.evidence}
        (self.output / (kind + '-attached-results.json')).write_text(sanitize(json.dumps(evidence, ensure_ascii=False, indent=2), self.secrets))
        assert result.wasSuccessful(), 'Attached ' + kind + ' checks failed; see sanitized evidence'
        return evidence

    def config_identity(self):
        baseline = set(self.baseline.splitlines())
        rows = [line.split('\t') for line in self.sql("SELECT PROCESSLIST_ID,PROCESSLIST_DB,PROCESSLIST_USER FROM performance_schema.threads WHERE PROCESSLIST_USER='access_env_v3';").splitlines()]
        new = [row for row in rows if row[0] not in baseline]
        assert new and all(row[1:] == ['access_env_v3', 'access_env_v3'] for row in new)
        listeners = []
        for proc, port in [(self.api, self.api_port), (self.mcp, self.mcp_port)]:
            raw = self.command(['lsof', '-nP', '-a', '-p', str(proc.pid), '-iTCP', '-sTCP:LISTEN', '-Fn'])
            names = [line[1:] for line in raw.splitlines() if line.startswith('n')]
            assert names and all(name == '127.0.0.1:' + str(port) for name in names)
            argv = self.command(['ps', '-p', str(proc.pid), '-o', 'args='])
            assert '--spring.config.location=classpath:' in argv and all(secret not in argv for secret in self.secrets)
            listeners.append({'pid': proc.pid, 'loopback_port': port, 'argv_secret_matches': 0})
        sockets = self.command(['lsof', '-nP', '-a', '-p', str(self.api.pid), '-iTCP', '-sTCP:ESTABLISHED', '-Fn'])
        assert '->127.0.0.1:3309' in sockets
        assert not (self.private / 'hostile-config.log').exists() and not (self.private / 'hostile-yaml.log').exists()
        assert all(self.sentinel not in p.read_text(errors='replace') for p in self.logs.values())
        return {'actual_new_db_connections': new, 'api_database_socket': '127.0.0.1:3309', 'listeners': listeners, 'hostile_config_effect': False}

    def browser(self):
        env = {**self.parent, 'E2E_BASE_URL': 'http://127.0.0.1:' + str(self.mcp_port),
               'E2E_FIXTURE_DIR': str(self.credentials), 'E2E_OUTPUT_DIR': str(self.output),
               'E2E_PLAYWRIGHT_MODULE': str(self.args.playwright_module), 'E2E_CHROMIUM_EXECUTABLE': str(self.args.chromium)}
        if not self.args.playwright_module.is_file() or not self.args.chromium.is_file():
            raise Blocked('Existing browser/module missing; no installation performed')
        text = self.command(['node', str(BE / 'tests/e2e/browser_auth.mjs')], env=env, timeout=180)
        result = json.loads(text.strip().splitlines()[-1])
        (self.output / 'browser-auth-result.json').write_text(sanitize(json.dumps(result, ensure_ascii=False, indent=2), self.secrets))
        assert result['status'] == 'PASS', 'Actual browser login flow failed'
        return result

    def auth_keys_restart(self):
        original_history = self.sql(HISTORY_SQL)
        missing = {}
        for key in ['AUTH_PUBLIC_KEY', 'AUTH_PRIVATE_KEY']:
            env = dict(self.api_env); env.pop(key)
            file = self.write_env(self.private / ('missing-' + key + '.env'), env)
            proc = self.start('missing-' + key, 'api', file)
            missing[key] = proc.wait(timeout=10)
            assert missing[key] == 2
        bad = self.write_env(self.private / 'mismatched-auth.env', {**self.api_env,
            'AUTH_PRIVATE_KEY': str(self.credentials / 'delegation-private.pem'), 'SERVER_PORT': str(self.unused_port)})
        proc = self.start('mismatched-auth', 'api', bad)
        try:
            self.ready(proc, self.unused_port)
            status, raw = request(self.unused_port, '/api/auth/login', body={'username': 'alice', 'password': self.passwords['alice']})
            assert status == 503 and json.loads(raw) == {'code': 'AUTH_UNAVAILABLE'}
            self.refresh()
            assert request(self.unused_port, '/api/me', self.tokens['api-valid'])[0] == 200
        finally:
            self.stop(proc)
        assert self.stop(self.api) == 130
        self.api = self.start('api-same-volume-restart', 'api')
        self.ready(self.api, self.api_port)
        status, raw = request(self.api_port, '/api/auth/login', body={'username': 'local-id-demo', 'password': self.passwords['local-id-demo']})
        data = json.loads(raw)
        assert status == 200 and data['user']['id'] == 'U900'
        self.secrets.extend([data['mcpAccessToken'], data['apiAccessToken']])
        assert self.sql(HISTORY_SQL) == original_history and self.app_sql(ACCOUNT_SQL).stdout == self.account_before
        assert self.sql(self.snapshot_sql()) == self.db_before
        return {'missing_required_key_exit': missing, 'mismatched_signing_pair_login': 503,
                'mismatch_keeps_valid_delegated_access': True, 'same_volume_restart_history_accounts_unchanged': True,
                'independent_subject_local_id': 'U900'}

    def finish(self):
        if hasattr(self, 'legacy_before'):
            assert self.legacy_snapshot() == self.legacy_before, 'Previous 3307/3308 schema/history/count/volume changed'
        if hasattr(self, 'account_before'):
            assert self.app_sql(ACCOUNT_SQL).stdout == self.account_before, 'Provisioned accounts changed'
            assert int(self.sql('SELECT COUNT(*) FROM access_audit;')) >= self.audit_before
        evidence = super().finish()
        evidence.update(legacy_databases_unchanged=True, provisioned_accounts_unchanged=hasattr(self, 'account_before'),
                        persistent_task_credentials=str(self.credentials) if hasattr(self, 'credentials') else None)
        return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--handoff', type=Path, required=True)
    parser.add_argument('--authorization', required=True)
    parser.add_argument('--previous-run')
    parser.add_argument('--playwright-module', type=Path, default='/Users/minjoon/.npm/_npx/31e32ef8478fbf80/node_modules/playwright/index.mjs')
    parser.add_argument('--chromium', type=Path, default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    args = parser.parse_args()
    args.project = 'mcp-access-env-v3'; args.volume = 'mcp-access-env-v3_access-data'
    output = BE / 'test-results' / ('launcher-v3-' + now().replace(':', '').replace('-', '').split('.')[0].replace('+0000', 'Z') + '-' + uuid.uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    ctx = MergedRuntime(args, output)
    checks = [('V3-GATE', ctx.gate), ('V3-NEW-DB', ctx.prepare), ('V3-NO-JVM', ctx.negative_placeholder),
              ('V3-SCHEMA-PROVISION-TOOLS', ctx.provision_smoke), ('V3-CONFIG-DB', ctx.config_identity),
              ('V3-LOGIN-TRUST-MANAGE', lambda: ctx.attached_suite('auth')),
              ('V3-CONTEXT', lambda: ctx.attached_suite('context')), ('V3-BROWSER', ctx.browser),
              ('V3-AUTH-KEYS-RESTART', ctx.auth_keys_restart), ('V3-JWT', ctx.authentication),
              ('V3-SIGINT-UPSTREAM', ctx.lifecycle_upstream), ('V3-DB-FAILURES', ctx.startup_failures)]
    report = {'run_id': output.name, 'previous_run': args.previous_run, 'started_at': now(), 'contract': 'env-launcher-v3',
              'python_version': sys.version.split()[0], 'profile': 'local', 'data_source': 'real launcher/API/MCP/new Docker MySQL/browser',
              'results': [], 'limitations': ['Separate fixture and offline test reports are not MySQL evidence.',
                  'Audit rows from authorized management tests are retained; relationships/accounts/history remain preserved.']}
    blocked = False
    for case, function in checks:
        row = {'id': case, 'started_at': now(), 'expected': 'Approved v3 combined contract and assertions', 'evidence': ['report.json']}
        if blocked:
            row.update(status='BLOCKED', actual='Prerequisite failed')
        else:
            try:
                row.update(status='PASS', actual=function())
            except Exception as error:
                row.update(status='BLOCKED' if isinstance(error, Blocked) else 'FAIL', actual=str(error) if isinstance(error, (AssertionError, Blocked)) else type(error).__name__)
                blocked = True
        row['finished_at'] = now(); report['results'].append(row); print(row['status'], case, flush=True)
    try:
        report['results'].append({'id': 'V3-CLEANUP', 'status': 'PASS', 'actual': ctx.finish()})
    except Exception as error:
        # Always stop owned groups even when preservation verification itself fails.
        for proc in ctx.processes:
            if proc.poll() is None:
                ctx.stop(proc)
        report['results'].append({'id': 'V3-CLEANUP', 'status': 'FAIL', 'actual': type(error).__name__})
    report.update(source_hashes=ctx.source_hashes, commands=ctx.commands, finished_at=now())
    report['counts'] = {s: sum(row['status'] == s for row in report['results']) for s in ['PASS', 'FAIL', 'BLOCKED', 'SKIPPED']}
    report['status'] = 'PASS' if not report['counts']['FAIL'] and not report['counts']['BLOCKED'] else 'FAIL'
    (output / 'report.json').write_text(sanitize(json.dumps(report, ensure_ascii=False, indent=2), ctx.secrets))
    (output / 'summary.md').write_text('# Merged launcher v3\n\n' + report['status'] + '\n\n' + '\n'.join('- ' + row['id'] + ': ' + row['status'] for row in report['results']) + '\n')
    print(output / 'report.json')
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
