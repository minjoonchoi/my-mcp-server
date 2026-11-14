"""Launcher contract tests; no real JVM, original env file or key is touched."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_server as launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='launcher unit ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'BE'
        self.root.mkdir()
        self.config = self.root / 'settings with spaces'
        self.config.mkdir()
        (self.config / 'key.pem').write_text('unit fixture; not a real key')
        self.java = self.root / 'jdk/bin/java'
        self.java.parent.mkdir(parents=True)
        self.java.write_text('#!/bin/sh\nexit 0\n')
        self.java.chmod(0o700)
        self.inherited = {'JAVA_HOME': str(self.java.parent.parent), 'PATH': '/usr/bin', 'HOME': str(self.root)}
        for server in ('api', 'mcp'):
            jar = self.root / f'{server}-server/build/libs/{server}-server-0.1.0.jar'
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b'unit dummy jar')
        self.api = {'MYSQL_DATABASE': 'access_demo', 'MYSQL_USER': 'app_user',
                    'MYSQL_PASSWORD': 'unit-secret $name $(noexec) `noexec` # = \\',
                    'DB_PORT': '3307', 'DEMO_PUBLIC_KEY': 'key.pem'}
        self.mcp = {key: 'key.pem' for key in ['DEMO_PUBLIC_KEY', 'DELEGATION_PRIVATE_KEY', 'DELEGATION_PUBLIC_KEY']}

    def env(self, values, name='server.env'):
        path = self.config / name
        path.write_text('\n'.join(f"{key}='{value}'" for key, value in values.items()) + '\n')
        return path

    def prepare(self, server='api', values=None, shell=None):
        return launcher.prepare_launch(server, self.env(values if values is not None else self.api),
                                       self.inherited if shell is None else shell, self.root)

    def parse(self, text):
        path = self.config / 'parse.env'
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
        return launcher.parse_env(path, {'A', 'B', 'C'})

    def test_parser_literals_quotes_comments_and_crlf(self):
        actual = self.parse('  # comment\r\n A = a=b#literal # comment\r\nB="  $x $(x) `x` \\ # =  "# tail\r\nC= \'a"b\' \t #tail\r\n')
        self.assertEqual(actual, {'A': 'a=b#literal', 'B': '  $x $(x) `x` \\ # =  ', 'C': 'a"b'})
        self.assertEqual(self.parse('A= # empty\nB=#literal\nC=  '), {'A': '', 'B': '#literal', 'C': ''})

    def test_parser_rejects_unsupported_syntax_without_input_echo(self):
        for source in ['export A=secret', 'A="secret', "A='secret' junk", 'A=se"cret', 'A=one\nA=two',
                       'BAD_SECRET=secret', '9BAD=secret', 'A=secret\0', 'A=secret\r', 'A="se\tcret"',
                       'A=secret\u2028more', b'A=\xff', b'\xef\xbb\xbfA=secret']:
            with self.subTest(case=repr(source[:3])):
                with self.assertRaises(launcher.LaunchError) as error:
                    self.parse(source)
                self.assertNotIn('secret', str(error.exception))
                self.assertNotIn('BAD_SECRET', str(error.exception))

    def test_placeholders_all_forms_including_shadowed_file(self):
        for value in ['${NAME}', '${NAME:default}', '${', '${a${b}}']:
            for quote in ['', "'", '"']:
                with self.subTest(form=value, quote=quote):
                    with self.assertRaisesRegex(launcher.LaunchError, 'placeholder syntax'):
                        self.parse('A=' + quote + value + quote)
            with self.assertRaisesRegex(launcher.LaunchError, 'placeholder syntax'):
                self.prepare(values={**self.api, 'MYSQL_PASSWORD': value},
                             shell={**self.inherited, 'MYSQL_PASSWORD': 'safe-unit-value'})
            with self.assertRaisesRegex(launcher.LaunchError, 'placeholder syntax'):
                self.prepare(shell={**self.inherited, 'MYSQL_PASSWORD': value})

    def test_precedence_empty_and_required_placeholders(self):
        spec = self.prepare(values={**self.api, 'SERVER_PORT': '9011'},
                            shell={**self.inherited, 'SERVER_PORT': '9012'})
        self.assertEqual(spec.environment['SERVER_PORT'], '9012')
        self.assertEqual(self.prepare().environment['SERVER_PORT'], '8081')
        for invalid in ['', '   ', 'replace-with-password', '<password>', 'change-me']:
            with self.assertRaises(launcher.LaunchError):
                self.prepare(shell={**self.inherited, 'MYSQL_PASSWORD': invalid})
        with self.assertRaises(launcher.LaunchError):
            self.prepare(values={k: v for k, v in self.api.items() if k != 'MYSQL_PASSWORD'})

    def test_paths_are_independent_of_cwd_and_argv_has_no_secret(self):
        path = self.env(self.api)
        spec = launcher.prepare_launch('api', path.relative_to(self.root), self.inherited, self.root)
        self.assertEqual(spec.cwd, self.root)
        self.assertEqual(spec.environment['DEMO_PUBLIC_KEY'], (self.config / 'key.pem').as_uri())
        self.assertEqual(spec.argv[2], str(self.root / 'api-server/build/libs/api-server-0.1.0.jar'))
        self.assertNotIn(self.api['MYSQL_PASSWORD'], ' '.join(spec.argv))
        self.assertEqual(spec.environment['MYSQL_PASSWORD'], self.api['MYSQL_PASSWORD'])
        self.assertNotIn('unit-secret', repr(spec))

    def test_env_separation_and_fixed_configuration(self):
        hostile = {**self.inherited, 'MYSQL_ROOT_PASSWORD': 'root-secret', 'DELEGATION_PRIVATE_KEY': 'unused',
                   'JAVA_TOOL_OPTIONS': 'injection', '_JAVA_OPTIONS': 'injection', 'JDK_JAVA_OPTIONS': 'injection',
                   'SPRING_APPLICATION_JSON': 'injection', 'SPRING_CONFIG_LOCATION': 'injection',
                   'SPRING_CONFIG_IMPORT': 'injection', 'SPRING_DATASOURCE_URL': 'injection',
                   'DEMO_DIR': 'injection', 'SERVER_ADDRESS': '0.0.0.0', 'LD_PRELOAD': 'injection'}
        api = self.prepare(shell=hostile)
        self.assertEqual(set(api.environment), set(self.inherited) | set(launcher.ALLOWED['api']) - {'SPRING_PROFILES_ACTIVE'})
        self.assertIn('--spring.config.location=classpath:/application.properties,classpath:/application-local.properties', api.argv)
        self.assertIn('--spring.profiles.active=local', api.argv)
        self.assertIn('--server.address=127.0.0.1', api.argv)
        mcp = self.prepare('mcp', self.mcp, {**self.inherited, **self.api, 'MYSQL_ROOT_PASSWORD': 'root-secret',
                                           'SPRING_PROFILES_ACTIVE': 'fixture', 'JAVA_TOOL_OPTIONS': 'injection'})
        self.assertFalse(any(k.startswith(('MYSQL_', 'SPRING_')) for k in mcp.environment))
        self.assertNotIn('DB_PORT', mcp.environment)
        self.assertIn('--spring.config.location=classpath:/application.properties', mcp.argv)
        self.assertFalse(any(a.startswith('--spring.profiles') for a in mcp.argv))

    def test_other_server_file_keys_and_fixed_trust_profile(self):
        for key in ['MYSQL_ROOT_PASSWORD', 'DELEGATION_PRIVATE_KEY', 'SPRING_CONFIG_LOCATION']:
            with self.assertRaisesRegex(launcher.LaunchError, 'unknown or other-server'):
                self.prepare(values={**self.api, key: 'secret'})
        for key, value in [('DEMO_ISSUER', 'https://wrong.invalid'), ('DEMO_AUDIENCE', 'other'),
                           ('SPRING_PROFILES_ACTIVE', 'fixture'), ('MYSQL_USER', 'root'),
                           ('MYSQL_DATABASE', 'db?injection'), ('DB_PORT', '65536'), ('SERVER_PORT', '0')]:
            with self.assertRaises(launcher.LaunchError):
                self.prepare(values={**self.api, key: value})

    def test_exact_api_origin(self):
        for value in ['http://localhost:8081', 'http://[::1]:8081', 'https://127.0.0.1:8081',
                      'http://127.0.0.1', 'http://127.0.0.1:8081/', 'http://127.0.0.1:8081?',
                      'http://127.0.0.1:8081#', 'http://user@127.0.0.1:8081', 'http://127.0.0.1:0',
                      'http://127.0.0.1:65536', 'http://127.1:8081']:
            with self.assertRaisesRegex(launcher.LaunchError, 'invalid'):
                self.prepare('mcp', {**self.mcp, 'ACCESS_BASE_URL': value})
        self.assertEqual(self.prepare('mcp', self.mcp).environment['ACCESS_BASE_URL'], 'http://127.0.0.1:8081')

    def test_missing_unreadable_resources_and_java(self):
        for value in ['https://secret.invalid/key', 'file:/secret/key', 'absent.pem', '.']:
            with self.assertRaises(launcher.LaunchError):
                self.prepare(values={**self.api, 'DEMO_PUBLIC_KEY': value})
        with patch.object(launcher.os, 'access', return_value=False):
            with self.assertRaises(launcher.LaunchError):
                self.prepare()
        with self.assertRaisesRegex(launcher.LaunchError, 'env file'):
            launcher.prepare_launch('api', 'missing.env', self.inherited, self.root)
        with self.assertRaisesRegex(launcher.LaunchError, 'Java executable'):
            self.prepare(shell={**self.inherited, 'JAVA_HOME': str(self.root / 'missing')})
        (self.root / 'api-server/build/libs/api-server-0.1.0.jar').unlink()
        with self.assertRaisesRegex(launcher.LaunchError, 'JAR'):
            self.prepare()

    def test_cli_no_echo_and_no_exec_on_invalid_input(self):
        for argv in [['secret-server'], ['api', '--bad-secret=value'], ['api', 'extra-secret']]:
            with patch.object(launcher.os, 'execve') as execute, contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(launcher.main(argv), 2)
                self.assertNotIn('secret', err.getvalue())
                execute.assert_not_called()

    def test_foreground_exec_without_shell_or_extra_arguments(self):
        spec = self.prepare()
        with patch.object(launcher, 'prepare_launch', return_value=spec), patch.object(launcher.os, 'chdir') as cd, patch.object(launcher.os, 'execve') as execute:
            launcher.main(['api'])
            cd.assert_called_once_with(self.root)
            execute.assert_called_once_with(spec.argv[0], spec.argv, spec.environment)

    def test_packaged_properties_have_no_external_import_or_profiles(self):
        for server in ['api', 'mcp']:
            files = list((launcher.BE_ROOT / f'{server}-server/src/main/resources').glob('application*.properties'))
            for path in files:
                for line in path.read_text().splitlines():
                    if line.startswith('#'):
                        continue
                    self.assertFalse(line.startswith(('spring.config.', 'spring.profiles.')), str(path))


if __name__ == '__main__':
    unittest.main()
