#!/usr/bin/env python3
"""Explicitly handed-off real launcher verification; no builds or volume deletion.

All mutations are private test fixture files and launcher-owned process groups.
MySQL is observed read-only; credentials never enter command arguments or reports.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

from runtime_safety import safe_environment
from errors import Blocked

BE = Path(__file__).resolve().parents[2]
REPO = BE.parent
PROTOCOL = '2025-06-18'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(port, path, token=None, body=None):
    headers = {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': PROTOCOL}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if body is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(body).encode()
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=12)
    try:
        connection.request('POST' if body is not None else 'GET', path, body, headers)
        response = connection.getresponse()
        return response.status, response.read().decode()
    finally:
        connection.close()


def rpc(port, token, method, params=None):
    status, raw = request(port, '/mcp', token, {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
    assert status == 200, 'RPC HTTP status mismatch'
    if raw.startswith(('event:', 'data:')):
        raw = next(line[5:].strip() for line in raw.splitlines() if line.startswith('data:'))
    payload = json.loads(raw)
    assert 'result' in payload, 'RPC did not return result'
    return payload['result']


class Runtime:
    def __init__(self, args, output):
        self.args, self.output = args, output
        self.parent = safe_environment(os.environ)
        self.commands, self.processes, self.secrets = [], [], []
        self.logs, self.source_hashes, self.preserved = {}, {}, {}
        self.observations = {}
        self.tmp = None

    def command(self, argv, *, input=None, env=None, cwd=BE, timeout=60):
        row = {'argv': list(map(str, argv)), 'cwd': str(cwd)}
        self.commands.append(row)
        result = subprocess.run(argv, input=input, env=env or self.parent, cwd=cwd,
                                capture_output=True, text=True, timeout=timeout)
        row['exit_code'] = result.returncode
        if result.returncode:
            raise Blocked('Required inspection command failed: ' + str(argv[0]))
        return result.stdout

    def compose(self, *args, **kwargs):
        return self.command(['docker', 'compose', '--env-file', str(self.args.compose_env),
                             '-p', self.args.project, *args], **kwargs)

    def sql(self, statement):
        return self.compose('exec', '-T', 'access-db', 'sh', '-c',
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --user=root --database="$MYSQL_DATABASE" --batch --skip-column-names',
            input=statement)

    def gate(self):
        if not self.args.authorization.startswith('msg_'):
            raise Blocked('Explicit MANAGER TEST_READY message reference required')
        assert sys.version_info >= (3, 9), 'Python 3.9 or newer is required'
        manifest = json.loads(self.args.handoff.read_text())
        paths = ['BE/scripts/run_server.py',
                 'BE/api-server/build/libs/api-server-0.1.0.jar',
                 'BE/mcp-server/build/libs/mcp-server-0.1.0.jar']
        for name in paths:
            expected = manifest.get(name)
            if not expected or sha(REPO / name) != expected:
                raise Blocked('Handoff artifact hash mismatch: ' + name)
            self.source_hashes[name] = expected
        if self.args.project != 'mcp-access-e2e-v2' or self.args.volume != 'mcp-access-e2e-v2_access-data':
            raise Blocked('Only the handed-off isolated project/volume are allowed')
        self.settings = dict(line.split('=', 1) for line in self.args.compose_env.read_text().splitlines()
                             if line.strip() and not line.lstrip().startswith('#'))
        safe_environment({}, self.settings)  # Preserve the existing exact five-key contract.
        if self.settings['MYSQL_DATABASE'] != 'access_e2e_v2':
            raise Blocked('Unexpected isolated database')
        self.secrets += [self.settings['MYSQL_PASSWORD'], self.settings['MYSQL_ROOT_PASSWORD']]
        protected = [self.args.compose_env, BE / '.env', BE / '.env.e2e']
        protected.extend((BE / '.dev/v2-handoff-6gckqms6').glob('*'))
        self.preserved = {str(p): sha(p) for p in protected if p.is_file()}
        self.source_hashes['BE/tests/e2e/launcher_runtime.py'] = sha(__file__)
        for module in ['api-server', 'mcp-server']:
            with zipfile.ZipFile(BE / module / 'build/libs' / (module + '-0.1.0.jar')) as jar:
                for name in jar.namelist():
                    if name.startswith('BOOT-INF/classes/application') and name.endswith(('.properties', '.yml', '.yaml')):
                        content = jar.read(name).decode()
                        assert not re.search(r'(?m)^\s*spring\.(config\.(import|activate)|profiles\.(include|group|active))', content), 'Packaged config imports/profile activation require review'
        return {'authorization': self.args.authorization, 'python_version': sys.version.split()[0],
                'packaged_import_profile_channels': 'absent', 'preserved_original_files': len(self.preserved),
                'five_key_env_contract': 'PASS'}

    def prepare(self):
        self.command(['docker', 'info', '--format', '{{.ServerVersion}}'])
        raw = self.compose('ps', '--format', 'json', 'access-db')
        entries = json.loads(raw) if raw.lstrip().startswith('[') else [json.loads(line) for line in raw.splitlines() if line]
        if not entries or entries[0].get('Health') != 'healthy':
            raise Blocked('Handed-off MySQL is not healthy; no automatic container changes performed')
        mounts = json.loads(self.command(['docker', 'inspect', '--format', '{{json .Mounts}}', entries[0]['ID']]))
        assert any(m.get('Name') == self.args.volume and m.get('Destination') == '/var/lib/mysql' for m in mounts), 'Unexpected DB volume'
        self.baseline = self.sql("SELECT PROCESSLIST_ID FROM performance_schema.threads WHERE PROCESSLIST_USER='access_e2e';")
        self.db_before = self.sql(self.snapshot_sql())
        (BE / '.dev').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='launcher-runtime-', dir=BE / '.dev')
        self.private = Path(self.tmp.name).resolve()
        self.root = self.private / 'copied project with spaces' / 'BE'
        self.root.mkdir(parents=True)
        self.credentials = self.private / 'new independent credentials'
        for name in self.source_hashes:
            if name.startswith('BE/tests/'):
                continue
            dest = self.root / Path(name).relative_to('BE')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, dest)
            assert sha(dest) == self.source_hashes[name], 'Copied artifact differs'
        self.command(['java', str(BE / 'tools/DemoCredentials.java'), str(self.credentials), '--fixtures'])
        self.load_secrets()
        self.api_port, self.mcp_port, self.unused_port = free_port(), free_port(), free_port()
        assert len({self.api_port, self.mcp_port, self.unused_port}) == 3
        self.api_env = {k: self.settings[k] for k in ['MYSQL_DATABASE', 'MYSQL_USER', 'MYSQL_PASSWORD', 'DB_PORT']}
        self.api_env.update(DEMO_PUBLIC_KEY=str(self.credentials / 'delegation-public.pem'), SERVER_PORT=str(self.api_port))
        self.mcp_env = {'DEMO_PUBLIC_KEY': str(self.credentials / 'issuer-public.pem'),
                        'DELEGATION_PRIVATE_KEY': str(self.credentials / 'delegation-private.pem'),
                        'DELEGATION_PUBLIC_KEY': str(self.credentials / 'delegation-public.pem'),
                        'SERVER_PORT': str(self.mcp_port), 'ACCESS_BASE_URL': 'http://127.0.0.1:' + str(self.api_port)}
        self.api_file = self.write_env(self.root / '.env.api', self.api_env)
        self.mcp_file = self.write_env(self.root / 'config env with spaces' / '.env.mcp', self.mcp_env)
        self.sentinel = 'EXTERNAL_CONFIG_SHOULD_NOT_LOAD_7a93'
        props = ('server.address=0.0.0.0\nserver.port=' + str(self.unused_port) +
                 '\nspring.datasource.url=jdbc:mysql://127.0.0.1:' + str(self.unused_port) + '/untrusted\n' +
                 'spring.flyway.url=jdbc:mysql://127.0.0.1:' + str(self.unused_port) + '/untrusted\n' +
                 'logging.pattern.console=' + self.sentinel + '%msg%n\nlogging.file.name=' + str(self.private / 'hostile-config.log') + '\n')
        yaml = ('server:\n  address: 0.0.0.0\n  port: ' + str(self.unused_port) + '\nlogging:\n  file:\n    name: ' + str(self.private / 'hostile-yaml.log') + '\n')
        hostile_files = {'application.properties': props, 'application.yml': yaml,
                         'config/application.properties': props, 'config/nested/application.yaml': yaml}
        for name, body in hostile_files.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        self.hostile = {key: self.sentinel for key in ['JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS',
                        'SPRING_APPLICATION_JSON', 'SPRING_CONFIG_LOCATION', 'SPRING_CONFIG_IMPORT',
                        'SPRING_DATASOURCE_URL', 'SPRING_FLYWAY_URL', 'MYSQL_ROOT_PASSWORD', 'UNRELATED_SECRET']}
        return {'copy_root': str(self.root), 'byte_identical_artifacts': True,
                'credentials': 'task-created independent keys/fixtures; original files untouched',
                'hostile_config_files': list(hostile_files), 'database_health': 'healthy', 'volume': self.args.volume}

    @staticmethod
    def snapshot_sql():
        sql = 'SELECT version,script,checksum,success FROM flyway_schema_history ORDER BY installed_rank;'
        for table in ['users', 'roles', 'policies', 'resources', 'user_roles', 'role_policies', 'policy_resources', 'access_audit']:
            sql += "SELECT '" + table + "',COUNT(*) FROM " + table + ';'
        return sql

    def load_secrets(self):
        self.tokens = {p.stem: p.read_text().strip() for p in self.credentials.glob('*.jwt')}
        self.secrets.extend(self.tokens.values())
        for p in self.credentials.glob('*private.pem'):
            self.secrets.extend(line for line in p.read_text().splitlines() if line and not line.startswith('-----'))

    def refresh(self):
        keys_before = {p.name: sha(p) for p in self.credentials.glob('*.pem')}
        self.command(['java', str(BE / 'tools/DemoCredentials.java'), str(self.credentials), '--refresh-fixtures'])
        assert keys_before == {p.name: sha(p) for p in self.credentials.glob('*.pem')}, 'Task fixture refresh changed keys'
        self.load_secrets()

    @staticmethod
    def write_env(path, values):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Quoted data, not shell syntax; existing generated values must be representable.
        assert all("'" not in value and '\n' not in value and '\r' not in value for value in values.values()), 'Fixture cannot be represented by chosen quote grammar'
        path.write_text('\n'.join(key + "='" + value + "'" for key, value in values.items()) + '\n')
        path.chmod(0o600)
        return path

    def start(self, name, target, envfile=None, inherited=None, cwd=None):
        args = [sys.executable, str(self.root / 'scripts/run_server.py'), target]
        if envfile is not None:
            args += ['--env-file', str(envfile)]
        path = self.private / (name + '.log')
        log = path.open('w')
        env = {**self.parent, **getattr(self, 'hostile', {}), **(inherited or {})}
        proc = subprocess.Popen(args, env=env, cwd=cwd or self.root.parent,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        self.processes.append(proc)
        self.logs[name] = path
        self.commands.append({'argv': args, 'cwd': str(cwd or self.root.parent), 'pid': proc.pid})
        return proc

    def ready(self, proc, port):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            assert proc.poll() is None, 'Launcher JVM exited before readiness'
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    return
            except OSError:
                time.sleep(.15)
        raise AssertionError('Launcher readiness timed out')

    def stop(self, proc):
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGINT)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
                raise AssertionError('Owned process ignored Ctrl+C; forced cleanup required')
        return proc.returncode

    def negative_placeholder(self):
        # A launch observer executes the actual approved local Java if reached; no fake JVM result.
        java = str(Path(self.parent['JAVA_HOME']) / 'bin/java') if self.parent.get('JAVA_HOME') else shutil.which('java', path=self.parent.get('PATH'))
        assert java and Path(java).is_file(), 'Actual Java not available'
        wrapper = self.private / 'java invocation observer/bin'
        wrapper.mkdir(parents=True)
        marker = self.private / 'java-was-invoked'
        script = ('#!' + sys.executable + '\nimport os,sys\nfrom pathlib import Path\n' +
                  'Path(' + repr(str(marker)) + ').write_text("invoked")\n' +
                  'os.execve(' + repr(java) + ',[' + repr(java) + ']+sys.argv[1:],os.environ)\n')
        (wrapper / 'java').write_text(script); (wrapper / 'java').chmod(0o700)
        variants = [({'MYSQL_PASSWORD': '${LAUNCHER_SENTINEL}'}, {}),
                    ({'MYSQL_PASSWORD': '${SHADOWED_SENTINEL}'}, {'MYSQL_PASSWORD': self.settings['MYSQL_PASSWORD']}),
                    ({}, {'MYSQL_PASSWORD': '${SHELL_SENTINEL:default}'})]
        exits = []
        for index, (file_override, shell_override) in enumerate(variants):
            file = self.write_env(self.private / ('negative-placeholder-' + str(index) + '.env'), {**self.api_env, **file_override})
            proc = self.start('negative-placeholder-' + str(index), 'api', file,
                              {'JAVA_HOME': str(wrapper.parent), **shell_override})
            try:
                code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.stop(proc)
                raise AssertionError('Placeholder input started a persistent process')
            assert code != 0, 'Placeholder input accepted by real CLI'
            assert not marker.exists(), 'Rejected placeholder reached real Java'
            exits.append(code)
        negative_files = [self.private / 'missing.env',
                          self.write_env(self.private / 'missing-key.env', {**self.api_env, 'DEMO_PUBLIC_KEY': str(self.private / 'absent.pem')}),
                          self.write_env(self.private / 'invalid.env', {**self.api_env, 'SERVER_PORT': 'not-a-port'})]
        for index, file in enumerate(negative_files):
            proc = self.start('negative-resource-' + str(index), 'api', file, {'JAVA_HOME': str(wrapper.parent)})
            code = proc.wait(timeout=10)
            assert code != 0 and not marker.exists(), 'Invalid/missing resource reached actual Java'
            exits.append(code)
        jar = self.root / 'api-server/build/libs/api-server-0.1.0.jar'
        held = jar.with_suffix('.held')
        jar.rename(held)
        try:
            proc = self.start('negative-missing-jar', 'api', self.api_file, {'JAVA_HOME': str(wrapper.parent)})
            code = proc.wait(timeout=10)
            assert code != 0 and not marker.exists(), 'Missing JAR reached actual Java'
            exits.append(code)
        finally:
            held.rename(jar)
        return {'real_cli_exit_codes': exits, 'actual_java_invocation_marker': False,
                'variants': ['file', 'shadowed-file', 'selected-shell', 'missing-env', 'missing-key', 'invalid-port', 'missing-jar'], 'JVM_started': False}

    def smoke(self):
        self.api = self.start('api', 'api')  # Default .env.api, invoked from copied repo root.
        self.ready(self.api, self.api_port)
        self.mcp = self.start('mcp', 'mcp', self.mcp_file.relative_to(self.root), cwd=self.root)
        self.ready(self.mcp, self.mcp_port)
        self.refresh()
        init = rpc(self.mcp_port, self.tokens['mcp-alice'], 'initialize',
                   {'protocolVersion': PROTOCOL, 'capabilities': {}, 'clientInfo': {'name': 'launcher-verifier', 'version': '1'}})
        assert init['protocolVersion'] == PROTOCOL
        assert request(self.mcp_port, '/mcp', self.tokens['mcp-alice'], {'jsonrpc': '2.0', 'method': 'notifications/initialized'})[0] == 202
        tools = rpc(self.mcp_port, self.tokens['mcp-alice'], 'tools/list')['tools']
        assert len(tools) == 12 and any(t['name'] == 'get_my_access' for t in tools)
        observations = []
        for user, expected in [('alice', ['R100', 'R200', 'R300']), ('bob', ['R100', 'R400']), ('carol', [])]:
            result = rpc(self.mcp_port, self.tokens['mcp-' + user], 'tools/call', {'name': 'get_my_access', 'arguments': {}})
            assert result['isError'] is False
            data = result['structuredContent']
            assert data == json.loads(result['content'][0]['text'])
            assert data['user']['id'] == user and [item['id'] for item in data['resources']] == expected
            status, raw = request(self.api_port, '/api/me', self.tokens['api-valid' if user == 'alice' else 'api-' + user])
            assert status == 200 and json.loads(raw) == data, 'Direct API differs from real MCP result'
            observations.append({'user': user, 'resources': expected, 'direct_api_equals_mcp': True})
        self.observations['expected_access'] = observations
        return {'protocol_sequence': ['initialize', 'notifications/initialized', 'tools/list', 'tools/call'],
                'tool_count': len(tools), 'expected_access': observations}

    def config_identity(self):
        baseline = set(self.baseline.splitlines())
        rows = [line.split('\t') for line in self.sql(
            "SELECT PROCESSLIST_ID,PROCESSLIST_DB,PROCESSLIST_USER FROM performance_schema.threads WHERE PROCESSLIST_USER='access_e2e' ORDER BY PROCESSLIST_ID;").splitlines()]
        new = [row for row in rows if row[0] not in baseline]
        assert new and all(row[1:] == ['access_e2e_v2', 'access_e2e'] for row in new), 'Actual API DB connections do not match isolation contract'
        listeners = []
        for proc, port in [(self.api, self.api_port), (self.mcp, self.mcp_port)]:
            raw = self.command(['lsof', '-nP', '-a', '-p', str(proc.pid), '-iTCP', '-sTCP:LISTEN', '-Fn'])
            names = [line[1:] for line in raw.splitlines() if line.startswith('n')]
            assert names and all(name == '127.0.0.1:' + str(port) for name in names), 'Real listener escaped loopback or selected port'
            argv = self.command(['ps', '-p', str(proc.pid), '-o', 'args='])
            assert 'java' in argv and '--spring.config.location=classpath:' in argv
            assert all(secret not in argv for secret in self.secrets), 'Secret in process arguments'
            listeners.append({'pid': proc.pid, 'loopback_port': port, 'argv_secret_matches': 0})
        sockets = self.command(['lsof', '-nP', '-a', '-p', str(self.api.pid), '-iTCP', '-sTCP:ESTABLISHED', '-Fn'])
        assert any('->127.0.0.1:3308' in line for line in sockets.splitlines()), 'API socket not connected to isolated DB port'
        assert not (self.private / 'hostile-config.log').exists() and not (self.private / 'hostile-yaml.log').exists()
        assert all(self.sentinel not in path.read_text(errors='replace') for path in self.logs.values()), 'Hostile config/env sentinel reached output'
        return {'actual_new_database_connections': new, 'api_database_socket': '127.0.0.1:3308',
                'listeners': listeners, 'hostile_external_log_files_created': False,
                'hostile_config_sentinel_in_logs': False, 'config_fixture_layout': str(self.root)}

    def authentication(self):
        self.refresh()
        checked = []
        for target, port, path in [('api', self.api_port, '/api/me'), ('mcp', self.mcp_port, '/mcp')]:
            for fixture in [None, 'malformed', target + '-expired', target + '-forged', target + '-wrong-issuer', target + '-wrong-audience']:
                token = None if fixture is None else 'malformed.token.sentinel' if fixture == 'malformed' else self.tokens[fixture]
                status, _ = request(port, path, token, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'} if target == 'mcp' else None)
                assert status == 401, 'Invalid auth did not receive 401'
                checked.append({'target': target, 'fixture': fixture or 'unauthenticated', 'status': status})
        return {'rejections': checked}

    def lifecycle_upstream(self):
        api_exit = self.stop(self.api)
        assert api_exit in (130, -signal.SIGINT), 'API Ctrl+C exit is not meaningful'
        self.refresh()
        result = rpc(self.mcp_port, self.tokens['mcp-alice'], 'tools/call', {'name': 'get_my_access', 'arguments': {}})
        assert result['isError'] and result['content'] == [{'type': 'text', 'text': 'UPSTREAM_UNAVAILABLE'}]
        mcp_exit = self.stop(self.mcp)
        assert mcp_exit in (130, -signal.SIGINT), 'MCP Ctrl+C exit is not meaningful'
        return {'SIGINT_api_exit': api_exit, 'SIGINT_mcp_exit': mcp_exit,
                'stopped_api_tool_result': 'UPSTREAM_UNAVAILABLE', 'no_false_success': True}

    def startup_failures(self):
        cases = [('unavailable-db', {'DB_PORT': str(self.unused_port)}),
                 ('incorrect-db-password', {'MYSQL_PASSWORD': 'independent-bad-password-sentinel-3f7a'})]
        self.secrets.append(cases[1][1]['MYSQL_PASSWORD'])
        observed = []
        for name, override in cases:
            path = self.write_env(self.private / (name + '.env'), {**self.api_env, **override})
            proc = self.start(name, 'api', path)
            try:
                code = proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.stop(proc)
                raise AssertionError('Invalid DB configuration did not fail startup')
            assert code != 0, 'Invalid DB unexpectedly started'
            observed.append({'case': name, 'exit_code': code})
        return {'startup_failures': observed}

    def finish(self):
        errors = []
        for proc in self.processes:
            try:
                self.stop(proc)
            except Exception:
                errors.append('owned process required forced cleanup')
        for name, path in self.logs.items():
            text = path.read_text(errors='replace')
            leak = any(value and value in text for value in self.secrets) or bool(re.search(r'eyJ[\w-]+\.[\w-]+\.[\w-]+|-----BEGIN (?:RSA |EC )?PRIVATE KEY-----', text))
            if leak:
                errors.append('secret detected in ' + name + ' log')
            for secret in sorted(set(self.secrets), key=len, reverse=True):
                if secret:
                    text = text.replace(secret, '[REDACTED]')
            text = re.sub(r'eyJ[\w-]+\.[\w-]+\.[\w-]+', '[REDACTED JWT]', text)
            text = re.sub(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----.*?-----END (?:RSA |EC )?PRIVATE KEY-----', '[REDACTED KEY]', text, flags=re.S)
            (self.output / (name + '.log')).write_text(text)
        assert all(sha(path) == previous for path, previous in self.preserved.items()), 'Original credentials/env were modified'
        if hasattr(self, 'db_before'):
            assert self.sql(self.snapshot_sql()) == self.db_before, 'Read-only launcher tests changed DB history/counts'
        for relative, expected in self.source_hashes.items():
            assert sha(REPO / relative) == expected, 'Source or artifact changed during verification'
        evidence = {'owned_pids': [proc.pid for proc in self.processes],
                    'remaining_owned_processes': sum(proc.poll() is None for proc in self.processes),
                    'preserved_original_files': len(self.preserved), 'original_files_unchanged': True,
                    'database_history_counts_unchanged': hasattr(self, 'db_before'), 'volume_preserved': self.args.volume,
                    'logs_secret_matches': len(errors), 'cleanup_errors': errors}
        if self.tmp:
            self.tmp.cleanup()
        assert not errors, 'Cleanup/log verification failed; sanitized evidence retained'
        return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--handoff', type=Path, required=True)
    parser.add_argument('--authorization', required=True)
    parser.add_argument('--compose-env', type=Path, default=BE / '.dev/tester-v2.env')
    parser.add_argument('--project', default='mcp-access-e2e-v2')
    parser.add_argument('--volume', default='mcp-access-e2e-v2_access-data')
    parser.add_argument('--previous-run')
    args = parser.parse_args()
    args.compose_env = args.compose_env.resolve()
    output = BE / 'test-results' / ('launcher-runtime-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    ctx = Runtime(args, output)
    report = {'run_id': output.name, 'previous_run': args.previous_run, 'started_at': now(),
              'profile': 'local', 'data_source': 'real launcher JVMs and isolated Docker MySQL',
              'python_version': sys.version.split()[0], 'results': [], 'limitations': [
                  'Launcher-specific smoke and failure checks only; previous core/UI reports are separate.',
                  'Actual DB identity uses new non-root Performance Schema sessions plus API PID socket to isolated port; no datasource override is injected.']}
    stopped = False
    checks = [('EL-GATE', ctx.gate), ('EL-PREPARE', ctx.prepare), ('EL-P2-NO-JVM', ctx.negative_placeholder),
              ('EL-REAL-SMOKE', ctx.smoke), ('EL-P1-CONFIG-DB', ctx.config_identity),
              ('EL-AUTH', ctx.authentication), ('EL-SIGINT-UPSTREAM', ctx.lifecycle_upstream),
              ('EL-STARTUP-FAILURES', ctx.startup_failures)]
    for case, function in checks:
        row = {'id': case, 'started_at': now()}
        if stopped:
            row.update(status='BLOCKED', actual='Prerequisite check did not pass')
        else:
            try:
                row.update(status='PASS', actual=function())
            except Exception as error:
                # Fixed safe assertion messages; never serialize runtime exceptions with values.
                row.update(status='BLOCKED' if isinstance(error, Blocked) else 'FAIL',
                           actual=str(error) if isinstance(error, (AssertionError, Blocked)) else type(error).__name__)
                stopped = True
        row['finished_at'] = now()
        report['results'].append(row)
        print(row['status'], case, flush=True)
    try:
        cleanup = ctx.finish()
        report['results'].append({'id': 'EL-CLEANUP-LOGS', 'status': 'PASS', 'actual': cleanup})
    except Exception as error:
        report['results'].append({'id': 'EL-CLEANUP-LOGS', 'status': 'FAIL', 'actual': type(error).__name__})
    report.update(source_hashes=ctx.source_hashes, commands=ctx.commands, finished_at=now())
    report['counts'] = {status: sum(row['status'] == status for row in report['results']) for status in ['PASS', 'FAIL', 'BLOCKED', 'SKIPPED']}
    report['status'] = 'PASS' if not report['counts']['FAIL'] and not report['counts']['BLOCKED'] else 'FAIL'
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    for secret in sorted(set(ctx.secrets), key=len, reverse=True):
        if secret:
            encoded = encoded.replace(secret, '[REDACTED]')
    (output / 'report.json').write_text(encoded + '\n')
    (output / 'summary.md').write_text('# Actual launcher verification\n\n' + report['status'] + '\n\n' +
        '\n'.join('- ' + row['id'] + ': ' + row['status'] for row in report['results']) + '\n')
    print(output / 'report.json')
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
