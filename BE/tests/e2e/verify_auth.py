#!/usr/bin/env python3
"""Independent real API/MCP login checks, optionally including Chromium.

Build first, then run with JDK 21 on PATH. Local mode creates a new temporary
MySQL database and explicitly imports generated credential hashes only after
Flyway has created the account table. Existing databases are never used.
"""
import argparse
import base64
import contextlib
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import unittest
import uuid
import zipfile
from datetime import datetime, timezone

from runtime_safety import safe_environment
from runner import sanitize
from errors import Blocked
from temporary_mysql import TemporaryMySQL
from verify_context_access import base, RecordedResult

ROOT = Path(__file__).resolve().parents[2]
ISSUER = 'https://demo-issuer.invalid'
ACTORS = ['alice', 'bob', 'carol', 'local-id-demo', 'admin']


class LoginFaultApi(http.server.BaseHTTPRequestHandler):
    """Failure-only HTTP upstream; it never fabricates successful authentication."""
    mode = 'unavailable'
    seen = []
    redirected = False
    expected_password = ''

    def log_message(self, *_):
        pass

    def do_POST(self):
        if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
            chunks, total = [], 0
            while True:
                size = int(self.rfile.readline(128).split(b';', 1)[0], 16)
                if not size:
                    self.rfile.readline(128)
                    break
                total += size
                if total > 4096:
                    raise ValueError('Failure-only upstream received an oversized request')
                chunks.append(self.rfile.read(size))
                if self.rfile.read(2) != b'\r\n':
                    raise ValueError('Malformed chunk delimiter')
            raw = b''.join(chunks)
        else:
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        value = json.loads(raw)
        type(self).seen.append({'path': self.path, 'headers': sorted(key.lower() for key in self.headers),
                               'keys': sorted(value), 'username': value.get('username'),
                               'password_matches': value.get('password') == self.expected_password})
        status = 302 if self.mode == 'redirect' else 200 if self.mode in {'malformed', 'oversize'} else 500
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        if self.mode == 'redirect':
            self.send_header('Location', '/unexpected-redirect')
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(b'x' * 20000 if self.mode == 'oversize' else b'{"internalDetail":"not-for-browser"}')

    def do_GET(self):
        type(self).redirected = True
        self.send_response(500)
        self.end_headers()


def decode_part(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def encode_part(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


class AuthenticationIntegration(unittest.TestCase):
    profile = 'fixture'
    database = None
    output = None
    browser = False
    legacy_browser = False
    legacy_scopes = None
    playwright = None
    chromium = None
    secrets = []
    evidence = {}

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

        cls.runtime = type('AuthenticationRuntime', (base.Integration,), {'start': classmethod(start)})
        cls.addClassCleanup(cls.runtime.doClassCleanups)
        cls.runtime.setUpClass()
        cls.helper = cls.runtime('test_01_initialize_tools_and_three_users')
        cls.passwords = json.loads((cls.runtime.credentials / 'login-passwords.json').read_text())
        if set(cls.passwords) != set(ACTORS) or not all(isinstance(value, str) for value in cls.passwords.values()):
            raise AssertionError('Generated password manifest has an unexpected shape')
        cls.secrets.extend(cls.passwords.values())
        cls.secrets.extend(cls.runtime.tokens.values())
        cls.accounts = json.loads((cls.runtime.credentials / 'login-accounts.json').read_text())
        cls.secrets.extend(account['passwordHash'] for account in cls.accounts)
        if cls.database is not None:
            cls.evidence['accounts_before_provisioning'] = int(cls.database.query('SELECT COUNT(*) FROM login_accounts;'))
            if cls.evidence['accounts_before_provisioning'] != 0:
                raise AssertionError('Migration must not automatically seed credential accounts')
            cls.database.query((cls.runtime.credentials / 'login-accounts.sql').read_text())
            cls.evidence['accounts_after_provisioning'] = int(cls.database.query('SELECT COUNT(*) FROM login_accounts;'))

    def setUp(self):
        self.runtime.refresh_tokens()
        self.secrets.extend(self.runtime.tokens.values())

    def login(self, username='alice', password=None, *, relay=False, port=None, expected=200, extra=None, body=None):
        if password is None:
            password = self.passwords.get(username, 'not-a-valid-password')
        if body is None:
            body = {'username': username, 'password': password}
        response = base.request(port or (self.runtime.mcp if relay else self.runtime.api),
                                '/auth/login' if relay else '/api/auth/login', body=body, extra=extra)
        status, headers, raw = response
        self.assertEqual(status, expected, 'Login HTTP status differs from contract')
        self.assertEqual({key.lower(): value for key, value in headers.items()}.get('cache-control'), 'no-store')
        self.assertFalse(any(key.lower() == 'set-cookie' for key in headers))
        self.assertLessEqual(len(raw.encode()), 16384)
        value = json.loads(raw)
        if status == 200:
            self.assertEqual(set(value), {'tokenType', 'expiresIn', 'mcpAccessToken', 'apiAccessToken', 'user'})
            self.assertEqual(value['tokenType'], 'Bearer')
            self.assertIs(type(value['expiresIn']), int)
            self.assertGreaterEqual(value['expiresIn'], 60)
            self.assertLessEqual(value['expiresIn'], 900)
            self.assertEqual(set(value['user']), {'id', 'name'})
            self.secrets.extend([value['mcpAccessToken'], value['apiAccessToken']])
        else:
            self.assertEqual(set(value), {'code'})
        return value

    def api(self, token, path='/api/me', expected=200, *, port=None, method=None, body=None):
        status, _, raw = base.request(port or self.runtime.api, path, token, body=body, method=method)
        self.assertEqual(status, expected, path)
        return json.loads(raw) if raw else None

    def call(self, token, tool='get_my_access', arguments=None):
        return self.helper.result(self.helper.call(tool, arguments, token=token))

    def signed(self, claims, key='issuer-private.pem'):
        header = encode_part(b'{"alg":"RS256","typ":"JWT"}')
        payload = encode_part(json.dumps(claims, separators=(',', ':')).encode())
        signing = (header + '.' + payload).encode()
        signature = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(self.runtime.credentials / key)],
                                   input=signing, capture_output=True, check=True).stdout
        token = signing.decode() + '.' + encode_part(signature)
        self.secrets.append(token)
        return token

    @contextlib.contextmanager
    def extra_api(self, name, *args):
        port = self.runtime.start('api-server', name, '--spring.profiles.active=' + self.profile, *args)
        stop = self.runtime.processes[-1][0]
        try:
            yield port
        finally:
            stop()

    def test_01_private_provisioning_and_hashes(self):
        directory = self.runtime.credentials
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for filename in ['issuer-private.pem', 'login-accounts.json', 'login-accounts.sql', 'login-passwords.json']:
            self.assertEqual(stat.S_IMODE((directory / filename).stat().st_mode), 0o600, filename)
        self.assertEqual({account['username'] for account in self.accounts}, set(ACTORS))
        self.assertEqual(len(set(self.passwords.values())), len(ACTORS))
        hashes = []
        for account in self.accounts:
            self.assertEqual(set(account), {'username', 'userId', 'issuer', 'subject', 'passwordHash', 'enabled'})
            self.assertEqual(account['issuer'], ISSUER)
            self.assertEqual(account['subject'], account['username'])
            self.assertEqual(account['userId'], 'U900' if account['username'] == 'local-id-demo' else account['username'])
            self.assertIs(account['enabled'], True)
            password = self.passwords[account['username']]
            self.assertRegex(password, r'^[A-Za-z0-9_-]{32}$')
            format_, iterations, salt, derived = account['passwordHash'].split('$')
            self.assertEqual((format_, iterations), ('pbkdf2-sha256', '600000'))
            self.assertEqual(len(decode_part(salt)), 16)
            self.assertEqual(len(decode_part(derived)), 32)
            actual = hashlib.pbkdf2_hmac('sha256', password.encode(), decode_part(salt), 600000, dklen=32)
            self.assertTrue(actual == decode_part(derived), 'Provisioned password does not match its hash')
            hashes.append(account['passwordHash'])
            self.assertNotIn(password, (directory / 'login-accounts.json').read_text())
            self.assertNotIn(password, (directory / 'login-accounts.sql').read_text())
        self.assertEqual(len(set(hashes)), len(ACTORS))
        before = {name: (directory / name).read_bytes() for name in ['issuer-private.pem', 'login-accounts.json', 'login-passwords.json']}
        self.runtime.refresh_tokens()
        self.assertEqual(before, {name: (directory / name).read_bytes() for name in before}, 'Fixture refresh changed credentials')
        for module in ['api-server', 'mcp-server']:
            with zipfile.ZipFile(ROOT / module / 'build/libs' / f'{module}-0.1.0.jar') as archive:
                self.assertFalse(any(re.search(r'(?:private\.pem|login-(?:passwords|accounts)\.(?:json|sql))$', name)
                                     for name in archive.namelist()))

    def test_02_login_tokens_and_direct_delegated_identity_parity(self):
        for actor in ACTORS:
            with self.subTest(actor=actor):
                result = self.login(actor, relay=True)
                self.assertEqual(result['expiresIn'], 900)
                expected_id = 'U900' if actor == 'local-id-demo' else actor
                self.assertEqual(result['user']['id'], expected_id)
                for key, audience, scopes in [('mcpAccessToken', 'access-mcp', {'mcp.connect', 'authz.read'}),
                                               ('apiAccessToken', 'access-api', {'access.read'})]:
                    token = result[key]
                    claims = base.claims(token)
                    self.assertEqual(json.loads(decode_part(token.split('.')[0]))['alg'], 'RS256')
                    self.assertEqual(claims['iss'], ISSUER)
                    self.assertEqual(claims['sub'], actor)
                    audiences = [claims['aud']] if isinstance(claims['aud'], str) else claims['aud']
                    self.assertEqual(audiences, [audience])
                    self.assertEqual(claims['exp'] - claims['iat'], result['expiresIn'])
                    self.assertEqual(claims['nbf'], claims['iat'])
                    self.assertLessEqual(abs(claims['iat'] - time.time()), 5)
                    if actor == 'admin':
                        scopes.add('authz.manage' if audience == 'access-mcp' else 'access.manage')
                    self.assertEqual(set(claims['scope'].split()), scopes)
                    for forbidden in ['act', 'original_iss', 'roles', 'grants', 'userId']:
                        self.assertNotIn(forbidden, claims)
                self.assertNotEqual(result['mcpAccessToken'], result['apiAccessToken'])
                direct = self.api(result['apiAccessToken'])
                delegated = self.call(result['mcpAccessToken'])
                self.assertEqual(direct, delegated)
                self.assertEqual(direct['user']['id'], expected_id)
                self.assertEqual(result['user']['name'], direct['user']['name'])
                self.assertEqual(self.api(result['apiAccessToken'], '/api/me/pages'),
                                 self.call(result['mcpAccessToken'], 'list_my_pages'))
                self.assertEqual(self.api(result['apiAccessToken'], '/api/catalog/users'),
                                 self.call(result['mcpAccessToken'], 'list_access_entities', {'entity': 'users'}))

    def test_03_bad_credentials_are_indistinguishable(self):
        for relay in [False, True]:
            values = []
            for username, password in [('alice', 'wrong-password'), ('missing-user', 'wrong-password'),
                                       ('Alice', self.passwords['alice']), ('alice', ' ' + self.passwords['alice'])]:
                values.append(self.login(username, password, relay=relay, expected=401))
            self.assertEqual(values, [{'code': 'INVALID_CREDENTIALS'}] * len(values))

    def test_04_strict_login_shape_size_and_chunked_bodies(self):
        valid = json.dumps({'username': 'alice', 'password': self.passwords['alice']}).encode()
        oversized = valid + b' ' * 5000  # Otherwise-valid credentials: a missing size bound would authenticate.
        wrong = [b'{}', b'[]', b'null', b'{',
                 b'{"username":"alice","password":"a","password":"b"}',
                 b'{"username":"alice","username":"bob","password":"x"}',
                 {'username': 'alice', 'password': 'x', 'userId': 'admin'},
                 {'username': 'alice', 'password': 'x', 'scope': 'authz.manage'},
                 {'username': 'alice', 'password': 'x', 'targetUrl': 'http://example.invalid'},
                 {'username': 7, 'password': 'x'}, {'username': 'alice', 'password': False},
                 {'username': '', 'password': 'x'}, {'username': 'alice ', 'password': 'x'},
                 {'username': 'a' * 65, 'password': 'x'}, {'username': 'álîce', 'password': 'x'},
                 {'username': 'alice', 'password': ''}, {'username': 'alice', 'password': 'x' * 257}, oversized]
        for relay in [False, True]:
            for index, body in enumerate(wrong):
                with self.subTest(relay=relay, invalid_shape=index):
                    self.assertEqual(self.login(relay=relay, body=body, expected=400), {'code': 'INVALID_ARGUMENT'})
            port, path = (self.runtime.mcp, '/auth/login') if relay else (self.runtime.api, '/api/auth/login')
            chunks = [valid, b' ' * 2500, b' ' * 2500]
            status, headers, raw = base.request(port, path, body=chunks, chunked=True)
            self.assertEqual((status, json.loads(raw)), (400, {'code': 'INVALID_ARGUMENT'}))
            self.assertEqual({k.lower(): v for k, v in headers.items()}.get('cache-control'), 'no-store')
            encoded_path = path.replace('login', '%6cogin')
            for chunked in [False, True]:
                with self.subTest(relay=relay, encoded_path=True, chunked=chunked):
                    status, _, _ = base.request(port, encoded_path, body=chunks if chunked else oversized, chunked=chunked)
                    self.assertIn(status, {400, 401, 403, 404, 413}, 'Encoded login path bypassed the strict body boundary')

    def test_05_origin_host_relay_and_protected_routes(self):
        alice = self.login(relay=True, extra={'Origin': f'http://127.0.0.1:{self.runtime.mcp}',
                           'Authorization': 'Bearer ' + self.runtime.tokens['mcp-bob'], 'X-User-Id': 'admin'})
        self.assertEqual(alice['user']['id'], 'alice')
        login_body = {'username': 'alice', 'password': self.passwords['alice']}
        for port, path in [(self.runtime.api, '/api/auth/login'), (self.runtime.mcp, '/auth/login')]:
            for headers in [{'Host': 'evil.invalid'}, {'Origin': 'https://evil.invalid'}]:
                self.assertEqual(base.request(port, path, body=login_body, extra=headers)[0], 403)
        self.assertEqual(base.request(self.runtime.api, '/api/auth/login', body=login_body,
                                     extra={'Origin': f'http://127.0.0.1:{self.runtime.api}'})[0], 403)
        for path in ['/api/me', '/api/me/pages', '/api/catalog/users', '/api/me/context-access?targetType=PAGE&pageId=page-customers']:
            self.api(None, path, expected=401)
            self.api(alice['mcpAccessToken'], path, expected=401)
        for method in ['tools/list', 'tools/call']:
            params = {'name': 'get_my_access', 'arguments': {}} if method == 'tools/call' else None
            self.assertEqual(base.rpc(self.runtime.mcp, None, method, params)[0], 401)
            self.assertEqual(base.rpc(self.runtime.mcp, alice['apiAccessToken'], method, params)[0], 401)
        self.api(None, '/api/manage/users/carol/roles/role-analyst', expected=401, method='PUT')
        self.api(alice['apiAccessToken'], '/api/manage/users/carol/roles/role-analyst', expected=403, method='PUT')
        relationship = {'userId': 'carol', 'roleId': 'role-billing'}
        self.helper.error(self.helper.call('assign_role_to_user', relationship, token=alice['mcpAccessToken']), 'FORBIDDEN')
        admin = self.login('admin', relay=True)
        path = '/api/manage/users/carol/roles/role-billing'
        try:
            added = self.call(admin['mcpAccessToken'], 'assign_role_to_user', relationship)
            self.assertTrue(added['changed'])
            self.assertTrue(added['present'])
        finally:
            removed = self.api(admin['apiAccessToken'], path, method='DELETE')
        self.assertTrue(removed['changed'])
        self.assertFalse(removed['present'])
        self.evidence['login_admin_management'] = {'mcp_add': 'PASS', 'direct_api_remove': 'PASS',
                                                   'ordinary_login_mcp_and_api': 'FORBIDDEN'}

    def test_06_direct_jwt_trust_branches_and_raw_claims(self):
        login = self.login()
        original = base.claims(login['apiAccessToken'])
        changes = [({'aud': ['access-mcp']}, None), ({'iss': 'https://attacker.invalid'}, None),
                   ({'iss': 'https://demo-delegation.invalid'}, None), ({'sub': 7}, None),
                   ({'sub': ''}, None), ({'iss': 7}, None), ({'exp': '9999999999'}, None),
                   ({'nbf': str(int(time.time()))}, None), ({'exp': int(time.time()) - 5}, None),
                   ({'nbf': int(time.time()) + 300}, None), ({'act': {'sub': 'access-mcp'}}, None),
                   ({'original_iss': ISSUER}, None), ({'act': None}, None), ({'original_iss': None}, None),
                   ({}, 'delegation-private.pem')]
        for index, (patch, key) in enumerate(changes):
            with self.subTest(malformed_claim=index):
                self.api(self.signed({**original, **patch}, key or 'issuer-private.pem'), expected=401)
        for field in ['sub', 'iss', 'aud', 'exp']:
            with self.subTest(missing_claim=field):
                claims = dict(original)
                del claims[field]
                self.api(self.signed(claims), expected=401)
        self.api(self.signed({**original, 'scope': ''}), expected=403)
        self.assertEqual(self.api(self.signed({**original, 'sub': 'missing-user'}), expected=403), {'code': 'FORBIDDEN'})
        self.assertEqual(self.api(self.runtime.tokens['api-valid'])['user']['id'], 'alice')
        forged_admin = self.signed({**original, 'scope': 'access.read access.manage', 'roles': ['SYSTEM_ADMIN']})
        self.api(forged_admin, '/api/manage/users/carol/roles/role-analyst', expected=403, method='PUT')

    def test_07_fixture_disabled_missing_manifest_and_configured_ttl(self):
        if self.profile == 'fixture':
            disabled_path = self.runtime.credentials / 'disabled-accounts.json'
            disabled = [dict(account, enabled=False) if account['username'] == 'alice' else account for account in self.accounts]
            disabled_path.write_text(json.dumps(disabled))
            disabled_path.chmod(0o600)
            with self.extra_api('auth-disabled-fixture', '--auth.fixture-accounts=file:' + str(disabled_path)) as port:
                disabled_result = self.login(port=port, expected=401)
                self.assertEqual(disabled_result, self.login('missing-user', port=port, expected=401))
                self.assertEqual(disabled_result, self.login(password='wrong-password', port=port, expected=401))
                self.assertEqual(self.api(self.runtime.tokens['api-valid'], port=port)['user']['id'], 'alice')
            with self.extra_api('auth-missing-fixture', '--auth.fixture-accounts=file:' + str(self.runtime.credentials / 'not-provisioned.json')) as port:
                self.assertEqual(self.login(port=port, expected=503), {'code': 'AUTH_UNAVAILABLE'})
                self.assertEqual(self.api(self.runtime.tokens['api-valid'], port=port)['user']['id'], 'alice')
        with self.extra_api('auth-ttl-60', '--auth.token-ttl-seconds=60') as port:
            result = self.login(port=port)
            self.assertEqual(result['expiresIn'], 60)
            for key in ['mcpAccessToken', 'apiAccessToken']:
                claims = base.claims(result[key])
                self.assertEqual(claims['exp'] - claims['iat'], 60)
            self.assertEqual(self.api(result['apiAccessToken'], port=port)['user']['id'], 'alice')

    def test_08_mysql_explicit_provisioning_disabled_and_restart(self):
        if self.database is None:
            self.skipTest('Actual account persistence and Flyway checks require isolated MySQL')
        db = self.database
        self.assertEqual(self.evidence['accounts_before_provisioning'], 0)
        self.assertEqual(self.evidence['accounts_after_provisioning'], 5)
        history_sql = 'SELECT version,script,checksum,success FROM flyway_schema_history ORDER BY installed_rank;'
        history = db.query(history_sql)
        rows = [row.split('\t') for row in history.splitlines()]
        self.assertEqual([row[0] for row in rows], [str(value) for value in range(1, 8)])
        self.assertEqual(rows[-1][1], 'V7__login_accounts_schema.sql')
        self.assertTrue(all(row[-1] == '1' for row in rows))
        accounts_sql = 'SELECT HEX(username),user_id,password_hash,enabled FROM login_accounts ORDER BY username;'
        before = db.query(accounts_sql)
        for row in before.splitlines():
            username, user_id, password_hash, enabled = row.split('\t')
            username = bytes.fromhex(username).decode()
            source = next(account for account in self.accounts if account['username'] == username)
            self.assertEqual((user_id, password_hash, enabled), (source['userId'], source['passwordHash'], '1'))
        self.assertNotEqual(db.execute((self.runtime.credentials / 'login-accounts.sql').read_text()).returncode, 0)
        self.assertEqual(db.query(accounts_sql), before, 'Duplicate provisioning must not reset accounts')
        try:
            db.query("UPDATE login_accounts SET enabled=FALSE WHERE username='alice';")
            disabled = self.login(expected=401)
            self.assertEqual(disabled, self.login('missing-user', expected=401))
            self.assertEqual(disabled, self.login(password='wrong-password', expected=401))
        finally:
            db.query("UPDATE login_accounts SET enabled=TRUE WHERE username='alice';")
        with self.extra_api('auth-same-db-restart') as port:
            self.assertEqual(self.login('local-id-demo', port=port)['user']['id'], 'U900')
            self.assertEqual(self.login(port=port)['user']['id'], 'alice')
            self.assertEqual(db.query(history_sql), history)
            self.assertEqual(db.query(accounts_sql), before)

    def test_09_mysql_live_admin_authority_and_relogin(self):
        if self.database is None:
            self.skipTest('Live administrator role changes require isolated MySQL')
        db = self.database
        admin = self.login('admin')
        alice = self.login('alice')
        path = '/api/manage/users/carol/roles/role-billing'
        try:
            db.query("DELETE FROM user_roles WHERE user_id='admin' AND role_id='SYSTEM_ADMIN';")
            self.api(admin['apiAccessToken'], path, expected=403, method='PUT')
            self.helper.error(self.helper.call('assign_role_to_user', {'userId': 'carol', 'roleId': 'role-billing'},
                                              token=admin['mcpAccessToken']), 'FORBIDDEN')
            fresh = self.login('admin')
            self.assertNotIn('access.manage', base.claims(fresh['apiAccessToken'])['scope'].split())
        finally:
            db.query("INSERT IGNORE INTO user_roles(user_id,role_id) VALUES ('admin','SYSTEM_ADMIN');")
        try:
            db.query("INSERT INTO user_roles(user_id,role_id) VALUES ('alice','SYSTEM_ADMIN');")
            self.api(alice['apiAccessToken'], path, expected=403, method='PUT')
            self.helper.error(self.helper.call('assign_role_to_user', {'userId': 'carol', 'roleId': 'role-billing'},
                                              token=alice['mcpAccessToken']), 'FORBIDDEN')
            refreshed = self.login('alice')
            self.assertIn('access.manage', base.claims(refreshed['apiAccessToken'])['scope'].split())
            self.assertTrue(self.api(refreshed['apiAccessToken'], path, method='PUT')['changed'])
        finally:
            db.query("DELETE FROM user_roles WHERE user_id='alice' AND role_id='SYSTEM_ADMIN';")
            self.api(admin['apiAccessToken'], path, method='DELETE')
        self.assertEqual(db.query("SELECT COUNT(*) FROM user_roles WHERE role_id='SYSTEM_ADMIN';"), '1')

    def test_10_actual_browser_login_chat_logout_and_account_switch(self):
        if not self.browser:
            self.skipTest('Browser explicitly disabled; use --browser with existing Playwright/Chromium')
        env = {**safe_environment(os.environ), 'E2E_BASE_URL': f'http://127.0.0.1:{self.runtime.mcp}',
               'E2E_FIXTURE_DIR': str(self.runtime.credentials), 'E2E_OUTPUT_DIR': str(self.output),
               'E2E_PLAYWRIGHT_MODULE': str(self.playwright), 'E2E_CHROMIUM_EXECUTABLE': str(self.chromium)}
        process = subprocess.run(['node', str(Path(__file__).with_name('browser_auth.mjs'))],
                                 capture_output=True, text=True, env=env, timeout=150)
        lines = [line for line in process.stdout.splitlines() if line.startswith('{')]
        if not lines:
            self.fail('Browser did not produce a structured result')
        browser = json.loads(lines[-1])
        self.evidence['browser'] = browser
        if browser.get('status') == 'BLOCKED':
            raise Blocked(browser.get('reason', 'Browser runtime unavailable'))
        self.assertEqual(browser.get('status'), 'PASS', 'Browser check failed; see sanitized browser evidence')
        self.assertEqual(process.returncode, 0)

    def test_11_relay_pins_route_strips_headers_and_sanitizes_upstream_failures(self):
        LoginFaultApi.seen = []
        LoginFaultApi.redirected = False
        LoginFaultApi.expected_password = self.passwords['alice']
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), LoginFaultApi)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stop = None
        try:
            port = self.runtime.start('mcp-server', 'auth-fault-relay',
                                      '--access.base-url=http://127.0.0.1:' + str(server.server_port))
            stop = self.runtime.processes[-1][0]
            for mode in ['unavailable', 'redirect', 'malformed', 'oversize']:
                with self.subTest(upstream_mode=mode):
                    LoginFaultApi.mode = mode
                    value = self.login(relay=True, port=port, expected=503,
                                       extra={'Origin': f'http://127.0.0.1:{port}', 'X-User-Id': 'admin',
                                              'Authorization': 'Bearer ' + self.runtime.tokens['mcp-bob']})
                    self.assertEqual(value, {'code': 'AUTH_UNAVAILABLE'})
                    seen = LoginFaultApi.seen[-1]
                    self.assertEqual(seen['path'], '/api/auth/login')
                    self.assertEqual(seen['keys'], ['password', 'username'])
                    self.assertEqual(seen['username'], 'alice')
                    self.assertTrue(seen['password_matches'])
                    self.assertTrue(set(seen['headers']).isdisjoint({'authorization', 'origin', 'x-user-id'}))
                    self.assertFalse(LoginFaultApi.redirected, 'Login relay must not follow redirects')
        finally:
            if stop is not None:
                stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            LoginFaultApi.expected_password = ''

    def test_12_existing_browser_read_management_and_discovery(self):
        if not self.legacy_browser:
            self.skipTest('Legacy browser scopes run separately with --legacy-browser on isolated MySQL')
        self.assertIsNotNone(self.database, 'Legacy mutation browser scopes require isolated MySQL')
        module = self.playwright / 'index.mjs' if self.playwright.is_dir() else self.playwright
        selectors = Path(__file__).with_name('discovery-selectors.json').read_text()
        cases = [('read', 'browser.mjs'), ('management', 'browser_management.mjs'),
                 ('management_faults', 'browser_management.mjs')]
        cases += [('discovery-' + mode, 'browser_discovery.mjs') for mode in ['guide', 'graph', 'catalog', 'manage', 'switch', 'mobile']]
        if self.legacy_scopes:
            cases = [case for case in cases if case[0] in self.legacy_scopes]
        results = self.evidence['legacy_browser'] = []
        relationship = {'userId': 'carol', 'roleId': 'role-analyst'}
        originally_present = self.database.query("SELECT COUNT(*) FROM user_roles WHERE user_id='carol' AND role_id='role-analyst';") == '1'
        for mode, script in cases:
            with self.subTest(browser_scope=mode):
                self.runtime.refresh_tokens()
                self.secrets.extend(self.runtime.tokens.values())
                self.call(self.runtime.tokens['mcp-admin'], 'remove_role_from_user', relationship)
                try:
                    env = {**safe_environment(os.environ), 'E2E_BASE_URL': f'http://127.0.0.1:{self.runtime.mcp}',
                           'E2E_FIXTURE_DIR': str(self.runtime.credentials), 'E2E_OUTPUT_DIR': str(self.output),
                           'E2E_PLAYWRIGHT_MODULE': str(module), 'E2E_CHROMIUM_EXECUTABLE': str(self.chromium),
                           'E2E_BROWSER_MODE': mode, 'E2E_UI_SELECTORS': selectors}
                    process = subprocess.run(['node', str(Path(__file__).with_name(script))],
                                             capture_output=True, text=True, env=env, timeout=180)
                    lines = [line for line in process.stdout.splitlines() if line.startswith('{')]
                    self.assertTrue(lines, 'Legacy browser did not produce a structured result')
                    result = json.loads(lines[-1])
                    results.append({'scope': mode, **result})
                    if result.get('status') == 'BLOCKED':
                        raise Blocked(result.get('reason', 'Legacy browser runtime unavailable'))
                    self.assertEqual(result.get('status'), 'PASS', 'Legacy browser failed: ' + mode)
                    self.assertEqual(process.returncode, 0)
                finally:
                    self.runtime.refresh_tokens()
                    self.secrets.extend(self.runtime.tokens.values())
                    self.call(self.runtime.tokens['mcp-admin'],
                              'assign_role_to_user' if originally_present else 'remove_role_from_user', relationship)

    def test_99_runtime_logs_do_not_expose_credentials(self):
        for path in (self.output / 'runtime').glob('*.log'):
            raw = path.read_text(errors='replace')
            self.assertFalse(re.search(r'eyJ[\w-]+\.[\w-]+\.[\w-]+|-----BEGIN (?:RSA )?PRIVATE KEY-----', raw),
                             'Raw JWT or private key appeared in ' + path.name)
            self.assertTrue(all(secret not in raw for secret in self.secrets if secret),
                            'Credential material appeared in ' + path.name)


class AuthenticationResult(RecordedResult):
    def addError(self, test, err):
        if issubclass(err[0], Blocked):
            unittest.TextTestResult.addError(self, test, err)
            self.rows.append({'id': str(test), 'status': 'BLOCKED', 'reason': str(err[1])})
        else:
            super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None and issubclass(err[0], Blocked):
            unittest.TextTestResult.addSubTest(self, test, subtest, err)
            self.rows.append({'id': str(subtest), 'status': 'BLOCKED', 'reason': str(err[1])})
        else:
            super().addSubTest(test, subtest, err)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['fixture', 'local'], default='fixture')
    parser.add_argument('--mysql-home', type=Path)
    parser.add_argument('--mysql-library-path')
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--legacy-browser', action='store_true', help='Run existing browser scopes against isolated local MySQL')
    parser.add_argument('--legacy-scope', action='append', choices=['read', 'management', 'management_faults',
        'discovery-guide', 'discovery-graph', 'discovery-catalog', 'discovery-manage', 'discovery-switch', 'discovery-mobile'])
    parser.add_argument('--test', action='append', choices=unittest.defaultTestLoader.getTestCaseNames(AuthenticationIntegration),
                        help='Select specific existing checks for a focused rerun; default is all')
    parser.add_argument('--playwright-module', type=Path, default=os.environ.get('E2E_PLAYWRIGHT_MODULE'))
    parser.add_argument('--chromium', type=Path, default='/usr/bin/chromium')
    args = parser.parse_args()
    if args.profile == 'local' and not args.mysql_home:
        parser.error('--profile local requires --mysql-home; existing databases are never used')
    if (args.browser or args.legacy_browser) and not args.playwright_module:
        parser.error('--browser requires --playwright-module pointing to an existing module')
    if args.legacy_browser and args.profile != 'local':
        parser.error('--legacy-browser requires --profile local and its isolated MySQL')
    if args.legacy_scope and not args.legacy_browser:
        parser.error('--legacy-scope requires --legacy-browser')
    original = dict(os.environ)
    os.environ.clear()
    os.environ.update(safe_environment(original))
    output = ROOT / 'test-results' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-auth-') + uuid.uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    database = None
    secrets = AuthenticationIntegration.secrets = []
    AuthenticationIntegration.evidence = {}
    stream = io.StringIO()
    report = {'profile': args.profile, 'data_source': 'real MySQL' if args.profile == 'local' else 'explicit fixture',
              'source_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in [Path(__file__), *(Path(__file__).with_name(name) for name in
                                  ['browser_auth.mjs', 'browser.mjs', 'mcp_response_capture.mjs', 'browser_management.mjs',
                                   'browser_discovery.mjs', 'runtime_safety.py', 'temporary_mysql.py'])]},
              'build_sha256': {}, 'results': []}
    report['selection'] = {'tests': args.test or 'all', 'legacy_scopes': args.legacy_scope or 'all'}
    try:
        report['build_sha256'] = {name: hashlib.sha256((ROOT / name / 'build/libs' / f'{name}-0.1.0.jar').read_bytes()).hexdigest()
                                  for name in ['api-server', 'mcp-server']}
        if args.profile == 'local':
            database = TemporaryMySQL(args.mysql_home, args.mysql_library_path)
            secrets.append(database.password)
            database.start()
            os.environ.update(database.environment())
            report['mysql'] = {'version': database.version, 'host': '127.0.0.1', 'port': database.port,
                               'database': database.database, 'user': database.user, 'data_directory': 'owned temporary directory'}
        AuthenticationIntegration.profile = args.profile
        AuthenticationIntegration.database = database
        AuthenticationIntegration.output = output
        AuthenticationIntegration.browser = args.browser
        AuthenticationIntegration.legacy_browser = args.legacy_browser
        AuthenticationIntegration.legacy_scopes = args.legacy_scope
        AuthenticationIntegration.playwright = args.playwright_module
        AuthenticationIntegration.chromium = args.chromium
        suite = unittest.TestSuite(AuthenticationIntegration(name) for name in args.test) if args.test else unittest.defaultTestLoader.loadTestsFromTestCase(AuthenticationIntegration)
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=AuthenticationResult).run(suite)
        status = 'PASS' if result.wasSuccessful() else 'FAIL' if any(row['status'] == 'FAIL' for row in result.rows) else 'BLOCKED'
        report.update(status=status, tests_run=result.testsRun,
                      results=result.rows, evidence=AuthenticationIntegration.evidence)
    except Exception as error:
        report['status'] = 'BLOCKED'
        report['results'].append({'id': 'runtime_setup', 'status': 'BLOCKED', 'reason': str(error)})
    finally:
        if database is not None:
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
        test_text = sanitize(stream.getvalue(), secrets)
        (output / 'tests.txt').write_text(test_text)
        (output / 'report.json').write_text(sanitize(json.dumps(report, indent=2, ensure_ascii=False), secrets))
        summary = '# Authentication verification: ' + report.get('status', 'BLOCKED') + '\n\n'
        summary += f"Profile: {args.profile}. Test counts: {report['counts']}.\n\n"
        if args.browser:
            summary += 'The browser_auth login journey uses actual loopback HTTP responses without request interception or response mocking.\n'
        if args.legacy_browser:
            summary += 'Legacy browser scopes use the real API/MCP/MySQL. Management fault cases deliberately abort, corrupt or delay the browser response after observing actual backend success.\n'
        if not args.browser and not args.legacy_browser:
            summary += 'Browser verification was not requested.\n'
        (output / 'summary.md').write_text(summary)
        print(test_text)
        print(output / 'report.json')
    return 0 if report.get('status') == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
