#!/usr/bin/env python3
"""Read a small dotenv data format and replace this process with one fixed JVM."""
import argparse
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shutil
import sys
import unicodedata

BE_ROOT = Path(__file__).resolve().parents[1]
COMMON = frozenset({'DEMO_PUBLIC_KEY', 'DEMO_ISSUER', 'DEMO_AUDIENCE', 'SERVER_PORT'})
ALLOWED = {
    'api': COMMON | {'MYSQL_DATABASE', 'MYSQL_USER', 'MYSQL_PASSWORD', 'DB_PORT',
                     'AUTH_PUBLIC_KEY', 'AUTH_PRIVATE_KEY',
                     'SPRING_PROFILES_ACTIVE'},
    'mcp': COMMON | {'DELEGATION_PRIVATE_KEY', 'DELEGATION_PUBLIC_KEY', 'ACCESS_BASE_URL'},
}
RUNTIME_ENV = frozenset({'PATH', 'JAVA_HOME', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE',
                         'TMPDIR', 'TMP', 'TEMP', 'TZ', 'SYSTEMROOT'})
DEFAULTS = {
    'api': {'SERVER_PORT': '8081', 'SPRING_PROFILES_ACTIVE': 'local',
            'DEMO_ISSUER': 'https://demo-delegation.invalid', 'DEMO_AUDIENCE': 'access-api'},
    'mcp': {'SERVER_PORT': '8080', 'ACCESS_BASE_URL': 'http://127.0.0.1:8081',
            'DEMO_ISSUER': 'https://demo-issuer.invalid', 'DEMO_AUDIENCE': 'access-mcp'},
}


class LaunchError(Exception):
    """Only fixed categories, approved key names and line numbers may be displayed."""


def fail(category, key=None, line=None):
    detail = f' ({key})' if key in COMMON | ALLOWED['api'] | ALLOWED['mcp'] else ''
    if line is not None:
        detail += f' at line {line}'
    raise LaunchError(category + detail)


def validate_value(value, key=None, line=None):
    if any(unicodedata.category(c) == 'Cc' or c in '\u2028\u2029' for c in value):
        fail('unsupported control character', key, line)
    if '${' in value:
        fail('unsupported placeholder syntax', key, line)


def parse_env(path, allowed):
    try:
        with path.open('rb') as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            fail('env file too large')
        source = data.decode('utf-8')
    except (OSError, UnicodeError):
        fail('cannot read UTF-8 env file')
    values = {}
    lines = source.split('\n')
    for number, raw in enumerate(lines, 1):
        # CRLF is a line separator; no other control characters are accepted as syntax.
        if number < len(lines):
            raw = raw.removesuffix('\r')
        if any((unicodedata.category(c) == 'Cc' and c != '\t') or c in '\u2028\u2029' for c in raw):
            fail('unsupported control character', line=number)
        stripped = raw.strip(' \t')
        if not stripped or stripped.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)', stripped)
        if not match:
            fail('invalid assignment syntax', line=number)
        key, value = match.groups()
        if key not in allowed:
            fail('unknown or other-server key', line=number)
        if key in values:
            fail('duplicate key', key, number)
        # A hash after whitespace immediately following '=' is an empty value/comment.
        value = '' if re.match(r'^[ \t]+#', value) else value.lstrip(' \t')
        if value.startswith(('"', "'")):
            end = value.find(value[0], 1)
            if end < 0:
                fail('unterminated quote', key, number)
            tail = value[end + 1:].strip(' \t')
            if tail and not tail.startswith('#'):
                fail('trailing quote data', key, number)
            value = value[1:end]
        else:
            value = re.split(r'[ \t]+#', value, maxsplit=1)[0].rstrip(' \t')
            if '"' in value or "'" in value:
                fail('misplaced quote', key, number)
        # Always check the file before shell precedence, including shadowed entries.
        validate_value(value, key, number)
        values[key] = value
    return values


def port(value, key):
    if not re.fullmatch(r'[0-9]{1,5}', value) or not 1 <= int(value) <= 65535:
        fail('invalid port', key)


def readable_file(path, category, key=None):
    try:
        if not path.is_file() or not os.access(path, os.R_OK):
            fail(category, key)
        with path.open('rb') as stream:
            stream.read(1)
    except OSError:
        fail(category, key)


@dataclass
class LaunchSpec:
    argv: list
    environment: dict = field(repr=False)
    cwd: Path


def prepare_launch(server, env_file=None, inherited=None, root=BE_ROOT):
    if server not in ALLOWED:
        fail('invalid server')
    inherited = os.environ if inherited is None else inherited
    root = root.resolve()
    env_path = Path(env_file) if env_file is not None else Path('.env.' + server)
    if not env_path.is_absolute():
        env_path = root / env_path
    try:
        env_path = env_path.resolve()
    except (OSError, ValueError):
        fail('invalid env file path')
    source = parse_env(env_path, ALLOWED[server])
    settings = {}
    for key in sorted(ALLOWED[server]):
        value = inherited[key] if key in inherited else source.get(key, DEFAULTS[server].get(key))
        if value is None:
            fail('missing required value', key)
        validate_value(value, key)
        if not value or not value.strip() or value.lower().startswith(('replace-with-', 'change-me', '<')):
            fail('empty or placeholder value', key)
        settings[key] = value
    for key in ('DEMO_ISSUER', 'DEMO_AUDIENCE'):
        if settings[key] != DEFAULTS[server][key]:
            fail('conflicting fixed trust identity', key)
    port(settings['SERVER_PORT'], 'SERVER_PORT')
    if server == 'api':
        if settings['SPRING_PROFILES_ACTIVE'] != 'local':
            fail('unsupported profile', 'SPRING_PROFILES_ACTIVE')
        port(settings['DB_PORT'], 'DB_PORT')
        if not re.fullmatch(r'[A-Za-z0-9_]{1,64}', settings['MYSQL_DATABASE']):
            fail('invalid database identifier', 'MYSQL_DATABASE')
        if not re.fullmatch(r'[A-Za-z0-9_]{1,32}', settings['MYSQL_USER']) or settings['MYSQL_USER'].lower() == 'root':
            fail('invalid non-root database user', 'MYSQL_USER')
    else:
        match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})', settings['ACCESS_BASE_URL'])
        if not match:
            fail('invalid API origin', 'ACCESS_BASE_URL')
        port(match[1], 'ACCESS_BASE_URL')
    keys = ['DEMO_PUBLIC_KEY'] + (['DELEGATION_PRIVATE_KEY', 'DELEGATION_PUBLIC_KEY'] if server == 'mcp'
                                else ['AUTH_PUBLIC_KEY', 'AUTH_PRIVATE_KEY'])
    for key in keys:
        value = settings[key]
        if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', value):
            fail('key must be a local filesystem path', key)
        try:
            path = Path(value)
            if not path.is_absolute():
                path = env_path.parent / path
            path = path.resolve()
            readable_file(path, 'key file is not readable', key)
            settings[key] = path.as_uri()
        except (OSError, ValueError):
            fail('invalid key file path', key)
    jar = root / f'{server}-server/build/libs/{server}-server-0.1.0.jar'
    readable_file(jar, 'build the server JAR first')
    runtime = {key: value for key, value in inherited.items() if key in RUNTIME_ENV}
    if 'JAVA_HOME' in runtime:
        java = str(Path(runtime['JAVA_HOME']) / 'bin/java')
    else:
        java = shutil.which('java', path=runtime.get('PATH', os.defpath))
    if not java or not Path(java).is_file() or not os.access(java, os.X_OK):
        fail('Java executable unavailable')
    argv = [str(Path(java).resolve()), '-jar', str(jar)]
    location = 'classpath:/application.properties'
    if server == 'api':
        location += ',classpath:/application-local.properties'
    argv += ['--spring.config.location=' + location, '--server.address=127.0.0.1']
    if server == 'api':
        argv.append('--spring.profiles.active=local')
        settings.pop('SPRING_PROFILES_ACTIVE')
    # No inherited JVM/Spring injection channels, root password or other-server data.
    runtime.update(settings)
    return LaunchSpec(argv, runtime, root)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise LaunchError('invalid command arguments; use --help')


def main(argv=None):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('server', choices=('api', 'mcp'))
    parser.add_argument('--env-file', help='env file relative to BE; defaults to .env.api or .env.mcp')
    try:
        args = parser.parse_args(argv)
        launch = prepare_launch(args.server, args.env_file)
        os.chdir(launch.cwd)
        # Replace the foreground process: JVM receives Ctrl+C and owns its exit status.
        os.execve(launch.argv[0], launch.argv, launch.environment)
    except LaunchError as error:
        print('Launcher: ' + str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print('Launcher: cannot prepare or execute Java', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
