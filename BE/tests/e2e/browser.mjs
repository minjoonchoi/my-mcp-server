// Real browser test: no intercepted/fulfilled responses and no credential output.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
import { captureMcpResponses } from './mcp_response_capture.mjs';

let browser;
let page;
let capture;
let stage = 'launch';
const checks = [];
const network = [];
try {
  let chromium;
  try {
    ({ chromium } = await import(pathToFileURL(process.env.E2E_PLAYWRIGHT_MODULE).href));
    browser = await chromium.launch({ headless: true, ...(process.env.E2E_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.E2E_CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] } : { channel: 'chrome' }) });
  } catch {
    console.log(JSON.stringify({ status: 'BLOCKED', reason: 'Existing local Playwright/Chrome could not launch; no installation attempted' }));
    process.exitCode = 0;
  }
  if (browser) {
    const context = await browser.newContext();
    page = await context.newPage();
    page.setDefaultTimeout(15000);
    capture = await captureMcpResponses(page);
    page.on('response', response => {
      const url = new URL(response.url());
      if (['/chat.html', '/chat.js', '/chat.css', '/mcp'].includes(url.pathname)) {
        let method;
        let tool;
        try { const body = response.request().postDataJSON(); method = body?.method; tool = body?.params?.name; } catch { /* static GET */ }
        network.push({ path: url.pathname, status: response.status(), rpcMethod: method || null, tool: tool || null });
      }
    });
    stage = 'load-chat';
    await page.goto(process.env.E2E_BASE_URL + '/chat.html');
    for (const asset of ['/chat.html', '/chat.js', '/chat.css']) {
      assert.ok(network.some(row => row.path === asset && row.status === 200), 'Browser asset did not load with HTTP 200: ' + asset);
    }
    assert.equal(await page.locator('#token').getAttribute('type'), 'password');
    const tokens = {};
    for (const name of ['alice', 'bob', 'carol', 'expired', 'connect-only', 'admin-read-only']) {
      tokens[name] = (await fs.readFile(path.join(process.env.E2E_FIXTURE_DIR, 'mcp-' + name + '.jwt'), 'utf8')).trim();
    }
    let connectedBefore = false;
    const connect = async name => {
      stage = 'connect-' + name;
      if (await page.locator('#login-dialog').isVisible()) await page.locator('#login-cancel').click();
      if (!await page.locator('.advanced-connection').evaluate(element => element.open)) await page.locator('.advanced-connection > summary').click();
      await page.locator('#token').fill(tokens[name]);
      // Filling a replacement token must synchronously discard old results.
      if (connectedBefore) assert.equal(await page.locator('#messages').innerText(), '');
      await page.locator('#connect').click();
      await page.waitForFunction(() => document.getElementById('connection-state').dataset.state === 'ready');
      assert.equal(await page.locator('#token').inputValue(), '');
      connectedBefore = true;
    };
    const ask = async (question, tool, expectedText, expectedUser, expectedArgs) => {
      stage = 'question-' + question;
      const responsePromise = page.waitForResponse(response => {
        if (!response.url().endsWith('/mcp') || response.request().method() !== 'POST') return false;
        const data = response.request().postDataJSON();
        return data?.method === 'tools/call' && data.params?.name === tool;
      });
      await page.locator('#query').fill(question);
      await page.locator('#send').click();
      stage = 'response-' + question;
      const response = await responsePromise;
      assert.equal(response.status(), 200);
      if (expectedArgs) assert.deepEqual(response.request().postDataJSON().params.arguments, expectedArgs);
      stage = 'decode-' + question;
      let raw = await capture.text(response);
      if (raw.startsWith('event:') || raw.startsWith('data:')) raw = raw.split('\n').find(line => line.startsWith('data:')).slice(5).trim();
      const payload = JSON.parse(raw);
      assert.equal(payload.result.isError, false);
      const actual = payload.result.structuredContent;
      assert.deepEqual(actual, JSON.parse(payload.result.content[0].text), 'MCP structured and textual results must agree');
      if (expectedUser) assert.equal(actual.user.id, expectedUser);
      stage = 'render-' + question;
      await page.waitForFunction(() => !document.getElementById('send').disabled);
      const rendered = await page.locator('#messages .assistant .bubble').last().innerText();
      assert.ok(rendered.includes(expectedText), 'Expected rendered text missing: ' + expectedText);
      checks.push({ question, tool, args: response.request().postDataJSON().params.arguments, http: 200, expectedUser: expectedUser || null,
        resourceDecision: tool === 'check_my_resource_access' ? actual : undefined, rendered: rendered.slice(0, 2000) });
      return actual;
    };
    await connect('alice');
    for (const [question, tool, text] of [
      ['내 정보 알려줘', 'get_my_access', '(alice)'],
      ['내 역할은 뭐야?', 'get_my_access', 'role-analyst'],
      ['나에게 적용된 정책은?', 'get_my_access', 'policy-shared'],
      ['접근 가능한 리소스 알려줘', 'get_my_access', 'R300'],
      ['리소스 R100에 접근할 수 있어? 왜?', 'check_my_resource_access', '접근 허용'],
      ['역할과 정책은 어떻게 달라?', 'explain_authorization', '실제 권한 근거가 아닙니다'],
      ['리소스 R400에 접근할 수 있어?', 'check_my_resource_access', '접근 허용 안 됨'],
      ['리소스 R999에 접근할 수 있어?', 'check_my_resource_access', '접근 허용 안 됨'],
    ]) await ask(question, tool, text, tool === 'get_my_access' ? 'alice' : undefined);
    const catalogKinds = [['사용자', 'users', ['alice']], ['역할', 'roles', ['role-analyst', 'role-editor']],
      ['정책', 'policies', ['ctx-page-customers', 'ctx-page-audit', 'ctx-endpoint-customers', 'ctx-endpoint-audit',
        'ctx-field-customers-id', 'ctx-field-customers-name', 'ctx-field-audit-id', 'ctx-field-audit-name', 'ctx-field-audit-email',
        'policy-edit', 'policy-reports', 'policy-shared'].sort()], ['리소스', 'resources', ['R100', 'R200', 'R300']]];
    for (const [label, entity, expected] of catalogKinds) {
      const data = await ask(label + ' 목록', 'list_access_entities', expected[0], undefined, { entity });
      assert.deepEqual(data.items.map(item => item.id), expected);
    }
    const ownDetail = await ask('사용자 alice 상세', 'get_access_entity', 'alice', undefined, { entity: 'users', id: 'alice' });
    assert.equal(ownDetail.item.id, 'alice');
    const publicRoles = await ask('요청 가능한 역할 알려줘', 'get_requestable_roles', '공개 메타데이터', undefined, {});
    assert.ok(publicRoles.roles.every(role => Object.keys(role).sort().join(',') === 'description,id,name'));
    const hiddenDetail = async id => {
      stage = 'hidden-detail-' + id;
      const promise = page.waitForResponse(response => {
        if (!response.url().endsWith('/mcp')) return false;
        try { return response.request().postDataJSON()?.params?.name === 'get_access_entity'; } catch { return false; }
      });
      await page.locator('#query').fill('사용자 ' + id + ' 상세');
      await page.locator('#send').click();
      const response = await promise;
      assert.equal(response.status(), 200);
      assert.deepEqual(response.request().postDataJSON().params.arguments, { entity: 'users', id });
      let raw = await capture.text(response);
      if (raw.startsWith('event:') || raw.startsWith('data:')) raw = raw.split('\n').find(line => line.startsWith('data:')).slice(5).trim();
      const result = JSON.parse(raw).result;
      assert.equal(result.isError, true);
      assert.ok(['NOT_FOUND', 'UPSTREAM_NOT_FOUND'].includes(result.content[0].text));
      await page.waitForFunction(() => !document.getElementById('send').disabled);
      const rendered = await page.locator('#messages .assistant .bubble').last().innerText();
      checks.push({ question: '사용자 ' + id + ' 상세', tool: 'get_access_entity', args: { entity: 'users', id }, error: result.content[0].text, rendered });
      return rendered;
    };
    assert.equal(await hiddenDetail('bob'), await hiddenDetail('missing-user'), 'Hidden and missing detail display must be indistinguishable');
    await connect('admin-read-only');
    for (const [label, entity] of catalogKinds) {
      const data = await ask(label + ' 목록', 'list_access_entities', entity === 'users' ? 'admin' : entity === 'roles' ? 'SYSTEM_ADMIN' : 'R', undefined, { entity });
      if (entity === 'users') assert.deepEqual(data.items.map(item => item.id), ['U900', 'admin', 'alice', 'bob', 'carol']);
      else assert.ok(data.items.some(item => item.protected === true));
    }
    const foreignDetail = await ask('사용자 bob 상세', 'get_access_entity', 'bob', undefined, { entity: 'users', id: 'bob' });
    assert.equal(foreignDetail.item.id, 'bob');
    await connect('bob');
    assert.ok(!(await page.locator('#messages').innerText()).includes('(alice)'));
    const bob = await ask('접근 가능한 리소스 알려줘', 'get_my_access', 'R400', 'bob');
    assert.deepEqual(bob.resources.map(r => r.id), ['R100', 'R400']);
    assert.ok(!(await page.locator('#messages').innerText()).includes('R300'));
    await page.locator('#disconnect').click();
    assert.equal(await page.locator('#messages').innerText(), '');
    assert.equal(await page.locator('#token').inputValue(), '');
    await connect('carol');
    const carol = await ask('내 역할은 뭐야?', 'get_my_access', '부여된 역할이 없습니다.', 'carol');
    assert.deepEqual(carol.roles, []);
    assert.deepEqual(carol.policies, []);
    assert.deepEqual(carol.resources, []);
    await page.locator('#token').fill(tokens.expired);
    assert.equal(await page.locator('#messages').innerText(), '');
    const rejected = page.waitForResponse(r => r.url().endsWith('/mcp') && r.status() === 401);
    await page.locator('#connect').click();
    await rejected;
    await page.waitForFunction(() => document.getElementById('connection-state').dataset.state === 'idle');
    assert.equal(await page.locator('#token').inputValue(), '');
    assert.equal(await page.locator('#messages').innerText(), '');
    checks.push({ check: 'expired credential', http: 401, previousDataCleared: true });
    await connect('connect-only');
    const denied = page.waitForResponse(r => r.url().endsWith('/mcp') && r.request().postDataJSON()?.method === 'tools/call');
    await page.locator('#query').fill('내 정보 알려줘');
    await page.locator('#send').click();
    const deniedResponse = await denied;
    let deniedRaw = await capture.text(deniedResponse);
    if (deniedRaw.startsWith('event:') || deniedRaw.startsWith('data:')) deniedRaw = deniedRaw.split('\n').find(l => l.startsWith('data:')).slice(5).trim();
    assert.equal(JSON.parse(deniedRaw).result.isError, true);
    await page.waitForFunction(() => !document.getElementById('send').disabled);
    assert.equal(await page.locator('#messages .assistant.error').count(), 1);
    checks.push({ check: 'tool scope error', isError: true, visibleError: true });
    await page.locator('#disconnect').click();
    const storage = await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }));
    assert.deepEqual(storage, { local: [], session: [] });
    console.log(JSON.stringify({ status: 'PASS', browser: 'Chrome', transport: 'real same-origin MCP',
      responseObservation: 'Passive CDP Network.streamResourceContent; original request IDs and bytes', checks, network }));
  }
} catch (error) {
  // Never print Playwright call logs: a fill call can contain a credential.
  let state;
  try { state = await page.locator('#connection-note').innerText({ timeout: 1000 }); } catch { state = 'Unavailable'; }
  const firstLine = String(error.message).split('\n')[0].replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, '[REDACTED JWT]').slice(0, 300);
  console.log(JSON.stringify({ status: 'FAIL', stage, state, network, completedChecks: checks, reason: firstLine, errorType: error.name }));
} finally {
  if (capture) await capture.dispose();
  if (browser) await browser.close();
}
