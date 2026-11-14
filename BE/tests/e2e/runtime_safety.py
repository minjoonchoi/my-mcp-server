"""Test-process environment and immutable datasource pinning, without runtime IO."""
import re
from errors import Blocked

ALLOWED_ENV = {'PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ',
               'USER', 'LOGNAME', 'JAVA_HOME', 'SYSTEMROOT', 'E2E_PLAYWRIGHT_MODULE'}

def safe_environment(inherited, settings=None):
    safe = {key: value for key, value in inherited.items() if key in ALLOWED_ENV}
    if settings:
        required = {'MYSQL_DATABASE', 'MYSQL_USER', 'MYSQL_PASSWORD', 'MYSQL_ROOT_PASSWORD', 'DB_PORT'}
        if set(settings) != required:
            raise Blocked('Isolated env must contain only five approved MySQL settings')
        if settings['DB_PORT'] != '3308' or not re.fullmatch(r'[A-Za-z0-9_]+', settings['MYSQL_DATABASE']):
            raise Blocked('Invalid isolated datasource database or port')
        if settings['MYSQL_USER'] == 'root':
            raise Blocked('API must use dedicated non-root account')
        safe.update(settings)
        # Passwords remain in the child environment, never CLI arguments.
        safe.update(SPRING_DATASOURCE_USERNAME=settings['MYSQL_USER'],
                    SPRING_DATASOURCE_PASSWORD=settings['MYSQL_PASSWORD'],
                    SPRING_FLYWAY_USER=settings['MYSQL_USER'], SPRING_FLYWAY_PASSWORD=settings['MYSQL_PASSWORD'])
    return safe

def pinned_api_arguments(settings, run_marker):
    assert re.fullmatch(r'[A-Za-z0-9_-]+', run_marker)
    safe_environment({}, settings)
    url = ('jdbc:mysql://127.0.0.1:3308/' + settings['MYSQL_DATABASE'] +
           '?sslMode=DISABLED&allowPublicKeyRetrieval=true&connectTimeout=2000&socketTimeout=3000'
           '&connectionAttributes=testerRun:' + run_marker)
    return ['--spring.config.location=classpath:/application.properties,classpath:/application-local.properties',
            '--spring.profiles.active=local', '--spring.datasource.url=' + url,
            '--spring.flyway.url=' + url]
