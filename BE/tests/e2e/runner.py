#!/usr/bin/env python3
"""Registered scenario runner. No scenario-supplied code or shell commands."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from management import V2_HANDLERS, Management
from errors import Blocked
from runtime_safety import safe_environment, pinned_api_arguments

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / 'tests/scenarios'
METHODS = {
    'protocol_users': 'test_01_initialize_tools_and_three_users',
    'delegation': 'test_02_delegation_and_expiry',
    'jwt_identity': 'test_03_jwt_validation_and_identity_binding',
    'scope': 'test_04_scope_is_not_domain_permission',
    'provenance': 'test_05_provenance_and_denial_equivalence',
    'arguments': 'test_06_input_and_concepts',
    'origin_static': 'test_07_origin_host_and_static_policy',
    'isolation': 'test_08_context_isolation',
    'protocol_limits': 'test_09_protocol_encoded_paths_and_limits',
    'upstream_faults': 'test_10_downstream_failures_and_limits',
    'disconnect': 'test_11_disconnect_and_overlap',
    'packaging': 'test_12_packaged_allowlist_and_source_layout',
    'db_unavailable': 'test_13_local_database_failure_has_no_fixture_fallback',
    'logs': 'test_99_no_secrets_in_logs_after_shutdown',
}
HANDLERS = set(METHODS) | {'mysql', 'restart', 'browser', 'exact_identity', 'independent_id'} | set(V2_HANDLERS)

def parse_snapshot(raw):
    counts, history = {}, []
    tables = {'users', 'roles', 'policies', 'resources', 'user_roles', 'role_policies', 'policy_resources'}
    for line in raw.splitlines():
        fields = line.split('\t')
        if len(fields) == 2 and fields[0] in tables:
            if fields[0] in counts:
                raise AssertionError('Duplicate table count: ' + fields[0])
            counts[fields[0]] = int(fields[1])
        elif len(fields) == 4 and fields[0].isdigit():
            if fields[3] != '1':
                raise AssertionError('Unsuccessful Flyway migration')
            history.append(fields)
        else:
            raise AssertionError('Unexpected MySQL snapshot row')
    if set(counts) != tables:
        raise AssertionError('Missing exact table count rows')
    return {'counts': counts, 'history': history}

def load_scenarios(directory=SCENARIOS):
    cases = []
    seen = set()
    for path in sorted(Path(directory).glob('*.json')):
        data = json.loads(path.read_text())
        if not isinstance(data, list) or not data:
            raise ValueError(f'{path.name}: expected nonempty array')
        for case in data:
            required = {'id', 'purpose', 'category', 'profiles', 'preconditions', 'fixtures',
                        'targets', 'steps', 'expected', 'handler', 'contract'}
            if not isinstance(case, dict) or set(case) != required:
                raise ValueError(f'{path.name}: invalid scenario fields')
            for field in ['id', 'purpose', 'category', 'handler']:
                if not isinstance(case[field], str) or not case[field].strip():
                    raise ValueError(f'{path.name}: invalid {field}')
            if not re.fullmatch(r'[A-Z][A-Z0-9-]+', case['id']) or case['id'] in seen:
                raise ValueError(f'{path.name}: invalid/duplicate ID {case["id"]}')
            if case['handler'] not in HANDLERS:
                raise ValueError(f'{case["id"]}: unregistered handler')
            if case['contract'] not in {'v1', 'v2'}:
                raise ValueError('Unknown scenario contract')
            for field in ['profiles', 'preconditions', 'fixtures', 'targets', 'steps']:
                if not isinstance(case[field], list) or not case[field] or not all(isinstance(v, str) and v.strip() for v in case[field]):
                    raise ValueError(f'{case["id"]}: invalid {field}')
            if not set(case['profiles']) <= {'local', 'fixture'}:
                raise ValueError(f'{case["id"]}: unknown profile')
            if not isinstance(case['expected'], dict) or set(case['expected']) != {'http', 'rpc', 'domain', 'error'}:
                raise ValueError(f'{case["id"]}: invalid expected shape')
            if not all(isinstance(v, str) and v.strip() for v in case['expected'].values()):
                raise ValueError(f'{case["id"]}: invalid expected value')
            if re.search(r'eyJ[\w-]+\.[\w-]+\.[\w-]+|PRIVATE KEY', json.dumps(case)):
                raise ValueError(f'{case["id"]}: credential material prohibited')
            seen.add(case['id']); cases.append(case)
    if not cases:
        raise ValueError('No scenarios found')
    return cases

def select(cases, ids=None, category=None):
    if ids:
        wanted = ids.split(',')
        if len(set(wanted)) != len(wanted) or set(wanted) - {c['id'] for c in cases}:
            raise ValueError('Duplicate or unknown selected ID')
        return [c for c in cases if c['id'] in wanted]
    if category:
        selected = [c for c in cases if c['category'] == category]
        if not selected:
            raise ValueError('Unknown category')
        return selected
    return cases

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def sanitize(value, secrets=()):
    text = str(value)
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', '[REDACTED KEY]', text, flags=re.S)
    text = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED JWT]', text)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*)([^\r\n]+)', r'\1[REDACTED]', text)
    return text

class Context:
    def __init__(self, args, output):
        self.args, self.output = args, output
        self.manifest = None
        self.module = self.runtime = None
        self.tmp = tempfile.TemporaryDirectory(prefix='scenario-private-')
        self.commands, self.secrets, self.builds = [], [], {}
        self.db_ready = False
        self.startup_error = None
        self.runtime_env = safe_environment(os.environ)
        self.settings = {}
        self.database_verified = False
        self.connection_marker = 'e2e_' + uuid.uuid4().hex[:16]

    def gate(self):
        if self.manifest is not None:
            return
        if not self.args.handoff:
            raise Blocked('MANAGER TEST_READY handoff not supplied; no runtime touched')
        data = json.loads(Path(self.args.handoff).read_text())
        required = {'status', 'runtime_owner', 'builds_frozen', 'profile', 'artifacts', 'compose_project', 'volume', 'fixture_path', 'previous_processes'}
        if not required <= set(data) or data['status'] != 'TEST_READY' or data['runtime_owner'] != 'TESTER' or data['builds_frozen'] is not True:
            raise Blocked('Incomplete explicit MANAGER handoff')
        if data['profile'] != self.args.profile:
            raise Blocked('Handoff profile mismatch')
        if data.get('contract', 'v1') != getattr(self.args, 'contract', 'v1'):
            raise Blocked('Handoff and scenario contract differ')
        if data.get('contract') == 'v2':
            if data['compose_project'] == 'mcp-access-demo' or data['volume'] == 'mcp-access-demo_access-data':
                raise Blocked('v2 mutations require isolated TESTER project and volume')
            if not data.get('isolated_mutations') or not data.get('compose_env_file'):
                raise Blocked('Missing explicit isolated mutation environment')
            for folder in [ROOT, ROOT / 'config', *list((ROOT / 'config').glob('*'))]:
                if folder.is_dir() and any(p.is_file() for suffix in ['.properties', '.yml', '.yaml'] for p in folder.glob('application*' + suffix)):
                    raise Blocked('External Spring application config in runtime cwd is not allowed')
        for name in ['api-server', 'mcp-server']:
            artifact = data['artifacts'][name]
            path = ROOT / name / 'build/libs' / f'{name}-0.1.0.jar'
            if Path(artifact['path']).resolve() != path.resolve() or sha(path) != artifact['sha256']:
                raise Blocked('Handed-off artifact path/hash mismatch: ' + name)
            self.builds[name] = artifact['sha256']
        self.builds['verify.py'] = sha(ROOT / 'scripts/verify.py')
        self.manifest = data

    def command(self, argv, *, input=None, timeout=120, env=None):
        self.commands.append({'argv': argv, 'cwd': str(ROOT)})
        try:
            proc = subprocess.run(argv, cwd=ROOT, env=self.runtime_env if env is None else env, input=input, text=True, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Blocked(type(error).__name__ + ': ' + argv[0]) from None
        self.commands[-1]['exit_code'] = proc.returncode
        if proc.returncode:
            raise Blocked(f'{argv[0]} exited {proc.returncode}: ' + sanitize(proc.stderr[-1500:], self.secrets))
        return proc.stdout

    def compose(self, *args, **kwargs):
        return self.command(['docker', 'compose', '--env-file', self.env_file(), '-p', self.manifest['compose_project'], *args], **kwargs)

    def env_file(self):
        return str(Path(self.manifest.get('compose_env_file', ROOT / '.env')).resolve())

    def mysql(self):
        self.gate()
        if self.args.profile != 'local':
            raise Blocked('MySQL scenario requires local profile')
        if self.db_ready:
            return {'database': 'real MySQL; compose health verified'}
        settings = {}
        if not Path(self.env_file()).is_file():
            raise Blocked('Handed-off env file missing; prepare authorized isolated settings')
        for line in Path(self.env_file()).read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1); settings[key.strip()] = value.strip()
        for key in ['MYSQL_DATABASE', 'MYSQL_USER', 'MYSQL_PASSWORD', 'MYSQL_ROOT_PASSWORD']:
            if not settings.get(key):
                raise Blocked('Missing DB setting: ' + key)
        if settings['MYSQL_USER'] == 'root':
            raise AssertionError('Application user must not be root')
        if self.manifest.get('contract') == 'v2' and settings.get('DB_PORT') != '3308':
            raise Blocked('v2 isolated database must use handed-off loopback port 3308')
        self.secrets.extend([settings['MYSQL_PASSWORD'], settings['MYSQL_ROOT_PASSWORD']])
        self.settings = settings
        if self.manifest.get('contract') == 'v2':
            self.runtime_env = safe_environment(os.environ, settings)
        else:
            self.runtime_env.update(settings)
        version = self.command(['docker', 'info', '--format', '{{.ServerVersion}}']).strip()
        self.compose('config', '--quiet')
        self.compose('up', '--build', '--wait', 'access-db', timeout=600)
        containers = self.compose('ps', '--format', 'json', 'access-db')
        entries = json.loads(containers) if containers.lstrip().startswith('[') else [json.loads(line) for line in containers.splitlines() if line.strip()]
        if not entries or any(e.get('Health') != 'healthy' for e in entries):
            raise Blocked('MySQL container not healthy')
        # Inspect only volume mounts, never container environment/passwords.
        mounts = json.loads(self.command(['docker', 'inspect', '--format', '{{json .Mounts}}', entries[0]['ID']]))
        if not any(m.get('Name') == self.manifest['volume'] and m.get('Destination') == '/var/lib/mysql' for m in mounts):
            raise Blocked('MySQL volume differs from handoff')
        self.db_ready = True
        return {'docker_version': version, 'health': 'healthy', 'volume': self.manifest['volume']}

    def database(self, sql):
        self.mysql()
        # Fixed command; SQL comes only from registered test code, never scenario files.
        return self.compose('exec', '-T', 'access-db', 'sh', '-c',
            'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --host=127.0.0.1 --user="$MYSQL_USER" --database="$MYSQL_DATABASE" --batch --skip-column-names', input=sql)

    def database_observer(self, sql):
        self.mysql()
        # Container-local root reads Performance Schema only; no grants/config changes.
        return self.compose('exec', '-T', 'access-db', 'sh', '-c',
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --user=root --database="$MYSQL_DATABASE" --batch --skip-column-names', input=sql)

    def verify_api_database(self):
        if self.manifest.get('contract') != 'v2':
            return
        # Separate pinned Flyway connections close after migration; Hikari is lazy.
        # A read-only request opens the application pool before observing its marker.
        for port in [self.runtime.api, self.runtime.second_api]:
            status, _, _ = self.module.request(port, '/api/me', self.runtime.tokens['api-valid'])
            if status != 200:
                raise Blocked('Read-only API warmup failed before database verification')
        sql = "SELECT a.PROCESSLIST_ID,t.PROCESSLIST_DB,t.PROCESSLIST_USER FROM performance_schema.session_connect_attrs a JOIN performance_schema.threads t ON t.PROCESSLIST_ID=a.PROCESSLIST_ID WHERE a.ATTR_NAME='testerRun' AND a.ATTR_VALUE='" + self.connection_marker + "' ORDER BY a.PROCESSLIST_ID;"
        rows = [row.split('\t') for row in self.database_observer(sql).splitlines()]
        if not rows or any(len(row) != 3 or row[1] != self.settings['MYSQL_DATABASE'] or row[2] != self.settings['MYSQL_USER'] for row in rows):
            raise Blocked('Actual API connections to isolated MySQL were not observed; mutations prohibited')
        self.database_verified = True
        self.database_identity = {'marker': self.connection_marker, 'host': '127.0.0.1', 'port': 3308,
                                  'database': self.settings['MYSQL_DATABASE'], 'connections': rows}

    def setup(self):
        self.gate()
        if self.startup_error:
            raise Blocked(self.startup_error)
        if self.runtime:
            return
        if self.args.profile == 'local':
            self.mysql()
        # Imported BE test helpers also inherit only this safe process-local environment.
        self.original_process_env = dict(os.environ)
        os.environ.clear(); os.environ.update(self.runtime_env)
        spec = importlib.util.spec_from_file_location('scenario_verify', ROOT / 'scripts/verify.py')
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        mod.REPORT = Path(self.tmp.name) / 'logs'
        mod.API_PROFILE = self.args.profile
        self.module = mod
        ctx = self
        def start(cls, module, name, *args):
            port = mod.free_port()
            log = (mod.REPORT / f'{name}.log').open('w')
            cls.addClassCleanup(log.close)
            argv = ['java', '-jar', str(ROOT/module/'build/libs'/f'{module}-0.1.0.jar'),
                    f'--server.port={port}', '--spring.main.banner-mode=off', *args]
            if ctx.manifest.get('contract') == 'v2':
                if module == 'api-server':
                    argv.extend(pinned_api_arguments(ctx.settings, ctx.connection_marker))
                else:
                    argv.append('--spring.config.location=classpath:/application.properties')
            java_env = {key: value for key, value in ctx.runtime_env.items() if key != 'MYSQL_ROOT_PASSWORD'}
            proc = subprocess.Popen(argv, cwd=ROOT, env={**java_env, 'DEMO_DIR': str(cls.credentials)}, stdout=log, stderr=subprocess.STDOUT)
            ctx.commands.append({'argv': argv, 'cwd': str(ROOT), 'pid': proc.pid, 'fixture_directory': '<private-temporary-fixtures>'})
            def stop():
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(timeout=10)
                    except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
            cls.addClassCleanup(stop)
            cls.processes.append((stop, log))
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if proc.poll() is not None: raise RuntimeError(f'{name} exited {proc.returncode}; inspect sanitized {name}.log')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2): return port
                except OSError: time.sleep(.1)
            raise RuntimeError(f'{name} did not bind within 40 seconds')
        runtime = type('ScenarioIntegration', (mod.Integration,), {'start': classmethod(start)})
        self.runtime = runtime
        self.commands.append({'argv': ['java', 'tools/DemoCredentials.java', '<private-temporary-fixtures>', '--fixtures'], 'cwd': str(ROOT)})
        try:
            runtime.setUpClass()
            self.secrets.extend(runtime.tokens.values())
            self.verify_api_database()
        except Exception as error:
            self.startup_error = 'Runtime setup failed: ' + sanitize(str(error), self.secrets)
            raise Blocked(self.startup_error) from None

    def integration(self, handler):
        self.setup()
        self.refresh_fixtures()
        if handler == 'logs':
            # The inherited fault event starts unset; a subset may never call it.
            with self.module.FaultApi.lock:
                if self.module.FaultApi.inflight == 0:
                    self.module.FaultApi.idle.set()
        method = METHODS[handler]
        case = self.runtime(method)
        # Invoke directly: unittest subTest raises normally outside a TestResult,
        # so a failed nested assertion cannot be silently swallowed.
        getattr(case, method)()
        return {'assertions': 'all registered assertions completed', 'method': method,
                'api_port': self.runtime.api, 'mcp_port': self.runtime.mcp}

    def restart(self):
        self.setup()
        sql = "SELECT version,script,checksum,success FROM flyway_schema_history ORDER BY installed_rank;\n"
        for table in ['users', 'roles', 'policies', 'resources', 'user_roles', 'role_policies', 'policy_resources']:
            sql += f"SELECT '{table}',COUNT(*) FROM {table};\n"
        before = self.database(sql)
        expected = {'users': 4, 'roles': 3, 'policies': 4, 'resources': 4, 'user_roles': 3, 'role_policies': 6, 'policy_resources': 6}
        parsed = parse_snapshot(before)
        assert parsed['counts'] == expected, 'Unexpected exact seed counts: ' + str(parsed['counts'])
        scripts = [row[1] for row in parsed['history']]
        assert 'V1__access_schema.sql' in scripts and 'V2__local_demo_seed.sql' in scripts and len(scripts) == 3, 'Missing migrations'
        port = self.runtime.start('api-server', 'api-same-volume-restart', '--spring.profiles.active=local')
        # HTTP readiness confirms startup and migrations completed; no stale short-lived API fixture needed.
        status, _, _ = self.module.request(port, '/api/me')
        assert status == 401, 'Restarted API did not reach authentication boundary'
        after = self.database(sql)
        assert parse_snapshot(after) == parsed, 'Parsed database state changed after second startup'
        assert before == after, 'Migration/checksum/entity/grant counts changed after second startup'
        (self.output / 'mysql-snapshot.txt').write_text(before)
        return {'same_volume': self.manifest['volume'], 'counts': expected, 'history_unchanged': True, 'evidence': 'mysql-snapshot.txt'}

    def exact_identity(self):
        self.setup()
        # Actual database string comparisons, independent of JWT's original_iss rejection.
        pairs = [('https://demo-issuer.invalid', 'alice', 1), ('https://DEMO-ISSUER.invalid', 'alice', 0),
                 ('https://démo-issuer.invalid', 'alice', 0), ('https://demo-issuer.invalid ', 'alice', 0),
                 ('https://demo-issuer.invalid', 'ALICE', 0), ('https://demo-issuer.invalid', 'álice', 0),
                 ('https://demo-issuer.invalid', 'alice ', 0)]
        for issuer, subject, expected in pairs:
            value = self.database("SELECT COUNT(*) FROM users WHERE issuer=CONVERT(0x" + issuer.encode().hex() +
                " USING utf8mb4) AND subject=CONVERT(0x" + subject.encode().hex() + " USING utf8mb4);\n")
            assert value.strip() == str(expected), f'Exact identity mismatch: issuer={issuer!r}, subject={subject!r}'
        return {'exact_issuer_subject_comparisons': len(pairs), 'store': 'real MySQL'}

    def independent_id(self):
        self.setup()
        fixture = 'mcp-local-id-demo'
        if fixture not in self.runtime.tokens:
            raise Blocked('RB1 fixture mcp-local-id-demo missing from handed-off generator')
        case = self.runtime('test_01_initialize_tools_and_three_users')
        data = case.result(case.call(token=self.runtime.tokens[fixture]))
        assert data['user']['id'] == 'U900', 'DB entity ID must be independent of verified JWT subject local-id-demo'
        assert all(data[key] == [] for key in ['roles', 'policies', 'resources']), 'Independent-ID user must not receive foreign grants'
        assert data['user']['roleIds'] == [], 'Independent-ID user has unexpected roles'
        return {'fixture': fixture, 'verified_subject': 'local-id-demo', 'returned_user_id': data['user']['id'], 'resources': data['resources']}

    def refresh_fixtures(self):
        if self.manifest.get('contract') != 'v2':
            return
        self.command(['java', str(ROOT / 'tools/DemoCredentials.java'), str(self.runtime.credentials), '--refresh-fixtures'])
        self.runtime.tokens = {p.stem: p.read_text().strip() for p in self.runtime.credentials.glob('*.jwt')}
        self.secrets.extend(self.runtime.tokens.values())
        now = time.time()
        for name in ['api-valid', 'mcp-near-expiry']:
            claim = self.module.claims(self.runtime.tokens[name])
            assert claim['exp'] > now + 20, 'Time-sensitive fixture is not fresh: ' + name
        assert self.module.claims(self.runtime.tokens['mcp-future'])['nbf'] > now + 10, 'Future fixture no longer future'

    def browser(self, mode='read'):
        self.setup()
        module_path = os.environ.get('E2E_PLAYWRIGHT_MODULE')
        if not module_path or not Path(module_path).exists():
            raise Blocked('E2E_PLAYWRIGHT_MODULE must point to an existing local Playwright module; no install performed')
        env = {**safe_environment(os.environ), 'E2E_FIXTURE_DIR': str(self.runtime.credentials),
               'E2E_BASE_URL': f'http://127.0.0.1:{self.runtime.mcp}', 'E2E_BROWSER_MODE': mode,
               'E2E_UI_SELECTORS': json.dumps(self.manifest.get('ui_selectors', {})),
               'E2E_OUTPUT_DIR': str(self.output)}
        script = 'browser_discovery.mjs' if mode.startswith('discovery-') else 'browser.mjs' if mode == 'read' else 'browser_management.mjs'
        output = self.command(['node', str(ROOT / 'tests/e2e' / script)], env=env, timeout=180)
        data = json.loads(output)
        (self.output / ('browser-' + mode + '.json')).write_text(sanitize(json.dumps(data, ensure_ascii=False, indent=2), self.secrets))
        if data.get('status') == 'BLOCKED':
            raise Blocked(data['reason'])
        assert data.get('status') == 'PASS', 'Browser assertions failed: ' + json.dumps(data)
        return data

    def execute(self, handler):
        if handler in V2_HANDLERS:
            self.setup()
            self.refresh_fixtures()
            return Management(self).execute(handler)
        if handler in METHODS:
            return self.integration(handler)
        return getattr(self, handler)()

    def close(self):
        if self.runtime:
            self.secrets.extend(getattr(self.runtime, 'tokens', {}).values())
            self.runtime.doClassCleanups()
        if self.module and self.module.REPORT.exists():
            for log in self.module.REPORT.glob('*.log'):
                (self.output / log.name).write_text(sanitize(log.read_text(errors='replace'), self.secrets))
        self.tmp.cleanup()
        if hasattr(self, 'original_process_env'):
            os.environ.clear(); os.environ.update(self.original_process_env)
        if self.runtime and getattr(self.runtime, 'tearDown_exceptions', []):
            raise RuntimeError('Runtime cleanup failed; process custody requires inspection')

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list', action='store_true')
    group.add_argument('--all', action='store_true')
    group.add_argument('--id')
    group.add_argument('--category')
    parser.add_argument('--profile', choices=['local', 'fixture'], default='local')
    parser.add_argument('--contract', choices=['v1', 'v2'], default='v2')
    parser.add_argument('--handoff', help='Sanitized MANAGER TEST_READY manifest')
    parser.add_argument('--previous-run', help='Prior immutable report run ID for retests')
    args = parser.parse_args(argv)
    try:
        cases = select([c for c in load_scenarios() if c['contract'] == args.contract], args.id, args.category)
    except (ValueError, OSError, KeyError) as error:
        print('Scenario configuration error: ' + str(error), file=sys.stderr)
        return 2
    if args.list:
        for case in cases:
            print(f'{case["id"]}\t{case["category"]}\t{case["handler"]}\t{case["purpose"]}')
        return 0
    if args.previous_run and not re.fullmatch(r'[A-Za-z0-9_-]+', args.previous_run):
        parser.error('Invalid previous-run ID')
    if args.previous_run and not (ROOT / 'test-results' / args.previous_run / 'report.json').is_file():
        parser.error('Previous run does not exist')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
    output = ROOT / 'test-results' / run_id
    output.mkdir(parents=True, exist_ok=False)
    ctx = Context(args, output)
    report = {'schema_version': 1, 'run_id': run_id, 'previous_run': args.previous_run,
              'started_at': datetime.now(timezone.utc).isoformat(), 'profile': args.profile, 'contract': args.contract,
              'source_hashes': {str(p.relative_to(ROOT)): sha(p) for p in
                  [Path(__file__).resolve(), Path(__file__).with_name('browser.mjs'),
                   Path(__file__).with_name('browser_management.mjs'), Path(__file__).with_name('management.py'),
                   Path(__file__).with_name('browser_contract.mjs'), Path(__file__).with_name('runtime_safety.py'),
                   Path(__file__).with_name('errors.py'), Path(__file__).with_name('concurrency_evidence.py'),
                   Path(__file__).with_name('browser_discovery.mjs'),
                   *sorted(SCENARIOS.glob('*.json'))]
                  if p.is_relative_to(ROOT)},
              'results': [], 'limitations': ['Fault-injection handlers use a test-only upstream and are not MySQL/E2E evidence.']}
    try:
        for case in cases:
            started = time.monotonic()
            row = {'id': case['id'], 'category': case['category'], 'handler': case['handler'],
                   'fixtures': case['fixtures'], 'expected': case['expected'], 'evidence': [],
                   'started_at': datetime.now(timezone.utc).isoformat(),
                   'data_source': 'controlled upstream' if case['category'] == 'fault-injection' else
                       'real MySQL' if args.profile == 'local' else 'explicit in-memory fixture profile'}
            try:
                if args.profile not in case['profiles']:
                    row.update(status='SKIPPED', actual='Scenario does not apply to selected profile')
                else:
                    actual = ctx.execute(case['handler'])
                    row.update(status='PASS', actual=actual)
            except Blocked as error:
                row.update(status='BLOCKED', actual=sanitize(str(error), ctx.secrets))
            except Exception as error:
                row.update(status='FAIL', actual=sanitize(type(error).__name__ + ': ' + str(error), ctx.secrets))
            row['duration_seconds'] = round(time.monotonic() - started, 3)
            row['evidence'] = [str(output.relative_to(ROOT) / 'report.json')]
            report['results'].append(row)
            print(f'{row["status"]} {case["id"]}', flush=True)
    finally:
        try:
            ctx.close()
        except Exception as error:
            report['results'].append({'id': 'RUN-CLEANUP', 'status': 'FAIL', 'actual': sanitize(str(error), ctx.secrets)})
        report.update(build_hashes=ctx.builds, commands=ctx.commands, finished_at=datetime.now(timezone.utc).isoformat(),
                      environment={key: ctx.manifest[key] for key in ['compose_project', 'volume', 'isolated_mutations', 'ui_selectors']
                                   if ctx.manifest and key in ctx.manifest})
        report['api_database_identity'] = getattr(ctx, 'database_identity', None)
        report['counts'] = {status: sum(r['status'] == status for r in report['results']) for status in ['PASS', 'FAIL', 'BLOCKED', 'SKIPPED']}
        report['status'] = 'FAIL' if report['counts']['FAIL'] else 'BLOCKED' if report['counts']['BLOCKED'] else 'PASS' if report['counts']['PASS'] else 'SKIPPED'
        # Exclusive writes plus unique directory: prior reports are never overwritten.
        with (output / 'report.json').open('x') as stream:
            stream.write(sanitize(json.dumps(report, ensure_ascii=False, indent=2), ctx.secrets))
        with (output / 'summary.md').open('x') as stream:
            stream.write(f'# {run_id}\n\nStatus: {report["status"]}; profile: {args.profile}\n\n')
            stream.write('Counts: ' + json.dumps(report['counts']) + '\n\n')
            if args.previous_run: stream.write('Previous run: ' + args.previous_run + '\n\n')
            for row in report['results']:
                stream.write(f'- {row["status"]} {row["id"]}: ' + sanitize(json.dumps(row['actual'], ensure_ascii=False), ctx.secrets) + '\n')
            stream.write('\nFault-injection results are distinct from real API/MySQL/browser results. See report.json for expectations, build hashes, commands and timings.\n')
    print(str(output / 'report.json'))
    return 0 if report['status'] == 'PASS' else 1

if __name__ == '__main__':
    raise SystemExit(main())
