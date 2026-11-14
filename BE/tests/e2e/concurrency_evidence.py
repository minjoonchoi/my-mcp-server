"""Observe overlapping real API transactions while holding a test-owned row lock."""
import concurrent.futures
from collections import Counter
import selectors
import subprocess
import threading
import time

def replay_audit(responses, rows, spec, initial):
    """Compare response multiset to the serialized committed audit, including present."""
    state = initial
    expected = []
    assert len(rows) == len(responses), 'Every successful request must have exactly one audit row'
    for row in rows:
        assert row['operation'] in [spec['add'], spec['remove']]
        assert [row['left_id'], row['right_id']] == list(spec['args'].values())
        assert row['success'] in [1, True]
        desired = row['operation'] == spec['add']
        assert bool(row['changed']) is (state != desired), 'Audit transition inconsistent with prior committed state'
        state = desired
        expected.append((row['operation'], bool(row['changed']), state))
    actual = []
    for response in responses:
        assert set(response) == {'operation', 'relationship', 'changed', 'present', 'impact'}
        assert response['relationship'] == spec['args']
        assert isinstance(response['changed'], bool) and isinstance(response['present'], bool)
        actual.append((response['operation'], response['changed'], response['present']))
    assert Counter(actual) == Counter(expected), 'Response operation/changed/present multiset differs from serialized audit'
    return state

class RowHold:
    def __init__(self, ctx):
        self.ctx, self.proc, self.connection_id = ctx, None, None

    def __enter__(self):
        assert self.ctx.database_verified and self.ctx.manifest['isolated_mutations'] is True
        argv = ['docker', 'compose', '--env-file', self.ctx.env_file(), '-p', self.ctx.manifest['compose_project'],
                'exec', '-T', 'access-db', 'sh', '-c',
                'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --host=127.0.0.1 --user="$MYSQL_USER" --database="$MYSQL_DATABASE" --batch --skip-column-names --unbuffered']
        self.proc = subprocess.Popen(argv, cwd=self.ctx.module.ROOT, env=self.ctx.runtime_env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.ctx.commands.append({'argv': argv, 'cwd': str(self.ctx.module.ROOT), 'pid': self.proc.pid, 'purpose': 'test-owned transaction holding SYSTEM_ADMIN row'})
        self.proc.stdin.write("START TRANSACTION; SELECT id INTO @tester_anchor FROM roles WHERE id='SYSTEM_ADMIN' AND protected=TRUE FOR UPDATE; SELECT CONCAT('TESTER_READY:',CONNECTION_ID());\n")
        self.proc.stdin.flush()
        selector = selectors.DefaultSelector(); selector.register(self.proc.stdout, selectors.EVENT_READ)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if selector.select(timeout=.2):
                    line = self.proc.stdout.readline().strip()
                    if line.startswith('TESTER_READY:'):
                        self.connection_id = int(line.split(':')[1]); return self
                if self.proc.poll() is not None: break
            raise AssertionError('Test row lock was not acquired')
        except BaseException:
            self.__exit__(None, None, None)
            raise
        finally:
            selector.close()

    def waiters(self):
        # Root is used only for container-local read-only Performance Schema observation.
        sql = ("SELECT r.ENGINE_TRANSACTION_ID,t.PROCESSLIST_ID,r.OBJECT_NAME,r.INDEX_NAME,r.LOCK_TYPE,r.LOCK_MODE,r.LOCK_DATA "
               "FROM performance_schema.data_lock_waits w "
               "JOIN performance_schema.data_locks r ON r.ENGINE_LOCK_ID=w.REQUESTING_ENGINE_LOCK_ID "
               "JOIN performance_schema.threads t ON t.THREAD_ID=w.REQUESTING_THREAD_ID "
               "JOIN performance_schema.threads b ON b.THREAD_ID=w.BLOCKING_THREAD_ID "
               "WHERE r.OBJECT_SCHEMA=DATABASE() AND r.OBJECT_NAME='roles' AND b.PROCESSLIST_ID=" + str(self.connection_id) + ';')
        return [line.split('\t') for line in self.ctx.database_observer(sql).splitlines()]

    def __exit__(self, *_):
        if self.proc is None: return
        try:
            self.proc.communicate(input='ROLLBACK;\n', timeout=5)
        except (subprocess.TimeoutExpired, BrokenPipeError):
            if self.connection_id is not None:
                # Only this helper's explicitly observed connection is eligible.
                self.ctx.database_observer('KILL CONNECTION ' + str(self.connection_id) + ';')
            self.proc.kill(); self.proc.communicate(timeout=5)

def observed_overlap(ctx, operations, invoke):
    count = len(operations)
    barrier = threading.Barrier(count + 1)
    timings = [None] * count
    def run(index):
        barrier.wait(timeout=5)
        started = time.monotonic()
        try:
            return invoke(operations[index])
        finally:
            timings[index] = {'operation': operations[index], 'start': started, 'end': time.monotonic()}
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        with RowHold(ctx) as lock:
            futures = [pool.submit(run, i) for i in range(count)]
            barrier.wait(timeout=5)
            waiters = []
            deadline = time.monotonic() + 1.2  # Below product upstream's two-second timeout.
            while time.monotonic() < deadline:
                waiters = lock.waiters()
                if len({row[1] for row in waiters}) >= count: break
        responses = [future.result() for future in futures]
    assert len({row[1] for row in waiters}) >= count, 'Actual server transaction overlap not observed; concurrency requirement not met'
    assert max(t['start'] for t in timings) < min(t['end'] for t in timings), 'Client request intervals did not overlap'
    return responses, {'barrier_participants': count, 'server_waiters': waiters, 'client_intervals': timings,
                       'controlled_lock': 'SYSTEM_ADMIN protected role row, released with ROLLBACK'}
