import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../chat.js', import.meta.url), 'utf8');
const { classifyQuestion, catalogResult, requestableResult, managementArgs, mutationResult, rpcResult, toolData, accessResult, resourceResult, conceptResult, resolveResourceName, validateTools, accessText, readResponse } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const asTool = data => ({ content: [{ type: 'text', text: JSON.stringify(data) }], isError: false });
const pair = (roleId, policyId) => ({ roleId, policyId });
const alice = () => ({
  user: { id: 'alice', name: 'Alice', description: 'Alice only', roleIds: ['editor', 'auditor'] },
  roles: [
    { id: 'editor', name: '편집자', description: '편집 역할', policyIds: ['read', 'write'] },
    { id: 'auditor', name: '감사자', description: '감사 역할', policyIds: ['read'] },
  ],
  policies: [
    { id: 'read', name: '열람 정책', description: '읽기', resourceIds: ['R100'], grantedByRoleIds: ['editor', 'auditor'] },
    { id: 'write', name: '편집 정책', description: '쓰기', resourceIds: ['R100', 'R200'], grantedByRoleIds: ['editor'] },
  ],
  resources: [
    { id: 'R100', name: '운영 대시보드', description: '운영 현황', grants: [pair('editor', 'read'), pair('auditor', 'read'), pair('editor', 'write')] },
    { id: 'R200', name: '문서 저장소', description: '문서', grants: [pair('editor', 'write')] },
  ],
});
const bob = () => ({
  user: { id: 'bob', name: 'Bob', description: 'Bob only', roleIds: ['viewer'] },
  roles: [{ id: 'viewer', name: '뷰어', description: '', policyIds: ['audit'] }],
  policies: [{ id: 'audit', name: '감사 정책', description: '', resourceIds: ['R300'], grantedByRoleIds: ['viewer'] }],
  resources: [{ id: 'R300', name: '감사 보고서', description: '', grants: [pair('viewer', 'audit')] }],
});
const carol = () => ({ user: { id: 'carol', name: 'Carol', description: '역할 없음', roleIds: [] }, roles: [], policies: [], resources: [] });
const accessTool = () => asTool(alice());
const allowed = () => ({ resourceId: 'R100', allowed: true, resource: { id: 'R100', name: '운영 대시보드', description: '운영 현황' }, grants: alice().resources[0].grants });
const denied = id => ({ resourceId: id, allowed: false, resource: null, grants: [] });
const payload = (id = 1, result = accessTool()) => ({ jsonrpc: '2.0', id, result });
const response = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
const fails = (fn, code = 'PROTOCOL') => assert.throws(fn, error => error.code === code);
const mutationCases = [
  ['assign_role_to_user', ['userId', 'roleId'], ['U200', 'reader'], true],
  ['remove_role_from_user', ['userId', 'roleId'], ['U200', 'reader'], false],
  ['attach_policy_to_role', ['roleId', 'policyId'], ['reader', 'read'], true],
  ['detach_policy_from_role', ['roleId', 'policyId'], ['reader', 'read'], false],
  ['attach_resource_to_policy', ['policyId', 'resourceId'], ['read', 'R100'], true],
  ['detach_resource_from_policy', ['policyId', 'resourceId'], ['read', 'R100'], false],
];
const toolList = () => {
  const definition = (name, properties, required = Object.keys(properties), mutation = false) => ({ name,
    inputSchema: { type: 'object', properties, required, additionalProperties: false },
    annotations: { readOnlyHint: !mutation, destructiveHint: mutation, idempotentHint: true },
  });
  const id = { type: 'string', pattern: '^[A-Za-z0-9_-]{1,64}$' };
  const resource = { type: 'string', pattern: '^R[0-9]{3}$' };
  const entity = { type: 'string', enum: ['users', 'roles', 'policies', 'resources'] };
  return { tools: [
    definition('get_my_access', {}), definition('check_my_resource_access', { resourceId: resource }),
    definition('explain_authorization', { concept: { type: 'string', enum: ['overview', 'permission', 'role', 'policy', 'resource'] } }),
    definition('list_access_entities', { entity }), definition('get_access_entity', { entity, id }), definition('get_requestable_roles', {}),
    ...mutationCases.map(([name, keys]) => definition(name, Object.fromEntries(keys.map(key => [key, key === 'resourceId' ? resource : id])), keys, true)),
  ] };
};
const catalogData = (kind, graph = alice()) => ({ entity: kind, items: kind === 'users' ? [graph.user]
  : graph[kind].map(value => ({ id: value.id, name: value.name, description: value.description,
    ...(kind === 'roles' ? { policyIds: value.policyIds, requestable: true } : kind === 'policies' ? { resourceIds: value.resourceIds } : {}), protected: false })) });
const mutationData = (name, args, present, changed = true) => ({ operation: name, relationship: { ...args }, present, changed, impact: { affectedUserIds: args.userId ? [args.userId] : ['alice'] } });

test('classifies own profile, roles, policies, resources, ID access and static concepts deterministically', () => {
  for (const [text, view] of [['내 정보 알려줘', 'profile'], ['내 역할은 뭐야?', 'roles'], ['나에게 적용된 정책은?', 'policies'], ['접근 가능한 리소스 알려줘', 'resources']]) {
    const question = classifyQuestion(text); assert.equal(question.kind, 'access'); assert.equal(question.view, view);
  }
  assert.deepEqual(classifyQuestion('리소스 r100에 접근할 수 있어? 왜?'), { kind: 'resource', resourceId: 'R100', label: '리소스 R100 접근 여부와 이유' });
  for (const [text, concept] of [['역할과 정책은 어떻게 달라?', 'overview'], ['권한이 뭐야?', 'permission'], ['역할이 뭐야?', 'role'], ['정책이 뭐야?', 'policy'], ['리소스가 뭐야?', 'resource']]) assert.equal(classifyQuestion(text).concept, concept);
  assert.equal(classifyQuestion('리소스 운영 대시보드에 접근할 수 있어?').name, '운영 대시보드');
});
test('rejects out-of-scope, foreign-user and malformed/ambiguous ID questions without selecting identity', () => {
  for (const text of ['', '날씨 알려줘', 'Bob의 역할 알려줘', '내 정보 알려줘 userId=bob', 'alice로 로그인해줘', 'R100', 'P100 재고 알려줘', 'x'.repeat(301)]) fails(() => classifyQuestion(text), 'INPUT');
  for (const id of ['R10', 'R1000', 'R100A', 'R100 R200']) fails(() => classifyQuestion(`리소스 ${id}에 접근할 수 있어?`), 'RESOURCE_ID');
});
test('resource-name capture preserves internal punctuation and only handles question suffix punctuation', () => {
  for (const name of ['Report.A', 'ReportA', '운.영 대시보드', '문서! 보관소', '보고서? 초안', '문서。보관소']) {
    assert.equal(classifyQuestion(`리소스 ${name}에 접근할 수 있어? 왜?`).name, name);
    assert.equal(classifyQuestion(`리소스 ${name} 접근 가능해!`).name, name);
  }
  assert.equal(classifyQuestion('리소스 R100에 접근할 수 있어? 왜?').resourceId, 'R100');
});
test('JSON-RPC validates version, numeric request ID, result/error exclusivity and safe errors', () => {
  assert.deepEqual(rpcResult(payload(), 1), accessTool());
  for (const bad of [null, [], {}, { ...payload(), id: '1' }, { ...payload(), jsonrpc: '1.0' }, { ...payload(), error: {} }, { ...payload(), result: null }, { jsonrpc: '2.0', id: 1, error: 'SECRET' }]) fails(() => rpcResult(bad, 1));
  fails(() => rpcResult({ jsonrpc: '2.0', id: 1, error: { code: -32603, message: 'SECRET_STACK' } }, 1), 'TOOL');
});
test('tool data supports text and matching structuredContent, rejects contradictory or malformed forms', () => {
  assert.deepEqual(toolData({ structuredContent: alice() }), alice());
  assert.deepEqual(toolData({ ...accessTool(), structuredContent: alice() }), alice());
  for (const value of [{}, { content: [] }, { content: [null] }, { content: [{ type: 'text', text: '{' }] }, { ...accessTool(), isError: 'false' }, { ...accessTool(), structuredContent: bob() }, { content: [{ type: 'image', data: 'secret' }] }]) fails(() => toolData(value));
});
test('access graph accepts A/B and no-role user; preserves distinct provenance and strips extras', () => {
  for (const data of [alice(), bob(), carol()]) assert.deepEqual(accessResult(asTool(data)), data);
  const data = alice(); data.private = 'SECRET'; data.user.private = 'SECRET';
  assert.deepEqual(accessResult(asTool(data)), alice());
  const text = accessText(accessResult(asTool(data)), 'resources');
  assert.ok(text.includes('editor')); assert.ok(text.includes('auditor')); assert.ok(text.includes('read')); assert.ok(text.includes('write')); assert.equal(text.includes('SECRET'), false);
  for (const view of ['roles', 'policies', 'resources']) assert.ok(accessText(carol(), view).includes('없습니다'));
});
test('access graph rejects missing fields, duplicates, invalid types, dangling and missing provenance paths', () => {
  const mutations = [
    data => { delete data.user; }, data => { data.roles = null; }, data => { delete data.user.description; }, data => { data.user.roleIds.push('editor'); },
    data => { data.user.roleIds = ['auditor']; }, data => { data.roles.push(data.roles[0]); }, data => { data.roles[0].policyIds.push('foreign'); },
    data => { data.policies[0].grantedByRoleIds = ['editor']; }, data => { data.policies[0].grantedByRoleIds.push('foreign'); },
    data => { data.policies[1].resourceIds.push('R999'); }, data => { data.resources[0].grants.pop(); },
    data => { data.resources[0].grants.push(pair('foreign', 'read')); }, data => { data.resources[0].grants.push(data.resources[0].grants[0]); },
    data => { data.resources[0].id = 'URL'; }, data => { data.resources.push(data.resources[0]); },
    data => { data.resources[0].grants[0].roleId = null; }, data => { data.resources[0].name = ''; },
  ];
  for (const mutate of mutations) { const data = alice(); mutate(data); fails(() => accessResult(asTool(data))); }
});
test('resource results validate allow provenance and identical safe denial shape without existence leaks', () => {
  assert.deepEqual(resourceResult(asTool(allowed()), 'R100'), allowed());
  for (const id of ['R200', 'R999']) assert.deepEqual(resourceResult(asTool(denied(id)), id), denied(id));
  for (const data of [{ ...allowed(), resourceId: 'R200' }, { ...allowed(), grants: [] }, { ...allowed(), allowed: 'true' }, { ...allowed(), resource: null }, { ...denied('R100'), resource: { id: 'R100', name: 'SECRET', description: '' } }, { ...denied('R100'), grants: [pair('foreign', 'secret')] }]) fails(() => resourceResult(asTool(data), 'R100'));
});
test('name resolution accepts only unique exact currently accessible names', () => {
  assert.equal(resolveResourceName(alice(), '운영 대시보드'), 'R100');
  for (const name of ['운영', 'SECRET', '운영 대시보드 ']) fails(() => resolveResourceName(alice(), name), 'RESOURCE_NAME');
  const ambiguous = alice(); ambiguous.resources[1].name = ambiguous.resources[0].name;
  fails(() => resolveResourceName(ambiguous, '운영 대시보드'), 'RESOURCE_NAME');
  fails(() => resolveResourceName(bob(), '운영 대시보드'), 'RESOURCE_NAME');
});
test('static concepts validate requested concept/explanation and never become grant evidence', () => {
  assert.deepEqual(conceptResult(asTool({ concept: 'role', explanation: '역할 설명' }), 'role'), { concept: 'role', explanation: '역할 설명' });
  for (const data of [{ concept: 'policy', explanation: 'x' }, { concept: 'role', explanation: null }, { concept: 'role', explanation: '' }]) fails(() => conceptResult(asTool(data), 'role'));
});
test('required tools have strict empty/resourceId/concept schemas and no userId input', () => {
  validateTools(toolList());
  for (const mutate of [list => list.tools.pop(), list => list.tools.push(list.tools[0]), list => { list.tools[0].inputSchema.additionalProperties = true; }, list => { list.tools[0].inputSchema.properties.userId = { type: 'string' }; }, list => { list.tools[1].inputSchema.properties.resourceId.pattern = '.*'; }, list => { list.tools[2].inputSchema.properties.concept.enum = ['overview', 'overview', 'role', 'policy', 'resource']; }]) {
    const list = toolList(); mutate(list); fails(() => validateTools(list), 'MISSING_TOOL');
  }
});
test('Tool errors use safe allowlist and never expose raw error bodies', () => {
  for (const code of ['FORBIDDEN', 'INVALID_ARGUMENT', 'ACCESS_DENIED', 'UPSTREAM_ACCESS_DENIED', 'UPSTREAM_UNAVAILABLE', 'UPSTREAM_INVALID_RESPONSE']) fails(() => toolData({ isError: true, content: [{ type: 'text', text: code }] }), code);
  assert.throws(() => toolData({ isError: true, content: [{ type: 'text', text: 'SECRET_STACK' }] }), error => error.code === 'TOOL' && !error.message.includes('SECRET'));
});

test('HTTP JSON response parses matching result', async () => assert.deepEqual(await readResponse(response(payload()), 1), accessTool()));
test('SSE parses chunked UTF-8, comments, notification and multi-line data with CRLF', async () => {
  const raw = ': heartbeat\r\n\r\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\r\n\r\nevent: message\r\ndata: {"jsonrpc":"2.0",\r\ndata: "id":1,"result":{"label":"재고"}}\r\n\r\n';
  const bytes = new TextEncoder().encode(raw);
  const body = new ReadableStream({ start(controller) { for (let i = 0; i < bytes.length; i++) controller.enqueue(bytes.slice(i, i + 1)); controller.close(); } });
  assert.deepEqual(await readResponse(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }), 1), { label: '재고' });
});
test('SSE returns before endless stream closes and cancels reader', async () => {
  let cancelled = false;
  const body = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(payload())}\n\n`)); }, cancel() { cancelled = true; } });
  assert.deepEqual(await readResponse(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }), 1), accessTool());
  assert.equal(cancelled, true);
});
test('malformed, wrong-ID, invalid UTF-8 and unsupported content responses fail safely', async () => {
  const cases = [response({ ...payload(), id: 2 }), new Response('SECRET_STACK', { headers: { 'Content-Type': 'application/json' } }), new Response('data: {}\n\n', { headers: { 'Content-Type': 'text/event-stream' } }), new Response('<html>SECRET</html>', { headers: { 'Content-Type': 'text/html' } }), new Response(Uint8Array.of(0xff), { headers: { 'Content-Type': 'application/json' } })];
  for (const item of cases) await assert.rejects(readResponse(item, 1), error => error.code === 'PROTOCOL' && !error.message.includes('SECRET'));
});
test('valid JSON-RPC accepts exactly 128 KiB and rejects 128 KiB plus one byte', async () => {
  const limit = 128 * 1024;
  const overhead = Buffer.byteLength(JSON.stringify(payload(1, { padding: '' })));
  const atLimit = payload(1, { padding: 'x'.repeat(limit - overhead) });
  const overLimit = payload(1, { padding: 'x'.repeat(limit + 1 - overhead) });
  assert.equal(Buffer.byteLength(JSON.stringify(atLimit)), limit);
  assert.equal(Buffer.byteLength(JSON.stringify(overLimit)), limit + 1);
  assert.deepEqual(await readResponse(response(atLimit), 1), atLimit.result);
  await assert.rejects(readResponse(response(overLimit), 1), error => error.code === 'PROTOCOL');
});
test('response size limit accumulates across individually smaller chunks and cancels stream', async () => {
  const limit = 128 * 1024;
  const overhead = Buffer.byteLength(JSON.stringify(payload(1, { padding: '' })));
  const bytes = new TextEncoder().encode(JSON.stringify(payload(1, { padding: 'x'.repeat(limit + 1 - overhead) })));
  let offset = 0, cancelled = false, chunks = 0;
  const body = new ReadableStream({
    pull(controller) {
      if (offset >= bytes.length) return; // Keep open to observe cancellation.
      const end = Math.min(offset + 32768, bytes.length);
      controller.enqueue(bytes.slice(offset, end)); offset = end; chunks++;
    },
    cancel() { cancelled = true; },
  });
  await assert.rejects(readResponse(new Response(body, { headers: { 'Content-Type': 'application/json' } }), 1), error => error.code === 'PROTOCOL');
  assert.equal(offset, limit + 1); assert.equal(chunks, 5); assert.equal(cancelled, true);
});
test('unsupported MIME cancels an open response body without reading or exposing it', async () => {
  let cancelled = false;
  const body = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('SECRET_STACK')); }, cancel() { cancelled = true; } });
  await assert.rejects(readResponse(new Response(body, { headers: { 'Content-Type': 'text/html' } }), 1), error => error.code === 'PROTOCOL' && !error.message.includes('SECRET'));
  assert.equal(cancelled, true);
});

// Minimal DOM + fake transport: unit evidence only, never live MCP/browser evidence.
class Element {
  constructor() { this.value = ''; this.disabled = false; this.hidden = false; this.textContent = ''; this.dataset = {}; this.children = []; this.handlers = {}; }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  dispatch(type) { return this.handlers[type]?.({ preventDefault() {} }); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.textContent = ''; }
  set innerHTML(_value) { throw new Error('HTML injection is forbidden'); }
  setAttribute(name, value) { this.attributes ??= {}; this.attributes[name] = value; }
  scrollIntoView() {}
  focus() { this.focusCount = (this.focusCount || 0) + 1; }
  get text() { return this.textContent + this.children.map(child => child.text).join(''); }
}
function harness() {
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const examples = [new Element(), new Element()]; examples[0].dataset.query = '내 역할은 뭐야?'; examples[1].dataset.query = '접근 가능한 리소스 알려줘';
  const calls = [], timers = new Map(); let timerId = 0;
  const win = new Element();
  const context = vm.createContext({
    document: { getElementById: get, createElement: () => new Element(), querySelectorAll: () => examples }, window: win,
    AbortController, TextDecoder, setTimeout: fn => { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout: id => timers.delete(id),
    fetch: (url, options) => new Promise((resolve, reject) => calls.push({ url, options, body: JSON.parse(options.body), resolve, reject })),
  });
  vm.runInContext(source.replace(/^export /gm, ''), context);
  return { get, calls, timers, win, examples, text: () => get('messages').text };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
function answer(call, result) { call.resolve(response(payload(call.body.id, result))); }
async function connect(h, token = 'UNIT_TEST_TOKEN') {
  h.get('token').value = token;
  const done = h.get('connect-form').dispatch('submit');
  await tick(); answer(h.calls.at(-1), { protocolVersion: '2025-06-18', capabilities: {}, serverInfo: { name: 'test' } });
  await tick(); h.calls.at(-1).resolve(new Response(null, { status: 202 }));
  await tick(); answer(h.calls.at(-1), toolList());
  await done;
}
function query(h, text) { h.get('query').value = text; h.get('query-form').dispatch('submit'); return h.calls.at(-1); }
async function show(h, text, result) { const call = query(h, text); answer(call, result); await tick(); return call; }

test('connect performs exact MCP lifecycle at fixed endpoint with memory-only token and no user selector', async () => {
  const h = harness(); await connect(h);
  assert.deepEqual(h.calls.map(call => call.body.method), ['initialize', 'notifications/initialized', 'tools/list']);
  assert.equal(Object.hasOwn(h.calls[1].body, 'id'), false);
  assert.equal(h.get('token').value, ''); assert.equal(h.get('connection-state').textContent, '연결됨');
  for (const call of h.calls) {
    assert.equal(call.url, '/mcp'); assert.equal(call.options.redirect, 'error'); assert.equal(call.options.credentials, 'omit'); assert.equal(call.options.mode, 'same-origin');
    assert.equal(call.options.headers.Authorization, 'Bearer UNIT_TEST_TOKEN'); assert.equal(call.options.headers['MCP-Protocol-Version'], '2025-06-18');
  }
  assert.equal(h.text().includes('UNIT_TEST_TOKEN'), false); assert.equal(h.timers.size, 0);
});
test('profile/roles/policies/resources each call actual tool handler with empty identity-free arguments', async () => {
  const h = harness(); await connect(h);
  for (const question of ['내 정보 알려줘', '내 역할은 뭐야?', '나에게 적용된 정책은?', '접근 가능한 리소스 알려줘']) {
    const call = await show(h, question, accessTool());
    assert.equal(call.body.params.name, 'get_my_access'); assert.deepEqual(call.body.params.arguments, {});
    assert.ok(h.text().includes('Alice'));
  }
  assert.ok(h.text().includes('editor')); assert.ok(h.text().includes('auditor')); assert.ok(h.text().includes('R100'));
});
test('resource and concept questions choose correct tool and clearly distinguish concepts from grant evidence', async () => {
  const h = harness(); await connect(h);
  const call = await show(h, '리소스 R100에 접근할 수 있어? 왜?', asTool(allowed()));
  assert.deepEqual(call.body.params, { name: 'check_my_resource_access', arguments: { resourceId: 'R100' } });
  assert.ok(h.text().includes('editor → read → R100'));
  const concept = await show(h, '역할과 정책은 어떻게 달라?', asTool({ concept: 'overview', explanation: '정적 개념 설명' }));
  assert.deepEqual(concept.body.params, { name: 'explain_authorization', arguments: { concept: 'overview' } });
  assert.ok(h.text().includes('실제 권한 근거가 아닙니다'));
});
test('duplicate submissions are blocked and unsafe server text remains literal text', async () => {
  const h = harness(); await connect(h); const call = query(h, '내 정보 알려줘'); query(h, '내 정보 알려줘');
  assert.equal(h.calls.length, 4); assert.equal(h.get('send').disabled, true);
  const data = alice(); data.user.name = '<img src=x onerror=alert(1)>';
  answer(call, asTool(data)); await tick();
  assert.ok(h.text().includes('<img src=x onerror=alert(1)>')); assert.equal(h.get('send').disabled, false);
});
test('unsupported question or pasted token is never sent or echoed', async () => {
  const h = harness(); await connect(h); query(h, 'SECRET_TOKEN');
  assert.equal(h.calls.length, 3); assert.equal(h.text().includes('SECRET_TOKEN'), false); assert.ok(h.text().includes('지원하는 질문'));
});
test('name lookup fetches current access; unique name calls ID tool; ambiguous/unknown name never guesses', async () => {
  const h = harness(); await connect(h);
  const first = query(h, '리소스 운영 대시보드에 접근할 수 있어?');
  assert.equal(first.body.params.name, 'get_my_access'); answer(first, accessTool()); await tick();
  assert.deepEqual(h.calls.at(-1).body.params, { name: 'check_my_resource_access', arguments: { resourceId: 'R100' } });
  answer(h.calls.at(-1), asTool(allowed())); await tick();
  for (const duplicate of [false, true]) {
    const data = duplicate ? alice() : bob(); if (duplicate) data.resources[1].name = data.resources[0].name;
    const count = h.calls.length; await show(h, '리소스 운영 대시보드에 접근할 수 있어?', asTool(data));
    assert.equal(h.calls.length, count + 1); assert.ok(h.text().includes('리소스 ID로 질문'));
  }
});
test('punctuated accessible names resolve exactly; punctuation variants never invoke the check tool', async () => {
  const h = harness(); await connect(h);
  const data = alice(); data.resources[0].name = 'Report.A'; data.resources[1].name = 'ReportA';
  for (const [name, id] of [['Report.A', 'R100'], ['ReportA', 'R200']]) {
    const first = query(h, `리소스 ${name}에 접근할 수 있어? 왜?`);
    assert.equal(first.body.params.name, 'get_my_access'); answer(first, asTool(data)); await tick();
    assert.deepEqual(h.calls.at(-1).body.params, { name: 'check_my_resource_access', arguments: { resourceId: id } });
    answer(h.calls.at(-1), asTool(denied(id))); await tick();
  }
  for (const [name, available] of [['운.영 대시보드', alice()], ['Report!A', data], ['Report?A', data], ['Report。A', data]]) {
    const count = h.calls.length;
    await show(h, `리소스 ${name}에 접근할 수 있어? 왜?`, asTool(available));
    assert.equal(h.calls.length, count + 1);
    assert.equal(h.calls.at(-1).body.params.name, 'get_my_access');
    assert.ok(h.text().includes('리소스 ID로 질문'));
  }
});
test('token change during initialize clears timer and late old response cannot reconnect', async () => {
  const h = harness(); h.get('token').value = 'OLD'; const pending = h.get('connect-form').dispatch('submit');
  const old = h.calls[0]; h.get('token').value = 'NEW'; h.get('token').dispatch('input');
  assert.equal(old.options.signal.aborted, true); assert.equal(h.timers.size, 0); assert.equal(h.get('token').value, 'NEW'); assert.equal(h.text(), '');
  answer(old, { protocolVersion: '2025-06-18', capabilities: {}, serverInfo: {} }); await pending;
  assert.equal(h.calls.length, 1); assert.equal(h.get('connection-state').textContent, '연결 안 됨');
});
test('Alice to Bob disconnect/reconnect clears display, isolates late Alice response and name resolution', async () => {
  const h = harness(); await connect(h, 'ALICE'); await show(h, '내 정보 알려줘', accessTool());
  const old = query(h, '접근 가능한 리소스 알려줘'); assert.ok(h.text().includes('Alice'));
  h.get('disconnect').dispatch('click'); assert.equal(h.text(), ''); assert.equal(h.timers.size, 0); assert.equal(h.get('token').value, '');
  await connect(h, 'BOB'); answer(old, accessTool()); await tick();
  assert.equal(h.text().includes('Alice'), false); assert.equal(h.text().includes('운영 대시보드'), false);
  const fresh = await show(h, '내 정보 알려줘', asTool(bob()));
  assert.equal(fresh.options.headers.Authorization, 'Bearer BOB'); assert.ok(h.text().includes('Bob')); assert.equal(h.text().includes('Alice'), false);
  const count = h.calls.length; await show(h, '리소스 운영 대시보드에 접근할 수 있어?', asTool(bob()));
  assert.equal(h.calls.length, count + 1); assert.ok(h.text().includes('리소스 ID로 질문'));
});
test('token edit clears prior authorization display and fences pending name lookup across account switch', async () => {
  const h = harness(); await connect(h, 'ALICE'); await show(h, '내 역할은 뭐야?', accessTool());
  const old = query(h, '리소스 운영 대시보드에 접근할 수 있어?');
  h.get('token').value = 'BOB'; h.get('token').dispatch('input'); assert.equal(h.text(), ''); assert.equal(old.options.signal.aborted, true);
  await connect(h, 'BOB'); const count = h.calls.length; answer(old, accessTool()); await tick();
  assert.equal(h.calls.length, count); assert.equal(h.text().includes('Alice'), false);
});
test('Carol with valid tool scope still renders no domain grants and safe denial', async () => {
  const h = harness(); await connect(h, 'CAROL');
  await show(h, '접근 가능한 리소스 알려줘', asTool(carol())); assert.ok(h.text().includes('접근 가능한 리소스가 없습니다'));
  await show(h, '리소스 R100에 접근할 수 있어?', asTool(denied('R100'))); assert.ok(h.text().includes('존재 여부는 알 수 없습니다'));
});
test('cancel and timeout suppress late authorization success and allow next query', async () => {
  for (const timeout of [false, true]) {
    const h = harness(); await connect(h); const call = query(h, '내 정보 알려줘');
    if (timeout) [...h.timers.values()][0](); else h.get('cancel').dispatch('click');
    assert.equal(call.options.signal.aborted, true); answer(call, accessTool()); await tick();
    assert.equal(h.text().includes('Alice'), false); assert.ok(h.text().includes(timeout ? '12초' : '취소')); assert.equal(h.get('send').disabled, false); assert.equal(h.timers.size, 0);
  }
});
test('401 clears all prior-user data/token; Tool FORBIDDEN is safe and distinct', async () => {
  const h = harness(); await connect(h); await show(h, '내 정보 알려줘', accessTool());
  await show(h, '내 역할은 뭐야?', { isError: true, content: [{ type: 'text', text: 'FORBIDDEN' }] });
  assert.ok(h.text().includes('요청 권한이 부족')); assert.equal(h.get('connection-state').textContent, '연결됨');
  const call = query(h, '내 역할은 뭐야?'); call.resolve(new Response('SECRET_STACK', { status: 401 })); await tick();
  assert.equal(h.text(), ''); assert.ok(h.get('connection-note').textContent.includes('인증할 수 없습니다'));
  assert.equal(h.get('connection-state').textContent, '연결 안 됨'); assert.equal(h.get('token').value, '');
});
test('network, HTTP 403 and malformed protocol failures are distinct during connect', async () => {
  for (const failure of ['network', 'forbidden', 'malformed']) {
    const h = harness(); h.get('token').value = 'TOKEN'; const done = h.get('connect-form').dispatch('submit');
    if (failure === 'network') h.calls[0].reject(new TypeError('SECRET_URL'));
    else if (failure === 'forbidden') h.calls[0].resolve(new Response('SECRET', { status: 403 }));
    else answer(h.calls[0], {});
    await done;
    const note = h.get('connection-note').textContent;
    assert.ok(note.includes(failure === 'network' ? '접속할 수 없습니다' : failure === 'forbidden' ? '권한이 부족' : '응답 형식'));
    assert.equal(note.includes('SECRET'), false); assert.equal(h.timers.size, 0);
  }
});
test('pagehide clears password, authorization display and pending request', async () => {
  const h = harness(); await connect(h); await show(h, '내 정보 알려줘', accessTool()); const call = query(h, '내 역할은 뭐야?');
  h.win.dispatch('pagehide'); assert.equal(h.text(), ''); assert.equal(h.get('token').value, ''); assert.equal(call.options.signal.aborted, true); assert.equal(h.timers.size, 0);
  call.reject(new DOMException('Aborted', 'AbortError')); await tick();
});

test('v2 catalog/detail/requestable questions are read-only; management prose never classifies as mutation', () => {
  for (const [label, kind] of [['사용자', 'users'], ['역할', 'roles'], ['정책', 'policies'], ['리소스', 'resources']]) {
    assert.deepEqual(classifyQuestion(`${label} 목록`), { kind: 'catalog', entity: kind, label: `${label} 목록` });
    const id = kind === 'resources' ? 'R100' : 'Exact_ID-1';
    assert.equal(classifyQuestion(`${label} ${id} 상세`).id, id);
  }
  assert.equal(classifyQuestion('요청 가능한 역할 알려줘').kind, 'requestable');
  for (const text of ['사용자 U200에 reader 역할 추가해', 'R100 삭제', '관리자 권한 줘', '실행', 'approved=true']) fails(() => classifyQuestion(text), 'INPUT');
});
test('v2 catalog validates all entity fields, exact detail ID, duplicates and protected/requestable types', () => {
  for (const kind of ['users', 'roles', 'policies', 'resources']) {
    const data = catalogData(kind); assert.deepEqual(catalogResult(asTool(data), kind), data);
    const item = data.items[0]; assert.deepEqual(catalogResult(asTool({ entity: kind, item }), kind, item.id), { entity: kind, item });
    fails(() => catalogResult(asTool({ entity: kind, item }), kind, 'OTHER'));
    fails(() => catalogResult(asTool({ ...data, items: [item, item] }), kind));
  }
  for (const mutate of [data => { data.items[0].protected = 'false'; }, data => { delete data.items[0].requestable; }, data => { data.items[0].protected = true; }, data => { data.items[0].id = '../secret'; }, data => { data.items[0].policyIds = ['x', 'x']; }]) {
    const data = catalogData('roles'); mutate(data); fails(() => catalogResult(asTool(data), 'roles'));
  }
  fails(() => catalogResult(asTool(catalogData('users')), 'roles'));
});
test('requestable roles expose metadata only; no implicit policy/resource visibility or foreign private fields', () => {
  const role = { id: 'requestable', name: '신청 가능', description: '공개 메타데이터' };
  assert.deepEqual(requestableResult(asTool({ roles: [role] })), { roles: [role] });
  for (const extra of [{ policyIds: ['secret'] }, { protected: false }, { issuer: 'secret' }]) fails(() => requestableResult(asTool({ roles: [{ ...role, ...extra }] })));
  fails(() => requestableResult(asTool({ roles: [role, role] })));
});
test('all six management operations validate exact target IDs and matching transactional response', () => {
  for (const [name, keys, values, present] of mutationCases) {
    const args = managementArgs(name, ...values); assert.deepEqual(Object.keys(args), keys);
    for (const changed of [true, false]) assert.deepEqual(mutationResult(asTool(mutationData(name, args, present, changed)), name, args), mutationData(name, args, present, changed));
    for (const mutate of [data => { data.operation = 'wrong'; }, data => { data.present = !present; }, data => { data.changed = 'true'; }, data => { data.relationship.approved = true; }, data => { data.relationship[keys[0]] = 'OTHER'; }, data => { data.impact.affectedUserIds = ['U100', 'U100']; }]) {
      const data = mutationData(name, args, present); mutate(data); fails(() => mutationResult(asTool(data), name, args));
    }
    fails(() => managementArgs(name, ' ../x ', values[1]), 'TARGET');
  }
  fails(() => managementArgs('toString', 'x', 'y'), 'TARGET');
  fails(() => managementArgs('assign_role_to_user', 'x'.repeat(65), 'reader'), 'TARGET');
  fails(() => managementArgs('attach_resource_to_policy', 'read', 'r100'), 'TARGET');
});
test('v2 schemas reject arbitrary target/approved keys and wrong mutation annotations', () => {
  assert.equal(toolList().tools.length, 12); validateTools(toolList());
  for (const mutate of [list => { list.tools[6].inputSchema.properties.approved = { type: 'boolean' }; }, list => { list.tools[6].annotations.readOnlyHint = true; }, list => { list.tools[6].annotations.destructiveHint = false; }, list => { list.tools[6].annotations.idempotentHint = false; }, list => { list.tools[4].inputSchema.properties.id.pattern = '.*'; }, list => { list.tools[3].inputSchema.properties.entity.enum.push('private'); }]) {
    const list = toolList(); mutate(list); fails(() => validateTools(list), 'MISSING_TOOL');
  }
});

async function previewManagement(h, entry = mutationCases[0], actor = alice()) {
  h.get('manage-operation').value = entry[0]; h.get('manage-operation').dispatch('change');
  h.get('manage-first').value = entry[2][0]; h.get('manage-first').dispatch('input');
  h.get('manage-second').value = entry[2][1]; h.get('manage-second').dispatch('input');
  const done = h.get('manage-form').dispatch('submit');
  const call = h.calls.at(-1); assert.equal(call.body.params.name, 'get_my_access');
  answer(call, asTool(actor)); await done;
  return call;
}
async function answerRefresh(h, graph = alice()) {
  await tick();
  const call = h.calls.at(-1); assert.equal(call.body.params.name, 'get_my_access'); answer(call, asTool(graph)); await tick();
  for (const kind of ['users', 'roles', 'policies', 'resources']) {
    const list = h.calls.at(-1); assert.deepEqual(list.body.params, { name: 'list_access_entities', arguments: { entity: kind } });
    answer(list, asTool(catalogData(kind, graph))); await tick();
  }
  const roles = h.calls.at(-1); assert.equal(roles.body.params.name, 'get_requestable_roles'); answer(roles, asTool({ roles: [] })); await tick();
}
const mutationCount = h => h.calls.filter(call => mutationCases.some(([name]) => call.body.params?.name === name)).length;

test('new read UI questions send only exact read tool arguments and safe NOT_FOUND', async () => {
  const h = harness(); await connect(h);
  for (const [label, kind] of [['사용자', 'users'], ['역할', 'roles'], ['정책', 'policies'], ['리소스', 'resources']]) {
    const data = catalogData(kind);
    const list = await show(h, `${label} 목록`, asTool(data)); assert.deepEqual(list.body.params, { name: 'list_access_entities', arguments: { entity: kind } });
    const detail = await show(h, `${label} ${data.items[0].id} 상세`, asTool({ entity: kind, item: data.items[0] }));
    assert.deepEqual(detail.body.params, { name: 'get_access_entity', arguments: { entity: kind, id: data.items[0].id } });
  }
  const request = await show(h, '요청 가능한 역할 알려줘', asTool({ roles: [] })); assert.deepEqual(request.body.params, { name: 'get_requestable_roles', arguments: {} });
  await show(h, '사용자 SECRET 상세', { isError: true, content: [{ type: 'text', text: 'NOT_FOUND' }] });
  assert.ok(h.text().includes('없는 대상은 구분하지 않습니다')); assert.equal(mutationCount(h), 0);
});
test('preview uses verified local ID independent of token subject; submit/Enter and text never execute', async () => {
  const h = harness(); await connect(h, 'SUBJECT_LOCAL_ID_DEMO');
  query(h, '사용자 U200에 reader 역할 추가해'); assert.equal(h.calls.length, 3);
  const actor = alice(); actor.user.id = 'U900';
  await previewManagement(h, mutationCases[0], actor);
  assert.ok(h.get('manage-actor').textContent.includes('U900')); assert.equal(h.get('manage-execute').disabled, false);
  const done = h.get('manage-form').dispatch('submit'); answer(h.calls.at(-1), asTool(actor)); await done;
  assert.equal(mutationCount(h), 0);
  const html = await readFile(new URL('../chat.html', import.meta.url), 'utf8');
  assert.match(html, /id="manage-execute" type="button"/);
  assert.ok(html.indexOf('</form>', html.indexOf('id="manage-form"')) < html.indexOf('id="manage-execute"'));
});
test('all management operations require explicit click, send once and refresh current graph/catalog/requestable roles', async () => {
  for (const entry of mutationCases) {
    const h = harness(); await connect(h); await previewManagement(h, entry);
    const done = h.get('manage-execute').dispatch('click'); h.get('manage-execute').dispatch('click'); h.get('manage-form').dispatch('submit');
    const call = h.calls.at(-1), args = Object.fromEntries(entry[1].map((key, index) => [key, entry[2][index]]));
    assert.deepEqual(call.body.params, { name: entry[0], arguments: args }); assert.equal(mutationCount(h), 1);
    assert.equal(h.get('manage-execute').disabled, true); assert.equal(h.get('manage-preview').hidden, true);
    answer(call, asTool(mutationData(entry[0], args, entry[3], false)));
    await answerRefresh(h); await done;
    assert.equal(mutationCount(h), 1); assert.ok(h.text().includes('변경 없음')); assert.ok(h.text().includes('처리 시점 관계'));
    assert.equal(h.get('manage-execute').disabled, true); assert.ok(h.get('manage-status').textContent.includes('최신 상태를 조회했습니다'));
  }
});
test('target/operation edits or preview cancellation invalidate snapshot, including edits without input events', async () => {
  for (const mode of ['first', 'second', 'operation', 'silent', 'cancel']) {
    const h = harness(); await connect(h); await previewManagement(h);
    if (mode === 'cancel') h.get('manage-cancel').dispatch('click');
    else {
      const id = mode === 'operation' ? 'manage-operation' : mode === 'second' ? 'manage-second' : 'manage-first';
      h.get(id).value = mode === 'operation' ? 'remove_role_from_user' : 'OTHER';
      if (mode !== 'silent') h.get(id).dispatch(mode === 'operation' ? 'change' : 'input');
    }
    const count = h.calls.length; h.get('manage-execute').dispatch('click');
    assert.equal(h.calls.length, count); assert.equal(mutationCount(h), 0); assert.equal(h.get('manage-preview').hidden, true);
  }
});
test('read question clears pending management preview without executing it', async () => {
  const h = harness(); await connect(h); await previewManagement(h);
  await show(h, '내 정보 알려줘', accessTool()); h.get('manage-execute').dispatch('click');
  assert.equal(mutationCount(h), 0); assert.equal(h.get('manage-preview').hidden, true);
});
test('account changes discard pending preview and late actor lookup cannot restore it', async () => {
  const h = harness(); await connect(h, 'ALICE'); await previewManagement(h);
  h.get('token').value = 'BOB'; h.get('token').dispatch('input');
  assert.equal(h.get('manage-preview').hidden, true); assert.equal(h.get('manage-first').value, ''); assert.equal(h.get('manage-actor').textContent, '');
  await connect(h, 'BOB'); h.get('manage-execute').dispatch('click'); assert.equal(mutationCount(h), 0);
  h.get('manage-first').value = 'U200'; h.get('manage-second').value = 'reader';
  const done = h.get('manage-form').dispatch('submit'), old = h.calls.at(-1);
  h.get('disconnect').dispatch('click'); await connect(h, 'CAROL'); answer(old, asTool(bob())); await done;
  assert.equal(h.get('manage-preview').hidden, true); assert.equal(h.get('manage-actor').textContent, ''); assert.equal(mutationCount(h), 0);
});
test('missing read scope prevents actor preview without guessing JWT identity or sending mutation', async () => {
  const h = harness(); await connect(h, 'MANAGE_ONLY');
  h.get('manage-first').value = 'U200'; h.get('manage-second').value = 'reader';
  const done = h.get('manage-form').dispatch('submit'); answer(h.calls.at(-1), { isError: true, content: [{ type: 'text', text: 'FORBIDDEN' }] }); await done;
  assert.ok(h.get('manage-status').textContent.includes('authz.read')); assert.equal(h.get('manage-execute').disabled, true);
  h.get('manage-execute').dispatch('click'); assert.equal(mutationCount(h), 0);
});
test('transmitted cancellation/timeout/network/malformed/oversized responses report unknown and only retry reads', async () => {
  for (const mode of ['cancel', 'timeout', 'network', 'malformed', 'oversized', 'tool-denied']) {
    const h = harness(); await connect(h); await previewManagement(h);
    const done = h.get('manage-execute').dispatch('click'), call = h.calls.at(-1);
    if (mode === 'cancel') { h.get('manage-cancel').dispatch('click'); call.reject(new DOMException('Aborted', 'AbortError')); }
    else if (mode === 'timeout') { [...h.timers.values()][0](); call.reject(new DOMException('Aborted', 'AbortError')); }
    else if (mode === 'network') call.reject(new TypeError('SECRET_NETWORK'));
    else if (mode === 'malformed') answer(call, { content: [{ type: 'text', text: '{SECRET' }] });
    else if (mode === 'oversized') answer(call, { padding: 'x'.repeat(128 * 1024) });
    else answer(call, { isError: true, content: [{ type: 'text', text: 'FORBIDDEN' }] });
    await tick(); assert.ok(h.text().includes('결과 미확인')); assert.ok(h.text().includes('롤백이 아니며')); assert.equal(h.text().includes('SECRET'), false);
    await answerRefresh(h); await done;
    assert.equal(mutationCount(h), 1); assert.equal(h.get('manage-execute').disabled, true); assert.equal(h.timers.size, 0);
  }
});
test('mutation 401 clears account data and says outcome unknown with unavailable refresh', async () => {
  const h = harness(); await connect(h); await previewManagement(h);
  const done = h.get('manage-execute').dispatch('click'), count = h.calls.length;
  h.calls.at(-1).resolve(new Response('SECRET', { status: 401 })); await done;
  assert.equal(h.calls.length, count); assert.equal(h.get('connection-state').textContent, '연결 안 됨'); assert.equal(h.text(), '');
  assert.ok(h.get('connection-note').textContent.includes('결과 미확인')); assert.ok(h.get('connection-note').textContent.includes('재조회하지 못했습니다'));
  assert.equal(h.get('manage-first').value, ''); assert.equal(h.get('manage-preview').hidden, true);
});
test('late transmitted mutation after account switch cannot render or refresh under the new account', async () => {
  const h = harness(); await connect(h, 'ALICE'); await previewManagement(h);
  const done = h.get('manage-execute').dispatch('click'), old = h.calls.at(-1);
  h.get('token').value = 'BOB'; h.get('token').dispatch('input'); assert.ok(h.get('connection-note').textContent.includes('결과 미확인'));
  await connect(h, 'BOB'); const count = h.calls.length;
  answer(old, asTool(mutationData('assign_role_to_user', { userId: 'U200', roleId: 'reader' }, true))); await done;
  assert.equal(h.calls.length, count); assert.equal(h.text().includes('변경됨'), false); assert.equal(h.get('manage-actor').textContent, '');
  h.get('manage-execute').dispatch('click'); assert.equal(mutationCount(h), 1);
});
test('refresh denied after success reports read limitation without resending mutation', async () => {
  const h = harness(); await connect(h); await previewManagement(h);
  const done = h.get('manage-execute').dispatch('click'); answer(h.calls.at(-1), asTool(mutationData('assign_role_to_user', { userId: 'U200', roleId: 'reader' }, true)));
  await tick(); assert.equal(h.calls.at(-1).body.params.name, 'get_my_access');
  answer(h.calls.at(-1), { isError: true, content: [{ type: 'text', text: 'FORBIDDEN' }] }); await done;
  assert.ok(h.text().includes('변경됨')); assert.ok(h.get('manage-status').textContent.includes('최신 상태를 확인하지 못했습니다')); assert.equal(mutationCount(h), 1);
});
test('unknown mutation remains unknown if follow-up read becomes unauthorized or account changes', async () => {
  for (const mode of ['unauthorized', 'disconnect']) {
    const h = harness(); await connect(h); await previewManagement(h);
    const done = h.get('manage-execute').dispatch('click'); h.calls.at(-1).reject(new TypeError('network'));
    await tick(); const refresh = h.calls.at(-1); assert.equal(refresh.body.params.name, 'get_my_access');
    if (mode === 'unauthorized') refresh.resolve(new Response(null, { status: 401 }));
    else { h.get('disconnect').dispatch('click'); refresh.reject(new DOMException('Abort', 'AbortError')); }
    await done;
    assert.ok(h.get('connection-note').textContent.includes('결과 미확인')); assert.equal(mutationCount(h), 1);
  }
});
test('mutation refresh rejects different actor-local-ID data and does not render it', async () => {
  const h = harness(); await connect(h); await previewManagement(h);
  const done = h.get('manage-execute').dispatch('click'); answer(h.calls.at(-1), asTool(mutationData('assign_role_to_user', { userId: 'U200', roleId: 'reader' }, true)));
  await tick(); const count = h.calls.length; answer(h.calls.at(-1), asTool(bob())); await done;
  assert.equal(h.calls.length, count); assert.equal(h.text().includes('Bob'), false); assert.ok(h.get('manage-status').textContent.includes('최신 상태를 확인하지 못했습니다'));
});

test('discovery guidance works before connection: every purpose prompts token focus without a request', async () => {
  const h = harness();
  for (const id of ['discover-own', 'discover-browse', 'discover-requestable', 'discover-manage', 'discover-concept', 'manage-load-targets']) {
    assert.equal(h.get(id).disabled, false); await h.get(id).dispatch('click');
    assert.equal(h.calls.length, 0); assert.ok(h.get('discovery-status').textContent.includes('연결하기'));
  }
  assert.equal(h.get('token').focusCount, 6); assert.equal(mutationCount(h), 0);
});
test('own-access guide renders all verified provenance paths, summary, empty state and result focus', async () => {
  const h = harness(); await connect(h);
  const done = h.get('discover-own').dispatch('click');
  assert.deepEqual(h.calls.at(-1).body.params, { name: 'get_my_access', arguments: {} });
  assert.equal(h.get('discover-own').disabled, true); assert.equal(h.get('capabilities').attributes['aria-busy'], 'true');
  answer(h.calls.at(-1), accessTool()); await done;
  assert.equal(h.get('relationship-view').hidden, false); assert.equal(h.get('relationship-view').open, true);
  assert.equal(h.get('relationship-paths').children.length, 4);
  assert.ok(h.get('relationship-paths').text.includes('감사자 (auditor) → 정책 열람 정책 (read)'));
  assert.ok(h.get('relationship-summary').textContent.includes('역할 2개 → 정책 2개 → 리소스 2개'));
  assert.equal(h.get('relationship-title').focusCount, 1); assert.equal(h.get('capabilities').attributes['aria-busy'], 'false');
  const empty = h.get('discover-own').dispatch('click'); answer(h.calls.at(-1), asTool(carol())); await empty;
  assert.equal(h.get('relationship-paths').children.length, 1); assert.ok(h.get('relationship-paths').text.includes('허용 경로가 없습니다'));
  assert.equal(h.get('relationship-paths').text.includes('Alice'), false); assert.ok(h.get('relationship-summary').textContent.includes('역할 0개'));
});
test('catalog guide selects only returned IDs and reads each entity detail with safe text/focus', async () => {
  const h = harness(); await connect(h);
  for (const kind of ['users', 'roles', 'policies', 'resources']) {
    h.get('browse-entity').value = kind; h.get('browse-entity').dispatch('change');
    const data = catalogData(kind); data.items[0].name = '<img src=x onerror=alert(1)>';
    const done = h.get('discover-browse').dispatch('click');
    assert.deepEqual(h.calls.at(-1).body.params, { name: 'list_access_entities', arguments: { entity: kind } });
    answer(h.calls.at(-1), asTool(data)); await done;
    assert.equal(h.get('browse-item').children.length, data.items.length + 1);
    assert.equal(h.get('browse-item').children[1].textContent, `${data.items[0].name} (${data.items[0].id})`);
    h.get('browse-item').value = data.items[0].id; h.get('browse-item').dispatch('change');
    assert.equal(h.get('browse-id').value, data.items[0].id); assert.ok(h.get('browse-detail').focusCount > 0);
    const detail = h.get('browse-detail').dispatch('click');
    assert.deepEqual(h.calls.at(-1).body.params, { name: 'get_access_entity', arguments: { entity: kind, id: data.items[0].id } });
    answer(h.calls.at(-1), asTool({ entity: kind, item: data.items[0] })); await detail;
    assert.ok(h.get('browse-result').textContent.includes('<img src=x')); assert.ok(h.get('browse-result').focusCount > 0);
  }
  const count = h.calls.length;
  h.get('browse-id').value = '../SECRET'; await h.get('browse-detail').dispatch('click');
  assert.equal(h.calls.length, count); assert.ok(h.get('browse-id').focusCount > 0); assert.equal(mutationCount(h), 0);
});
test('catalog empty, denial and invalid graph replace stale displays with usable status', async () => {
  const h = harness(); await connect(h);
  let done = h.get('discover-browse').dispatch('click'); answer(h.calls.at(-1), asTool(catalogData('roles'))); await done;
  done = h.get('discover-browse').dispatch('click'); answer(h.calls.at(-1), asTool({ entity: 'roles', items: [] })); await done;
  assert.equal(h.get('browse-item').disabled, true); assert.equal(h.get('browse-item').children.length, 1); assert.ok(h.get('browse-result').textContent.includes('항목이 없습니다'));
  done = h.get('discover-browse').dispatch('click'); answer(h.calls.at(-1), { isError: true, content: [{ type: 'text', text: 'FORBIDDEN' }] }); await done;
  assert.equal(h.get('browse-result').textContent, ''); assert.ok(h.get('browse-status').textContent.includes('권한이 부족')); assert.ok(h.get('discovery-status').focusCount > 0);
  done = h.get('discover-own').dispatch('click'); const malformed = alice(); malformed.resources[0].grants.pop(); answer(h.calls.at(-1), asTool(malformed)); await done;
  assert.equal(h.get('relationship-view').hidden, true); assert.equal(h.get('relationship-paths').children.length, 0); assert.ok(h.get('discovery-status').textContent.includes('응답 형식'));
});
test('requestable and concept guides use real read handlers and never present an application or grant', async () => {
  const h = harness(); await connect(h);
  let done = h.get('discover-requestable').dispatch('click'); assert.equal(h.calls.at(-1).body.params.name, 'get_requestable_roles');
  answer(h.calls.at(-1), asTool({ roles: [{ id: 'candidate', name: '후보 역할', description: '설명' }] })); await done;
  assert.ok(h.text().includes('현재 권한 아님')); assert.ok(h.text().includes('신청·승인 기능이 없습니다')); assert.ok(h.get('messages').focusCount > 0);
  done = h.get('discover-concept').dispatch('click'); assert.deepEqual(h.calls.at(-1).body.params, { name: 'explain_authorization', arguments: { concept: 'overview' } });
  answer(h.calls.at(-1), asTool({ concept: 'overview', explanation: '역할과 정책의 관계 설명' })); await done;
  assert.ok(h.text().includes('실제 권한 근거가 아닙니다')); assert.equal(mutationCount(h), 0);
});
async function loadTargetCatalogs(h, first, second) {
  const done = h.get('manage-load-targets').dispatch('click');
  assert.deepEqual(h.calls.at(-1).body.params, { name: 'list_access_entities', arguments: { entity: first.entity } });
  answer(h.calls.at(-1), asTool(first)); await tick();
  assert.deepEqual(h.calls.at(-1).body.params, { name: 'list_access_entities', arguments: { entity: second.entity } });
  answer(h.calls.at(-1), asTool(second)); await done;
}
test('management guide only opens form; all link types use permitted catalog selection and protected denial', async () => {
  for (const [entry, kinds] of [[mutationCases[0], ['users', 'roles']], [mutationCases[2], ['roles', 'policies']], [mutationCases[4], ['policies', 'resources']]]) {
    const h = harness(); await connect(h); const count = h.calls.length;
    h.get('discover-manage').dispatch('click'); assert.equal(h.get('management-panel').open, true); assert.equal(h.calls.length, count); assert.ok(h.get('manage-operation').focusCount > 0);
    h.get('manage-operation').value = entry[0]; h.get('manage-operation').dispatch('change');
    const first = catalogData(kinds[0]), second = catalogData(kinds[1]);
    const protectedItem = { ...second.items[0], id: kinds[1] === 'resources' ? 'R999' : 'SYSTEM_PROTECTED', protected: true };
    if (kinds[1] === 'roles') protectedItem.requestable = false;
    second.items.push(protectedItem);
    await loadTargetCatalogs(h, first, second);
    assert.equal(h.get('manage-second-select').children.at(-1).disabled, true);
    h.get('manage-first-select').value = first.items[0].id; h.get('manage-first-select').dispatch('change');
    h.get('manage-second-select').value = second.items[0].id; h.get('manage-second-select').dispatch('change');
    assert.equal(h.get('manage-first').value, first.items[0].id); assert.equal(h.get('manage-second').value, second.items[0].id);
    const prepared = h.get('manage-form').dispatch('submit'); answer(h.calls.at(-1), accessTool()); await prepared;
    assert.equal(h.get('manage-preview').hidden, false); assert.ok(h.get('manage-preview').focusCount > 0); assert.equal(mutationCount(h), 0);
    h.get('manage-second-select').value = protectedItem.id; h.get('manage-second-select').dispatch('change');
    assert.equal(h.get('manage-second').value, ''); assert.equal(h.get('manage-preview').hidden, true); assert.ok(h.get('manage-target-status').textContent.includes('보호된 관계'));
    h.get('manage-execute').dispatch('click'); assert.equal(mutationCount(h), 0);
  }
});
test('empty or denied target catalogs explain limitations and keep validated direct-ID fallback', async () => {
  const h = harness(); await connect(h);
  await loadTargetCatalogs(h, { entity: 'users', items: [] }, { entity: 'roles', items: [] });
  assert.equal(h.get('manage-first-select').disabled, true); assert.ok(h.get('manage-target-status').textContent.includes('비어 있거나 모두 보호'));
  assert.equal(h.get('manage-first').disabled, false);
  const denied = h.get('manage-load-targets').dispatch('click'); answer(h.calls.at(-1), { isError: true, content: [{ type: 'text', text: 'FORBIDDEN' }] }); await denied;
  assert.ok(h.get('manage-target-status').textContent.includes('불러오지 못했습니다')); assert.equal(h.get('manage-first-select').children.length, 1);
  await previewManagement(h); assert.equal(h.get('manage-preview').hidden, false); assert.equal(mutationCount(h), 0);
});
test('account switch clears relation/catalog/target selections and fences a late catalog response', async () => {
  const h = harness(); await connect(h, 'ALICE');
  let done = h.get('discover-own').dispatch('click'); answer(h.calls.at(-1), accessTool()); await done;
  done = h.get('discover-browse').dispatch('click'); answer(h.calls.at(-1), asTool(catalogData('roles'))); await done;
  await loadTargetCatalogs(h, catalogData('users'), catalogData('roles'));
  const pending = h.get('manage-load-targets').dispatch('click'), old = h.calls.at(-1);
  h.get('token').value = 'BOB'; h.get('token').dispatch('input');
  assert.equal(h.get('relationship-view').hidden, true); assert.equal(h.get('relationship-paths').children.length, 0); assert.equal(h.get('browse-result').textContent, '');
  assert.equal(h.get('browse-item').children.length, 1); assert.equal(h.get('manage-first-select').children.length, 1); assert.equal(h.get('manage-second-select').children.length, 1);
  await connect(h, 'BOB'); const count = h.calls.length; answer(old, asTool(catalogData('users'))); await pending;
  assert.equal(h.calls.length, count); assert.equal(h.get('manage-first-select').children.length, 1);
  h.get('manage-first-select').value = 'alice'; h.get('manage-first-select').dispatch('change'); assert.equal(h.get('manage-first').value, '');
});
test('mutation readback rebuilds relationship view from new verified state and clears target selections', async () => {
  const h = harness(); await connect(h);
  let read = h.get('discover-own').dispatch('click'); answer(h.calls.at(-1), accessTool()); await read;
  await loadTargetCatalogs(h, catalogData('users'), catalogData('roles')); await previewManagement(h);
  const done = h.get('manage-execute').dispatch('click');
  assert.equal(h.get('relationship-view').hidden, true); assert.equal(h.get('manage-first-select').children.length, 1);
  answer(h.calls.at(-1), asTool(mutationData('assign_role_to_user', { userId: 'U200', roleId: 'reader' }, true)));
  const fresh = alice(); fresh.resources.pop(); fresh.policies[1].resourceIds = ['R100'];
  await answerRefresh(h, fresh); await done;
  assert.equal(h.get('relationship-paths').children.length, 3); assert.equal(h.get('relationship-paths').text.includes('R200'), false);
  assert.ok(h.get('relationship-summary').textContent.includes('리소스 1개')); assert.equal(mutationCount(h), 1);
});
test('discovery markup has five purposes, explicit labels, keyboard controls and mobile layout rules', async () => {
  const html = await readFile(new URL('../chat.html', import.meta.url), 'utf8');
  const css = await readFile(new URL('../chat.css', import.meta.url), 'utf8');
  assert.equal((html.match(/class="capability-card"/g) || []).length, 5);
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]); assert.equal(new Set(ids).size, ids.length);
  for (const match of html.matchAll(/<(?:input|select)\b[^>]*\bid="([^"]+)"/g)) assert.ok(html.includes(`for="${match[1]}"`), `missing label for ${match[1]}`);
  for (const id of ['discover-own', 'discover-browse', 'discover-requestable', 'discover-manage', 'discover-concept', 'browse-detail', 'manage-load-targets']) assert.match(html, new RegExp(`<button\\b[^>]*id="${id}"[^>]*type="button"|<button\\b[^>]*type="button"[^>]*id="${id}"`));
  assert.match(html, /id="discovery-status"[^>]*role="status"[^>]*tabindex="-1"/);
  assert.match(css, /@media\(max-width:650px\)/); assert.match(css, /\.capability-grid\{grid-template-columns:1fr\}/);
  assert.match(css, /select:focus-visible,summary:focus-visible/); assert.match(css, /prefers-reduced-motion:reduce/);
});
test('401 clears discovery paths, catalog selections and management targets from the expired actor', async () => {
  const h = harness(); await connect(h);
  let done = h.get('discover-own').dispatch('click'); answer(h.calls.at(-1), accessTool()); await done;
  done = h.get('discover-browse').dispatch('click'); answer(h.calls.at(-1), asTool(catalogData('roles'))); await done;
  await loadTargetCatalogs(h, catalogData('users'), catalogData('roles'));
  done = h.get('browse-detail').dispatch('click'); // Empty ID stays local and asks for input.
  await done;
  h.get('browse-id').value = 'editor'; done = h.get('browse-detail').dispatch('click');
  h.calls.at(-1).resolve(new Response('SECRET', { status: 401 })); await done;
  assert.equal(h.get('relationship-view').hidden, true); assert.equal(h.get('relationship-paths').children.length, 0);
  assert.equal(h.get('catalog-browser').hidden, true); assert.equal(h.get('browse-result').textContent, ''); assert.equal(h.get('browse-id').value, '');
  assert.equal(h.get('manage-first-select').children.length, 1); assert.equal(h.get('manage-second-select').children.length, 1);
  assert.equal(h.get('manage-preview').hidden, true); assert.equal(h.get('token').value, '');
});
test('relationship disclosure retains native Tab focus and Enter/Space activation when rendered collapsed', async () => {
  const html = await readFile(new URL('../chat.html', import.meta.url), 'utf8');
  const summary = html.match(/<summary\b[^>]*id="relationship-title"[^>]*>/)?.[0];
  assert.ok(summary); assert.doesNotMatch(summary, /tabindex="-1"|aria-disabled="true"/);
  assert.match(html, /<details\b[^>]*id="relationship-view"[^>]*>\s*<summary\b[^>]*id="relationship-title"/);
  const h = harness(); await connect(h); await show(h, '내 정보 알려줘', accessTool());
  assert.equal(h.get('relationship-view').hidden, false); assert.ok(!h.get('relationship-view').open);
  // Native key behavior is verified by TESTER with Tab/Enter/Space, without direct focus calls.
});
