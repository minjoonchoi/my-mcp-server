"""Fixed v2 domain handlers; scenarios cannot provide executable code or SQL."""
import concurrent.futures
import json
import re
import threading
from errors import Blocked
from concurrency_evidence import observed_overlap, replay_audit

READ_TOOLS = {
    'get_my_access': {}, 'check_my_resource_access': {'resourceId': 'R100'},
    'explain_authorization': {'concept': 'role'}, 'list_access_entities': {'entity': 'roles'},
    'get_access_entity': {'entity': 'users', 'id': 'alice'}, 'get_requestable_roles': {},
}
LINKS = {
    'user_role': {'add': 'assign_role_to_user', 'remove': 'remove_role_from_user',
                  'args': {'userId': 'carol', 'roleId': 'role-analyst'},
                  'path': '/api/manage/users/carol/roles/role-analyst'},
    'role_policy': {'add': 'attach_policy_to_role', 'remove': 'detach_policy_from_role',
                    'args': {'roleId': 'role-billing', 'policyId': 'policy-edit'},
                    'path': '/api/manage/roles/role-billing/policies/policy-edit'},
    'policy_resource': {'add': 'attach_resource_to_policy', 'remove': 'detach_resource_from_policy',
                       'args': {'policyId': 'policy-billing', 'resourceId': 'R300'},
                       'path': '/api/manage/policies/policy-billing/resources/R300'},
}
MUTATION_TOOLS = {spec[action]: spec['args'] for spec in LINKS.values() for action in ['add', 'remove']}
V2_HANDLERS = {
    'v2_protocol': ('protocol', None), 'v2_self': ('self_read', None),
    'v2_catalog': ('catalog', None), 'v2_requestable': ('requestable', None),
    'v2_authority': ('authority', None), 'v2_protected': ('protected', None),
    'v2_invalid': ('invalid', None), 'v2_paths': ('paths', None),
    'v2_audit': ('audit', None), 'v2_restart': ('restart', None),
    'v2_user_role': ('cycle', 'user_role'), 'v2_role_policy': ('cycle', 'role_policy'),
    'v2_policy_resource': ('cycle', 'policy_resource'),
    'v2_concurrent_user_role': ('concurrent', 'user_role'),
    'v2_concurrent_role_policy': ('concurrent', 'role_policy'),
    'v2_concurrent_policy_resource': ('concurrent', 'policy_resource'),
    'v2_atomic_rejection': ('atomic_rejection', None),
    'v2_browser_manage': ('browser_manage', None),
    'v2_browser_faults': ('browser_faults', None),
    'v2_audit_rollback': ('audit_rollback', None),
    'v2_write_boundaries': ('write_boundaries', None),
    'v2_mixed_concurrency': ('mixed_concurrency', None),
    'v2_seed': ('seed', None),
    'ui_guide': ('discovery', 'guide'), 'ui_graph': ('discovery', 'graph'),
    'ui_catalog': ('discovery', 'catalog'), 'ui_manage': ('discovery', 'manage'),
    'ui_switch': ('discovery', 'switch'), 'ui_mobile': ('discovery', 'mobile'),
}

class Management:
    def __init__(self, context):
        self.ctx = context
        self.mod = context.module
        self.runtime = context.runtime
        self.observations = []
        self.local = threading.local()

    def execute(self, handler):
        if not self.ctx.database_verified:
            raise Blocked('Actual API isolated-database connection not verified; v2 handlers prohibited')
        method, argument = V2_HANDLERS[handler]
        result = getattr(self, method)(argument) if argument else getattr(self, method)()
        return {'observations': self.observations, 'result': result}

    def token(self, name):
        defaults = {'mcp-admin-read': 'mcp-admin-read-only'}
        mapped = self.ctx.manifest.get('fixture_names', {}).get(name, defaults.get(name, name))
        if mapped not in self.runtime.tokens:
            raise Blocked('Missing named v2 fixture: ' + name)
        return self.runtime.tokens[mapped]

    def raw_call(self, tool, args=None, actor='mcp-admin', extra=None):
        status, _, payload = self.mod.rpc(self.runtime.mcp, self.token(actor), 'tools/call',
                                          {'name': tool, 'arguments': args or {}}, extra)
        self.local.observation = {'tool': tool, 'arguments': args or {}, 'fixture': actor, 'http': status}
        self.observations.append(self.local.observation)
        assert status == 200, f'{tool}: expected HTTP200, got {status}'
        assert isinstance(payload, dict) and 'result' in payload, f'{tool}: missing result'
        return payload['result']

    def call(self, tool, args=None, actor='mcp-admin', extra=None):
        result = self.raw_call(tool, args, actor, extra)
        assert result.get('isError') is False, f'{tool}: unexpected tool error {result.get("content")}'
        data = result['structuredContent']
        assert json.loads(result['content'][0]['text']) == data, 'Structured/text result differs'
        self.local.observation['domain'] = data
        return data

    def error(self, tool, args, actor, expected):
        result = self.raw_call(tool, args, actor)
        assert result.get('isError') is True, f'{tool}: expected tool error'
        codes = {expected} if isinstance(expected, str) else set(expected)
        actual = result['content'][0]['text']
        assert actual in codes, f'{tool}: expected {codes}, got {actual}'
        self.local.observation['error'] = actual

    def api(self, path, actor='api-admin-read', method='GET', expected=200, body=None):
        status, _, raw = self.mod.request(self.runtime.api, path, self.token(actor), body, method=method)
        self.observations.append({'api': path, 'method': method, 'fixture': actor, 'http': status})
        assert status == expected, f'{method} {path}: expected {expected}, got {status}'
        data = json.loads(raw) if raw else None
        self.observations[-1]['domain'] = data
        return data

    def catalog_items(self, entity, actor='mcp-admin'):
        data = self.call('list_access_entities', {'entity': entity}, actor)
        assert set(data) == {'entity', 'items'} and data['entity'] == entity
        ids = [item['id'] for item in data['items']]
        assert ids == sorted(set(ids)), 'Catalog order or duplicate IDs'
        return data['items']

    def graph(self):
        return {entity: self.catalog_items(entity) for entity in ['users', 'roles', 'policies', 'resources']}

    def db_links(self):
        sql = "SELECT 'user_role',user_id,role_id FROM user_roles ORDER BY user_id,role_id;\n"
        sql += "SELECT 'role_policy',role_id,policy_id FROM role_policies ORDER BY role_id,policy_id;\n"
        sql += "SELECT 'policy_resource',policy_id,resource_id FROM policy_resources ORDER BY policy_id,resource_id;\n"
        return self.ctx.database(sql).splitlines()

    def audit_rows(self):
        mapping = self.ctx.manifest.get('audit_schema')
        if not mapping:
            raise Blocked('Missing handed-off audit_schema table/columns')
        fields = ['actor_id', 'actor_issuer', 'actor_subject', 'operation', 'left_id', 'right_id', 'occurred_at', 'success', 'changed']
        identifiers = [mapping.get('table', ''), mapping.get('order', '')] + [mapping.get(k, '') for k in fields]
        assert all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) for name in identifiers), 'Invalid audit identifier'
        expressions = {k: ('CONVERT(`' + mapping[k] + '` USING utf8mb4)' if k in ['actor_issuer', 'actor_subject'] else '`' + mapping[k] + '`') for k in fields}
        sql = "SELECT JSON_OBJECT(" + ','.join("'" + k + "'," + expressions[k] for k in fields) + ") FROM `" + mapping['table'] + "` ORDER BY `" + mapping['order'] + "`;\n"
        return [json.loads(row) for row in self.ctx.database(sql).splitlines()]

    def state(self):
        return {'links': self.db_links(), 'audit': self.audit_rows()}

    def mutation(self, spec, add, actor='mcp-admin'):
        operation = spec['add' if add else 'remove']
        before = self.graph()
        args = spec['args']
        if 'userId' in args:
            affected = [args['userId']]
        else:
            role_ids = [args['roleId']] if 'roleId' in args else [r['id'] for r in before['roles'] if args['policyId'] in r['policyIds']]
            affected = sorted(u['id'] for u in before['users'] if set(role_ids) & set(u['roleIds']))
        data = self.call(operation, args, actor)
        assert set(data) == {'operation', 'changed', 'relationship', 'present', 'impact'}
        assert data['operation'] == operation and data['relationship'] == args
        assert isinstance(data['changed'], bool) and data['present'] is add
        assert data['impact'] == {'affectedUserIds': affected}, 'Incorrect deterministic impact set'
        return data

    def present(self, kind):
        spec = LINKS[kind]; args = spec['args']
        if kind == 'user_role':
            item = next(u for u in self.catalog_items('users') if u['id'] == args['userId'])
            return args['roleId'] in item['roleIds']
        if kind == 'role_policy':
            item = next(r for r in self.catalog_items('roles') if r['id'] == args['roleId'])
            return args['policyId'] in item['policyIds']
        item = next(p for p in self.catalog_items('policies') if p['id'] == args['policyId'])
        return args['resourceId'] in item['resourceIds']

    def protocol(self):
        status, _, payload = self.mod.rpc(self.runtime.mcp, self.token('mcp-admin'), 'initialize',
            {'protocolVersion': self.mod.PROTOCOL, 'capabilities': {}, 'clientInfo': {'name': 'v2-scenario', 'version': '2'}})
        assert status == 200 and payload['result']['protocolVersion'] == self.mod.PROTOCOL
        assert self.mod.request(self.runtime.mcp, '/mcp', self.token('mcp-admin'), {'jsonrpc': '2.0', 'method': 'notifications/initialized'})[0] == 202
        status, _, payload = self.mod.rpc(self.runtime.mcp, self.token('mcp-admin'), 'tools/list')
        assert status == 200
        tools = {tool['name']: tool for tool in payload['result']['tools']}
        assert set(tools) == set(READ_TOOLS) | set(MUTATION_TOOLS), 'Expected exactly twelve tools'
        for name, tool in tools.items():
            assert tool['inputSchema']['additionalProperties'] is False
            expected_fields = set((READ_TOOLS | MUTATION_TOOLS)[name])
            assert set(tool['inputSchema']['properties']) == expected_fields
            assert tool['annotations']['readOnlyHint'] is (name in READ_TOOLS)
            if name in MUTATION_TOOLS:
                assert tool['annotations']['destructiveHint'] is True and tool['annotations']['idempotentHint'] is True
        return {'tools': sorted(tools), 'schemas_and_annotations': 'verified'}

    def self_read(self):
        for subject, identity in [('alice', 'alice'), ('bob', 'bob'), ('carol', 'carol'), ('local-id-demo', 'U900')]:
            data = self.call('get_my_access', actor='mcp-' + subject)
            assert data['user']['id'] == identity
            direct = self.api('/api/me', 'api-' + ('valid' if subject == 'alice' else subject))
            assert data == direct, 'API and MCP graph mismatch'
            if subject in ['carol', 'local-id-demo']:
                assert all(data[k] == [] for k in ['roles', 'policies', 'resources'])
        return {'independent_id': 'U900', 'verified_subject': 'local-id-demo'}

    def catalog(self):
        required_fields = {'users': {'id', 'name', 'description', 'roleIds'},
                           'roles': {'id', 'name', 'description', 'policyIds', 'protected', 'requestable'},
                           'policies': {'id', 'name', 'description', 'resourceIds', 'protected'},
                           'resources': {'id', 'name', 'description', 'protected'}}
        for actor in ['mcp-alice', 'mcp-bob', 'mcp-carol', 'mcp-admin-read']:
            own = self.call('get_my_access', actor=actor)
            for entity in required_fields:
                items = self.catalog_items(entity, actor)
                assert all(set(item) == required_fields[entity] for item in items), 'Catalog leaked fields or omitted required fields'
                if actor != 'mcp-admin-read':
                    expected = [own['user']['id']] if entity == 'users' else [v['id'] for v in own[entity]]
                    assert [v['id'] for v in items] == sorted(expected)
                for item in items:
                    detail = self.call('get_access_entity', {'entity': entity, 'id': item['id']}, actor)
                    assert detail == {'entity': entity, 'item': item}
                if actor == 'mcp-admin-read':
                    assert items == self.catalog_items(entity), 'Read-only DB admin cannot read full catalog'
        denied = self.api('/api/catalog/users/bob', 'api-valid', expected=404)
        unknown = self.api('/api/catalog/users/no-such-user', 'api-valid', expected=404)
        assert denied == unknown == {'code': 'NOT_FOUND'}
        self.error('get_access_entity', {'entity': 'users', 'id': 'bob'}, 'mcp-alice', {'NOT_FOUND', 'UPSTREAM_NOT_FOUND'})
        return {'visibility': 'ordinary own graph; admin full graph; hidden and unknown equal'}

    def requestable(self):
        roles = self.catalog_items('roles')
        policy_resources = {p['id']: set(p['resourceIds']) for p in self.catalog_items('policies')}
        overlapping_unassigned = []
        for actor in ['mcp-alice', 'mcp-bob', 'mcp-carol', 'mcp-admin-read']:
            own = self.call('get_my_access', actor=actor)
            expected = [{k: role[k] for k in ['id', 'name', 'description']} for role in roles
                        if role['requestable'] and not role['protected'] and role['id'] not in own['user']['roleIds']]
            actual = self.call('get_requestable_roles', actor=actor)
            assert actual == {'roles': expected}, 'Requestable catalog must depend on direct membership only'
            own_resources = {r['id'] for r in own['resources']}
            for role in roles:
                effective = set().union(*(policy_resources[p] for p in role['policyIds']))
                if role['id'] in {r['id'] for r in expected} and effective & own_resources:
                    overlapping_unassigned.append({'actor': actor, 'role': role['id']})
        assert overlapping_unassigned, 'Fixture did not exercise direct-role versus effective-resource distinction'
        return {'public_metadata_only': True, 'direct_membership_filter': True, 'effective_overlap_still_requestable': overlapping_unassigned}

    def cycle(self, kind):
        spec = LINKS[kind]; original = self.present(kind)
        try:
            self.mutation(spec, False)
            affected_actor = 'mcp-carol' if kind == 'user_role' else 'mcp-bob'
            baseline_graph = self.call('get_my_access', actor=affected_actor)
            assert self.mutation(spec, True)['changed'] is True
            assert self.mutation(spec, True)['changed'] is False
            direct_put = self.api(spec['path'], 'api-admin-manage', 'PUT')
            assert direct_put['operation'] == spec['add'] and direct_put['changed'] is False and direct_put['present'] is True
            assert self.present(kind), 'DB-derived catalog did not persist add'
            added_graph = self.call('get_my_access', actor=affected_actor)
            assert {r['id'] for r in added_graph['resources']} > {r['id'] for r in baseline_graph['resources']}, 'Link add did not change current effective resources'
            values = list(spec['args'].values())
            assert '\t'.join([kind, *values]) in self.db_links(), 'Real MySQL relationship missing'
            assert self.mutation(spec, False)['changed'] is True
            assert self.mutation(spec, False)['changed'] is False
            direct_delete = self.api(spec['path'], 'api-admin-manage', 'DELETE')
            assert direct_delete['operation'] == spec['remove'] and direct_delete['changed'] is False and direct_delete['present'] is False
            assert not self.present(kind)
            assert self.call('get_my_access', actor=affected_actor) == baseline_graph, 'Link removal left stale access/provenance'
            assert '\t'.join([kind, *values]) not in self.db_links()
        finally:
            self.mutation(spec, original)
        return {'link': kind, 'transitions': ['absent->present', 'duplicate PUT', 'present->absent', 'absent DELETE'], 'restored': original}

    def authority(self):
        before = self.state()
        for actor in ['mcp-alice', 'mcp-alice-manage', 'mcp-carol', 'mcp-admin-read']:
            for operation, args in MUTATION_TOOLS.items():
                self.error(operation, args, actor, {'FORBIDDEN', 'UPSTREAM_ACCESS_DENIED'})
        for tool, args in READ_TOOLS.items():
            self.error(tool, args, 'mcp-admin-manage-only', 'FORBIDDEN')
        for spec in LINKS.values():
            for method in ['PUT', 'DELETE']:
                self.api(spec['path'], 'api-admin-read', method, 403)
                self.api(spec['path'], 'api-alice-manage', method, 403)
        forged = self.call('get_my_access', actor='mcp-carol', extra={'X-User-Id': 'admin'})
        assert forged['user']['id'] == 'carol' and forged['resources'] == []
        assert self.state() == before, 'Denied requests changed relations or audit success rows'
        for kind, spec in LINKS.items():
            original = self.present(kind)
            try:
                self.mutation(spec, not original, actor='mcp-admin-manage-only')
                assert self.present(kind) is not original
            finally:
                self.mutation(spec, original)
        before = self.state()
        for operation, args in MUTATION_TOOLS.items():
            self.error(operation, args, 'mcp-unknown-manage', {'FORBIDDEN', 'UPSTREAM_ACCESS_DENIED'})
        for spec in LINKS.values():
            for method in ['PUT', 'DELETE']:
                assert self.api(spec['path'], 'api-unknown-manage', method, 403) == {'code': 'FORBIDDEN'}
        assert self.state() == before, 'Unknown actor changed DB'
        return {'separate_scope_and_db_admin': True, 'denial_db_unchanged': True}

    def protected(self):
        graph = self.graph(); before = self.state()
        roles = [r['id'] for r in graph['roles'] if r['protected']]
        policies = [p['id'] for p in graph['policies'] if p['protected']]
        resources = [r['id'] for r in graph['resources'] if r['protected']]
        assert 'SYSTEM_ADMIN' in roles and policies and resources
        requests = []
        for role in roles:
            requests += [('assign_role_to_user', {'userId': 'alice', 'roleId': role}), ('remove_role_from_user', {'userId': 'admin', 'roleId': role})]
            requests += [(op, {'roleId': role, 'policyId': 'policy-edit'}) for op in ['attach_policy_to_role', 'detach_policy_from_role']]
        for policy in policies:
            requests += [(op, {'roleId': 'role-analyst', 'policyId': policy}) for op in ['attach_policy_to_role', 'detach_policy_from_role']]
            requests += [(op, {'policyId': policy, 'resourceId': 'R100'}) for op in ['attach_resource_to_policy', 'detach_resource_from_policy']]
        for resource in resources:
            requests += [(op, {'policyId': 'policy-edit', 'resourceId': resource}) for op in ['attach_resource_to_policy', 'detach_resource_from_policy']]
        for role in roles:
            for policy in policies:
                requests += [(op, {'roleId': role, 'policyId': policy}) for op in ['attach_policy_to_role', 'detach_policy_from_role']]
        for policy in policies:
            for resource in resources:
                requests += [(op, {'policyId': policy, 'resourceId': resource}) for op in ['attach_resource_to_policy', 'detach_resource_from_policy']]
        for operation, args in requests:
            self.error(operation, args, 'mcp-admin', {'FORBIDDEN', 'UPSTREAM_ACCESS_DENIED'})
        assert self.state() == before, 'Protected mutation changed database'
        return {'protected_requests': len(requests), 'database_unchanged': True}

    def invalid(self):
        before = self.state()
        for operation, args in MUTATION_TOOLS.items():
            self.error(operation, {**args, 'approved': True}, 'mcp-admin', 'INVALID_ARGUMENT')
            self.error(operation, {**args, 'actorId': 'admin'}, 'mcp-admin', 'INVALID_ARGUMENT')
            key = next(iter(args))
            for value in ['../escape', '', None, 123, 'a' * 65]:
                self.error(operation, {**args, key: value}, 'mcp-admin', 'INVALID_ARGUMENT')
        for spec in LINKS.values():
            for method in ['PUT', 'DELETE']:
                self.api(spec['path'] + '?approved=true', 'api-admin-manage', method, 400)
                self.api(spec['path'], 'api-admin-manage', method, 400, body={'actorId': 'admin'})
        assert self.state() == before, 'Invalid arguments changed database'
        return {'unknown_keys_and_id_types': 'rejected', 'database_unchanged': True}

    def atomic_rejection(self):
        before = self.state()
        for spec in LINKS.values():
            args = dict(spec['args']); key = next(iter(args)); args[key] = 'missing-reference'
            for action in ['add', 'remove']:
                self.error(spec[action], args, 'mcp-admin', {'NOT_FOUND', 'UPSTREAM_NOT_FOUND'})
                self.error(spec[action], args, 'mcp-alice-manage', {'FORBIDDEN', 'UPSTREAM_ACCESS_DENIED'})
        assert self.state() == before, 'Rejected missing-reference operation changed links/audit'
        return {'failure_cases': 'authenticated missing FK references and nonadmin target concealment',
                'limitations': 'No induced disk/commit/storage outage or production fault endpoint; those failure modes remain untested.'}

    def concurrent(self, kind):
        spec = LINKS[kind]; original = self.present(kind)
        overlap_evidence = []
        try:
            self.mutation(spec, False)
            for add in [True, False]:
                operation = spec['add' if add else 'remove']
                audit_before = len(self.audit_rows())
                results, evidence = observed_overlap(self.ctx, [operation] * 6, lambda name: self.call(name, spec['args']))
                assert sum(result['changed'] is True for result in results) == 1, 'Concurrent idempotence: exactly one state change required'
                assert all(result['present'] is add for result in results)
                assert self.present(kind) is add
                rows = self.audit_rows()[audit_before:]
                assert replay_audit(results, rows, spec, not add) is add
                overlap_evidence.append({'evidence': evidence, 'responses': results, 'audit_order': rows})
        finally:
            self.mutation(spec, original)
        return {'link': kind, 'parallel_requests_per_action': 6, 'exactly_one_change': True, 'overlap': overlap_evidence}

    def paths(self):
        # Carol is dedicated no-role fixture; modifications are only in isolated TESTER DB.
        analyst = LINKS['user_role']
        editor = {**analyst, 'args': {'userId': 'carol', 'roleId': 'role-editor'}}
        before = self.call('get_my_access', actor='mcp-carol')
        assert before['roles'] == [], 'Expected pristine Carol before path test'
        initial = self.call('get_requestable_roles', actor='mcp-carol')
        try:
            self.mutation(analyst, True); self.mutation(editor, True)
            granted = self.call('check_my_resource_access', {'resourceId': 'R100'}, 'mcp-carol')
            assert granted['allowed'] and len(granted['grants']) >= 2
            requestable = self.call('get_requestable_roles', actor='mcp-carol')
            assert not {'role-analyst', 'role-editor'} & {r['id'] for r in requestable['roles']}
            self.mutation(analyst, False)
            remaining = self.call('check_my_resource_access', {'resourceId': 'R100'}, 'mcp-carol')
            assert remaining['allowed'] and all(g['roleId'] == 'role-editor' for g in remaining['grants'])
            self.mutation(editor, False)
            denied = self.call('check_my_resource_access', {'resourceId': 'R100'}, 'mcp-carol')
            assert denied == {'resourceId': 'R100', 'allowed': False, 'resource': None, 'grants': []}
            assert self.call('get_requestable_roles', actor='mcp-carol') == initial
        finally:
            self.mutation(analyst, False); self.mutation(editor, False)
        return {'alternate_path_retained': True, 'last_path_revoked': True, 'requestable_hide_reappear': True}

    def audit(self):
        spec = LINKS['user_role']; original = self.present('user_role')
        self.mutation(spec, False)
        before = self.audit_rows()
        try:
            self.mutation(spec, True)
            rows = self.audit_rows()[len(before):]
            assert len(rows) == 1, 'One durable audit row required for changed successful mutation'
            row = rows[0]
            actor = self.call('get_my_access')['user']['id']
            assert row['actor_id'] == actor and row['actor_subject'] == self.mod.claims(self.token('mcp-admin'))['sub']
            assert row['actor_issuer'] == 'https://demo-issuer.invalid'
            assert row['operation'] == spec['add'] and row['success'] in [True, 1] and row['changed'] in [True, 1]
            assert row['left_id'] == 'carol' and row['right_id'] == 'role-analyst' and row['occurred_at']
        finally:
            self.mutation(spec, original)
        return {'verified_actor_attribution': True, 'durable_rows': rows}

    def restart(self):
        before = self.state()
        tables = ['users', 'roles', 'policies', 'resources', 'user_roles', 'role_policies', 'policy_resources']
        counts_sql = '\n'.join("SELECT '" + table + "',COUNT(*) FROM " + table + ';' for table in tables)
        counts = self.ctx.database(counts_sql)
        history_sql = 'SELECT version,script,checksum,success FROM flyway_schema_history ORDER BY installed_rank;\n'
        history = self.ctx.database(history_sql)
        port = self.runtime.start('api-server', 'v2-same-volume-restart', '--spring.profiles.active=local')
        assert self.mod.request(port, '/api/me')[0] == 401
        assert self.state() == before, 'Relations/audit changed on same-volume startup'
        assert self.ctx.database(history_sql) == history, 'Migration checksums/history changed'
        assert self.ctx.database(counts_sql) == counts, 'Entity/join counts changed after startup'
        return {'volume': self.ctx.manifest['volume'], 'relations_audit_history_unchanged': True, 'history': history.splitlines(), 'counts': counts.splitlines()}

    def browser_manage(self):
        original = self.present('user_role')
        try:
            self.mutation(LINKS['user_role'], False)
            return self.ctx.browser(mode='management')
        finally:
            self.mutation(LINKS['user_role'], original)

    def browser_faults(self):
        original = self.present('user_role')
        try:
            self.mutation(LINKS['user_role'], False)
            return self.ctx.browser(mode='management_faults')
        finally:
            self.mutation(LINKS['user_role'], original)

    def discovery(self, case):
        if case == 'manage':
            original = self.present('user_role')
            try:
                self.mutation(LINKS['user_role'], False)
                return self.ctx.browser(mode='discovery-' + case)
            finally:
                self.mutation(LINKS['user_role'], original)
        return self.ctx.browser(mode='discovery-' + case)

    def seed(self):
        expected = {'users': 5, 'roles': 4, 'policies': 5, 'resources': 5, 'user_roles': 4, 'role_policies': 7, 'policy_resources': 7}
        counts = {}
        for table in expected:
            counts[table] = int(self.ctx.database('SELECT COUNT(*) FROM ' + table + ';').strip())
        assert counts == expected, 'v2 exact seed count mismatch: ' + str(counts)
        history = [row.split('\t') for row in self.ctx.database('SELECT version,script,checksum,success FROM flyway_schema_history ORDER BY installed_rank;').splitlines()]
        assert [row[0] for row in history] == ['1', '2', '3', '4']
        assert all(row[3] == '1' for row in history)
        return {'counts': counts, 'history': history, 'audit_rows_existing': len(self.audit_rows())}

    def write_boundaries(self):
        before = self.state()
        for spec in LINKS.values():
            for method in ['PUT', 'DELETE']:
                for headers in [{'Origin': 'https://evil.invalid'}, {'Origin': f'http://127.0.0.1:{self.runtime.api}'}, {'Host': 'evil.invalid'}]:
                    status, _, _ = self.mod.request(self.runtime.api, spec['path'], self.token('api-admin-manage'), extra=headers, method=method)
                    assert status == 403, f'Write Origin/Host expected403 got{status}'
                self.api(spec['path'], 'mcp-admin', method, 401)
        assert self.mod.rpc(self.runtime.mcp, self.token('api-admin-manage'), 'tools/list')[0] == 401
        assert self.state() == before
        return {'new_put_delete_origin_host': True, 'cross_audience': True, 'unchanged': True}

    def audit_rollback(self):
        # Authorized failure injection in a new TESTER database only. No app endpoint.
        assert self.ctx.manifest.get('isolated_mutations') is True
        table = self.ctx.manifest['audit_schema']['table']
        assert re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', table)
        constraint = 'tester_reject_audit_v2'
        exists = self.ctx.database("SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='" + constraint + "';")
        assert exists.strip() == '0', 'Refuse to replace preexisting audit constraint'
        original = self.present('user_role')
        self.mutation(LINKS['user_role'], False)
        before = self.state()
        cutoff = self.ctx.database("SELECT DATE_FORMAT(CURRENT_TIMESTAMP(6),'%Y-%m-%d %H:%i:%s.%f');").strip()
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}', cutoff), 'Invalid server timestamp'
        assert self.ctx.database("SELECT COUNT(*) FROM `" + table + "` WHERE occurred_at > TIMESTAMP '" + cutoff + "';").strip() == '0', 'Clock moved behind existing audit rows'
        created = False
        try:
            # Existing rows satisfy the constant cutoff; subsequent audit inserts fail.
            # CHECK needs only scoped ALTER privilege, unlike binlog-restricted triggers.
            self.ctx.database('ALTER TABLE `' + table + '` ADD CONSTRAINT ' + constraint +
                              " CHECK (occurred_at <= TIMESTAMP '" + cutoff + "');")
            created = True
            self.error('assign_role_to_user', LINKS['user_role']['args'], 'mcp-admin', {'INTERNAL_ERROR', 'UPSTREAM_UNAVAILABLE'})
            assert self.state() == before, 'Audit failure did not roll back link mutation'
        finally:
            if created:
                self.ctx.database('ALTER TABLE `' + table + '` DROP CHECK ' + constraint + ';')
                # Same operation must now succeed, isolating the injected audit failure.
                try:
                    assert self.mutation(LINKS['user_role'], True)['changed'] is True
                finally:
                    self.mutation(LINKS['user_role'], original)
            else:
                self.mutation(LINKS['user_role'], original)
        return {'injection': 'test-owned MySQL CHECK rejects audit timestamps after server cutoff',
                'cutoff': cutoff, 'existing_audit_rows_preserved': len(before['audit']),
                'link_and_audit_rollback': True, 'constraint_removed': True, 'same_write_succeeded_after_removal': True}

    def mixed_concurrency(self):
        results = []
        for kind, spec in LINKS.items():
            original = self.present(kind)
            try:
                self.mutation(spec, False)
                audit_before = len(self.audit_rows())
                responses, evidence = observed_overlap(self.ctx, [spec['add'], spec['remove']], lambda name: self.call(name, spec['args']))
                rows = self.audit_rows()[audit_before:]
                state = replay_audit(responses, rows, spec, False)
                assert self.present(kind) is state, 'Final DB state differs from serialized audit order'
                assert all(r['relationship'] == spec['args'] for r in responses)
                results.append({'kind': kind, 'responses': responses, 'audit': rows, 'final_present': state, 'overlap': evidence})
            finally:
                self.mutation(spec, original)
        return results
