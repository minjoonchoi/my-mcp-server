#!/usr/bin/env python3
"""Independent public-CLI launcher checks; no real JVM, socket or Docker use.

The child is a test-owned executable that records hashes, never credential values.
Run with Python's unittest discover, or directly for an immutable JSON report.
"""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
import uuid

BE = Path(__file__).resolve().parents[2]
SOURCE = BE / 'scripts/run_server.py'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class LauncherCliContract(unittest.TestCase):
    def setUp(self):
        if not SOURCE.is_file():
            self.fail('Launcher source not available; independent CLI checks cannot run')
        self.tmp = tempfile.TemporaryDirectory(prefix='launcher-cli-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'project with spaces' / 'BE'
        (self.root / 'scripts').mkdir(parents=True)
        self.launcher = self.root / 'scripts/run_server.py'
        shutil.copyfile(SOURCE, self.launcher)
        self.envdir = self.root / 'env fixtures'
        self.envdir.mkdir()
        for name in ['issuer-public.pem', 'delegation-public.pem', 'delegation-private.pem']:
            (self.envdir / name).write_text('TEST ONLY NON-KEY CONTENT\n')
        for module in ['api-server', 'mcp-server']:
            folder = self.root / module / 'build/libs'
            folder.mkdir(parents=True)
            (folder / (module + '-0.1.0.jar')).write_bytes(b'TEST ONLY NON-JAR')
        self.bin = self.base / 'fake-java/bin'
        self.bin.mkdir(parents=True)
        self.calls = self.base / 'child-observations.jsonl'
        fake = ('#!' + sys.executable + '\n' +
                'import hashlib,json,os,sys\n' +
                'from pathlib import Path\n' +
                'h=lambda v:hashlib.sha256(v.encode()).hexdigest()\n' +
                'record={"argv_hashes":[h(a) for a in sys.argv[1:]],' +
                '"env_hashes":{k:h(v) for k,v in os.environ.items()},"cwd":os.getcwd()}\n' +
                'with open(' + repr(str(self.calls)) + ',"a") as out:out.write(json.dumps(record)+"\\n")\n')
        (self.bin / 'java').write_text(fake)
        (self.bin / 'java').chmod(0o700)
        self.parent = {'PATH': str(self.bin) + os.pathsep + '/usr/bin:/bin',
                       'HOME': str(self.base), 'JAVA_HOME': str(self.bin.parent),
                       'LANG': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}
        self.password = 'independent-sentinel-7c19'
        self.api = {'MYSQL_DATABASE': 'access_e2e_v2', 'MYSQL_USER': 'access_e2e',
                    'MYSQL_PASSWORD': self.password, 'DB_PORT': '3308',
                    'DEMO_PUBLIC_KEY': 'delegation-public.pem'}
        self.mcp = {'DEMO_PUBLIC_KEY': 'issuer-public.pem',
                    'DELEGATION_PRIVATE_KEY': 'delegation-private.pem',
                    'DELEGATION_PUBLIC_KEY': 'delegation-public.pem'}
        self.checked = 0

    def write_env(self, target='api', values=None, extra=''):
        p = self.envdir / ('.env.' + target)
        data = dict(self.api if target == 'api' else self.mcp)
        if values is not None:
            for key, value in values.items():
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
        p.write_text('\n'.join(k + '=' + v for k, v in data.items()) + '\n' + extra)
        return p

    def invoke(self, p, target='api', shell=None, cwd=None, extra=()):
        self.calls.unlink(missing_ok=True)
        env = {**self.parent, **(shell or {})}
        result = subprocess.run([sys.executable, str(self.launcher), target,
                                 '--env-file', str(p), *extra],
                                env=env, cwd=cwd or self.base, capture_output=True,
                                text=True, timeout=10)
        observations = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        self.checked += 1
        # Even failures must not print values, raw source lines or unknown identifiers.
        self.assertTrue(self.password not in result.stdout + result.stderr, 'Synthetic password leaked by launcher')
        return result, observations

    def accepted(self, p, target='api', shell=None, cwd=None):
        result, calls = self.invoke(p, target, shell, cwd)
        self.assertTrue(result.returncode == 0, 'Valid CLI case rejected; inspect source safely')
        self.assertTrue(len(calls) >= 1, 'Launcher did not invoke independent child')
        return calls[-1]

    def rejected(self, p, target='api', shell=None, extra=(), forbidden=()):
        result, calls = self.invoke(p, target, shell, extra=extra)
        self.assertTrue(result.returncode != 0, 'Invalid CLI case was accepted')
        self.assertTrue(not calls, 'Invalid configuration executed Java before rejection')
        self.assertTrue(all(value not in result.stdout + result.stderr for value in forbidden),
                        'Invalid-input value or raw identifier leaked')

    def setting(self, observation, key, value):
        prop = key.lower().replace('_', '.')
        envhash = observation['env_hashes'].get(key)
        self.assertTrue(envhash == digest(value) or digest('--' + prop + '=' + value) in observation['argv_hashes'],
                        'Resolved setting differs from independent expectation: ' + key)

    def test_defaults_precedence_empty_and_trust_identity(self):
        for target, port, issuer, audience in [('api', '8081', 'https://demo-delegation.invalid', 'access-api'),
                                                ('mcp', '8080', 'https://demo-issuer.invalid', 'access-mcp')]:
            with self.subTest(target=target):
                p = self.write_env(target)
                obs = self.accepted(p, target)
                self.setting(obs, 'SERVER_PORT', port)
                self.setting(obs, 'DEMO_ISSUER', issuer)
                self.setting(obs, 'DEMO_AUDIENCE', audience)
                p = self.write_env(target, {'SERVER_PORT': '18121'})
                self.setting(self.accepted(p, target), 'SERVER_PORT', '18121')
                self.setting(self.accepted(p, target, {'SERVER_PORT': '18122'}), 'SERVER_PORT', '18122')
                self.rejected(p, target, {'SERVER_PORT': ''})
                self.rejected(self.write_env(target, {'DEMO_ISSUER': 'https://untrusted.invalid'}), target)
                self.rejected(self.write_env(target, {'DEMO_AUDIENCE': 'untrusted'}), target)
        p = self.write_env()
        self.rejected(p, shell={'MYSQL_PASSWORD': ''})
        override = 'independent-shell-secret-5c21'
        obs = self.accepted(p, shell={'MYSQL_PASSWORD': override})
        self.setting(obs, 'MYSQL_PASSWORD', override)

    def test_literal_grammar_without_shell_execution(self):
        marker = self.base / 'MUST_NOT_EXIST'
        examples = [
            (' plain words=with=equals ', 'plain words=with=equals'),
            ('"  quoted # = spaces  " # comment', '  quoted # = spaces  '),
            ("'single # = quote' # comment", 'single # = quote'),
            ('abc#kept # removed', 'abc#kept'),
            ('literal\\backslash$name', 'literal\\backslash$name'),
            ('"$(touch ' + str(marker) + ')"', '$(touch ' + str(marker) + ')'),
            ('"`touch ' + str(marker) + '`"', '`touch ' + str(marker) + '`'),
        ]
        for index, (syntax, expected) in enumerate(examples):
            with self.subTest(case=index):
                p = self.write_env(values={'MYSQL_PASSWORD': None},
                                   extra='\n # comment\n MYSQL_PASSWORD = ' + syntax + '\n')
                obs = self.accepted(p)
                self.setting(obs, 'MYSQL_PASSWORD', expected)
                self.assertFalse(marker.exists(), 'Shell substitution executed')

    def test_invalid_syntax_encoding_control_and_unknown_keys(self):
        additions = ['MYSQL_DATABASE=duplicate\n', 'export BAD=value\n',
                     '1BAD=value\n', 'NO_EQUALS\n', 'UNKNOWN_SENTINEL_51=secret\n',
                     'MYSQL_PASSWORD="unterminated_sentinel_91\n', 'MYSQL_PASSWORD="ok"junk\n',
                     'MYSQL_PASSWORD=mis"placed\n', 'MYSQL_PASSWORD=bad\x00value\n',
                     'MYSQL_PASSWORD=bad\x01value\n', 'MYSQL_PASSWORD=bad\x7fvalue\n']
        for index, extra in enumerate(additions):
            with self.subTest(case=index):
                p = self.write_env(values={'MYSQL_PASSWORD': None} if 'MYSQL_PASSWORD' in extra else {}, extra=extra)
                self.rejected(p, forbidden=('UNKNOWN_SENTINEL_51', 'unterminated_sentinel_91', 'bad\x01value'))
        p = self.write_env(); p.write_bytes(p.read_bytes() + b'\xff\xfe\n')
        self.rejected(p)

    def test_placeholder_rejected_in_all_file_values_and_selected_shell(self):
        for index, value in enumerate(['${NAME}', '${NAME:default}', '${${NAME}}', '${',
                                       "'${NAME}'", '"${NAME}"']):
            with self.subTest(case=index):
                p = self.write_env(values={'MYSQL_PASSWORD': value})
                self.rejected(p, forbidden=(value,))
                self.rejected(p, shell={'MYSQL_PASSWORD': 'safe-shell-override'}, forbidden=(value,))
        p = self.write_env()
        for value in ['${SENTINEL}', '${SENTINEL:default}', '${']:
            self.rejected(p, shell={'MYSQL_PASSWORD': value}, forbidden=(value,))
        for key, safe_value in self.api.items():
            p = self.write_env(values={key: '${HIDDEN_SENTINEL}'})
            self.rejected(p, shell={key: safe_value}, forbidden=('${HIDDEN_SENTINEL}',))

    def test_required_values_resources_and_no_extra_arguments(self):
        for key in self.api:
            for value in [None, '', 'replace-with-independent-value']:
                with self.subTest(key=key, kind='missing' if value is None else 'empty' if not value else 'placeholder'):
                    self.rejected(self.write_env(values={key: value}))
        for target in ['api', 'mcp']:
            self.rejected(self.envdir / 'missing.env', target)
            self.rejected(self.write_env(target), target, extra=('--spring.config.location=/untrusted',))
            for key in (['DEMO_PUBLIC_KEY'] if target == 'api' else list(self.mcp)):
                self.rejected(self.write_env(target, {key: 'missing-key.pem'}), target)
                self.rejected(self.write_env(target, {key: 'https://untrusted.invalid/key'}), target)
        jar = self.root / 'api-server/build/libs/api-server-0.1.0.jar'
        jar.unlink(); self.rejected(self.write_env())

    def test_unreadable_resources_and_unavailable_java(self):
        p = self.write_env()
        p.chmod(0)
        try:
            self.rejected(p)
        finally:
            p.chmod(0o600)
        key = self.envdir / 'delegation-public.pem'
        key.chmod(0)
        try:
            self.rejected(p)
        finally:
            key.chmod(0o600)
        self.rejected(p, shell={'JAVA_HOME': str(self.base / 'missing-java')})

    def test_cwd_path_bases_and_default_env(self):
        p = self.write_env()
        relative = p.relative_to(self.root)
        for cwd in [self.root, self.root.parent, self.base]:
            with self.subTest(cwd=cwd.name):
                obs = self.accepted(relative, cwd=cwd)
                self.assertEqual(obs['cwd'], str(self.root))
                expected = self.envdir / 'delegation-public.pem'
                self.assertTrue(any(obs['env_hashes'].get('DEMO_PUBLIC_KEY') == digest(v) or
                                    digest('--demo.public-key=' + v) in obs['argv_hashes']
                                    for v in [expected.as_uri(), 'file:' + str(expected)]),
                                'Key path did not resolve against env-file directory')
        data = dict(self.api, DEMO_PUBLIC_KEY=str(self.envdir / 'delegation-public.pem'))
        (self.root / '.env.api').write_text('\n'.join(k + '=' + v for k, v in data.items()) + '\n')
        result = subprocess.run([sys.executable, str(self.launcher), 'api'], env=self.parent,
                                cwd=self.base, capture_output=True, text=True, timeout=10)
        self.assertTrue(result.returncode == 0, 'Default server env path depends on caller cwd')

    def test_server_separation_and_packaged_configuration_pin(self):
        hostile = {key: 'independent-hostile-sentinel' for key in [
            'MYSQL_ROOT_PASSWORD', 'UNRELATED_SECRET', 'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS',
            'JDK_JAVA_OPTIONS', 'SPRING_APPLICATION_JSON', 'SPRING_CONFIG_LOCATION',
            'SPRING_CONFIG_ADDITIONAL_LOCATION', 'SPRING_CONFIG_IMPORT', 'SPRING_CONFIG_NAME',
            'SPRING_PROFILES_INCLUDE', 'SPRING_DATASOURCE_URL', 'SPRING_FLYWAY_URL',
            'SPRING_DATASOURCE_PASSWORD', 'DEMO_DIR']}
        for target in ['api', 'mcp']:
            foreign = self.mcp if target == 'api' else self.api
            shell = {**hostile, **{k: 'independent-other-server-sentinel' for k in foreign if k != 'DEMO_PUBLIC_KEY'}}
            obs = self.accepted(self.write_env(target), target, shell)
            self.assertTrue(not (set(hostile) & set(obs['env_hashes'])), 'Hostile inherited variable forwarded')
            self.assertTrue(not ((set(foreign) - {'DEMO_PUBLIC_KEY'}) & set(obs['env_hashes'])), 'Other-server secret forwarded')
            allowed = {'PATH', 'JAVA_HOME', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR', 'TMP', 'TEMP', 'TZ', 'SYSTEMROOT',
                       'DEMO_PUBLIC_KEY', 'DEMO_ISSUER', 'DEMO_AUDIENCE', 'SERVER_PORT'}
            # macOS can inject this encoding marker while loading the Python observer.
            if sys.platform == 'darwin':
                allowed.add('__CF_USER_TEXT_ENCODING')
            allowed.update(self.api if target == 'api' else self.mcp)
            if target == 'mcp':
                allowed.add('ACCESS_BASE_URL')
            self.assertFalse(set(obs['env_hashes']) - allowed, 'Unexpected child environment key')
            location = 'classpath:/application.properties' + (',classpath:/application-local.properties' if target == 'api' else '')
            self.assertIn(digest('--spring.config.location=' + location), obs['argv_hashes'])
            if target == 'api':
                self.assertIn(digest('--spring.profiles.active=local'), obs['argv_hashes'])
            self.assertNotIn(digest(self.password), obs['argv_hashes'])
            forbidden_key = 'DELEGATION_PRIVATE_KEY' if target == 'api' else 'MYSQL_PASSWORD'
            self.rejected(self.write_env(target, {forbidden_key: 'foreign-secret'}), target)
            self.rejected(self.write_env(target, {'SPRING_CONFIG_LOCATION': 'config/'}), target)
            self.rejected(self.write_env(target, {'SPRING_PROFILES_ACTIVE': 'fixture'}), target)

    def test_exact_upstream_url_and_ports(self):
        for value in ['http://localhost:8081', 'http://[::1]:8081', 'http://127.0.0.2:8081',
                      'https://127.0.0.1:8081', 'http://127.0.0.1', 'http://127.0.0.1:0',
                      'http://127.0.0.1:65536', 'http://127.0.0.1:8081/',
                      'http://user@127.0.0.1:8081', 'http://127.0.0.1:8081?x=1',
                      'http://127.0.0.1:8081#part']:
            self.rejected(self.write_env('mcp', {'ACCESS_BASE_URL': value}), 'mcp')
        for value in ['0', '65536', '-1', 'bad', '1.5']:
            self.rejected(self.write_env(values={'SERVER_PORT': value}))
            self.rejected(self.write_env(values={'DB_PORT': value}))
        self.setting(self.accepted(self.write_env('mcp', {'ACCESS_BASE_URL': 'http://127.0.0.1:18121'}), 'mcp'),
                     'ACCESS_BASE_URL', 'http://127.0.0.1:18121')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-run')
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    output = BE / 'test-results' / ('launcher-cli-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LauncherCliContract)
    ids = [case.id() for case in suite]
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    # Traceback contents stay in local test execution; persistent results carry only stable IDs.
    failed_ids = [case.id() for case, _ in result.failures + result.errors]
    report = {'run_id': output.name, 'previous_run': args.previous_run, 'started_at': started,
              'python_version': sys.version.split()[0], 'profile': 'offline-fake-java', 'data_source': 'synthetic CLI fixtures; no real JVM/MySQL',
              'finished_at': datetime.now(timezone.utc).isoformat(),
              'launcher_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest() if SOURCE.exists() else None,
              'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'commands': [['python3', 'tests/e2e/test_env_launcher.py']],
              'tests_run': result.testsRun, 'failures': [case.id() for case, _ in result.failures],
              'errors': [case.id() for case, _ in result.errors],
              'status': 'PASS' if result.wasSuccessful() else 'FAIL'}
    report['results'] = [{'id': case, 'status': 'FAIL' if any(fail.startswith(case) for fail in failed_ids) else 'PASS',
                          'fixtures': ['private synthetic env/key/JAR paths and hash-only child observer'],
                          'expected': 'Documented public CLI contract; invalid input rejected before child execution',
                          'actual': 'See independent assertion outcome; no real JVM/MySQL used'} for case in ids]
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'summary.md').write_text('# Independent launcher CLI\n\n' + report['status'] +
        '; synthetic child only, not runtime/MySQL evidence.\n')
    print(output / 'report.json')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
