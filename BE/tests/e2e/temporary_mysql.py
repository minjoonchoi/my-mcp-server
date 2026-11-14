"""Disposable local MySQL for tests; never connects to or deletes an existing DB."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time

from runtime_safety import safe_environment


class TemporaryMySQL:
    def __init__(self, home, library_path=None):
        self.home = Path(home).resolve()
        for binary in ['mysqld', 'mysql']:
            if not (self.home / 'bin' / binary).is_file():
                raise ValueError('Missing MySQL binary: ' + binary)
        self.temp = tempfile.TemporaryDirectory(prefix='mcp-context-db-')
        self.root = Path(self.temp.name)
        self.password = secrets.token_hex(24)
        self.database, self.user = 'context_test', 'context_app'
        self.process = None
        self.env = safe_environment(os.environ)
        if library_path:
            self.env['LD_LIBRARY_PATH'] = str(Path(library_path).resolve())
        with socket.socket() as connection:
            connection.bind(('127.0.0.1', 0))
            self.port = connection.getsockname()[1]

    def start(self):
        server = str(self.home / 'bin/mysqld')
        self.version = subprocess.check_output([server, '--no-defaults', '--version'], env=self.env, text=True).strip()
        data = self.root / 'data'
        with (self.root / 'initialize.log').open('w') as log:
            proc = subprocess.run([server, '--no-defaults', '--initialize-insecure', '--basedir=' + str(self.home),
                                   '--datadir=' + str(data)], env=self.env, stdout=log, stderr=subprocess.STDOUT, timeout=90)
        if proc.returncode:
            raise RuntimeError('Temporary MySQL initialization failed: ' + (self.root / 'initialize.log').read_text()[-2000:])
        # TCP-only operation also works where Unix socket creation is unavailable.
        # Credentials stay in a mode-600 file inside our private temp directory,
        # never on the server/client command line or in the checked-in source.
        bootstrap = self.root / 'bootstrap.sql'
        bootstrap.write_text(f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{self.password}';\n"
            f"CREATE DATABASE {self.database} CHARACTER SET utf8mb4;\n"
            f"CREATE USER '{self.user}'@'127.0.0.1' IDENTIFIED BY '{self.password}';\n"
            f"GRANT ALL PRIVILEGES ON {self.database}.* TO '{self.user}'@'127.0.0.1';\n")
        bootstrap.chmod(0o600)
        self.process = subprocess.Popen([server, '--no-defaults', '--basedir=' + str(self.home), '--datadir=' + str(data),
            '--socket=', '--init-file=' + str(bootstrap), '--pid-file=' + str(self.root / 'mysql.pid'),
            '--log-error=' + str(self.root / 'server.log'), '--bind-address=127.0.0.1', '--port=' + str(self.port),
            '--mysqlx=0', '--skip-name-resolve', '--skip-log-bin', '--innodb-buffer-pool-size=64M', '--innodb-redo-log-capacity=64M'],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Temporary MySQL failed: ' + (self.root / 'server.log').read_text()[-2000:])
            if self.execute('SELECT 1;').returncode == 0:
                bootstrap.unlink()
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Temporary MySQL did not become ready')

    def environment(self):
        return {'DB_PORT': str(self.port), 'MYSQL_DATABASE': self.database, 'MYSQL_USER': self.user,
                'MYSQL_PASSWORD': self.password}

    def execute(self, sql):
        args = [str(self.home / 'bin/mysql'), '--no-defaults', '--batch', '--skip-column-names', '--connect-timeout=2']
        env = dict(self.env)
        args += ['--protocol=TCP', '--host=127.0.0.1', '--port=' + str(self.port), '--user=' + self.user, '--database=' + self.database]
        env['MYSQL_PWD'] = self.password
        return subprocess.run(args, input=sql, capture_output=True, text=True, env=env, timeout=15)

    def query(self, sql):
        result = self.execute(sql)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.temp.cleanup()
