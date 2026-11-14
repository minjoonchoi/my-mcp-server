// Actual Chromium -> same-origin login relay -> API -> authenticated MCP.
// No request interception, response fulfillment or mocked successful responses.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

let browser;
let page;
let stage = 'preflight';
const checks = [];
const network = [];
const consoleMessages = [];
const secrets = [];
const bearerTokens = [];
const clean = value => {
  let output = String(value).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, '[REDACTED JWT]');
  for (const secret of secrets) if (secret) output = output.split(secret).join('[REDACTED]');
  return output;
};
const parseRpc = async response => {
  let raw = await response.text();
  if (raw.startsWith('event:') || raw.startsWith('data:')) raw = raw.split('\n').find(line => line.startsWith('data:')).slice(5).trim();
  return JSON.parse(raw);
};

try {
  const fixtureDir = process.env.E2E_FIXTURE_DIR;
  const outputDir = process.env.E2E_OUTPUT_DIR;
  const passwords = JSON.parse(await fs.readFile(path.join(fixtureDir, 'login-passwords.json'), 'utf8'));
  secrets.push(...Object.values(passwords));
  const modulePath = process.env.E2E_PLAYWRIGHT_MODULE;
  const moduleFile = (await fs.stat(modulePath)).isDirectory() ? path.join(modulePath, 'index.mjs') : modulePath;
  const { chromium } = await import(pathToFileURL(moduleFile).href);
  stage = 'launch';
  browser = await chromium.launch({ executablePath: process.env.E2E_CHROMIUM_EXECUTABLE, headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext();
  page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('console', message => consoleMessages.push(message.text()));
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/mcp') {
      const authorization = request.headers().authorization;
      bearerTokens.push(authorization || '');
      let method;
      let tool;
      try {
        const data = request.postDataJSON();
        method = data?.method;
        tool = data?.params?.name;
      } catch { /* A malformed request is represented without raw content. */ }
      network.push({ path: '/mcp', method: request.method(), rpcMethod: method || null, tool: tool || null,
        hasBearer: /^Bearer [A-Za-z0-9_.-]+$/.test(authorization || '') });
    } else if (url.pathname === '/auth/login') {
      const body = request.postDataJSON();
      network.push({ path: '/auth/login', method: request.method(), keys: Object.keys(body).sort(),
        username: body.username, hasAuthorization: Boolean(request.headers().authorization) });
    }
  });
  const storageEmpty = async () => {
    const storage = await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }));
    assert.deepEqual(storage, { local: [], session: [] }, 'Credentials must not be stored in browser storage');
    assert.deepEqual(await context.cookies(), [], 'Login must not create cookies');
  };
  const assertNoSecretDom = async () => {
    const content = await page.locator('body').innerText();
    for (const secret of secrets) assert.ok(!content.includes(secret), 'A credential appeared in rendered page text');
    assert.equal(await page.locator('#login-password').inputValue(), '', 'Password input must be cleared');
    if (await page.locator('#token').count()) assert.equal(await page.locator('#token').inputValue(), '', 'Manual token input must remain empty');
    await storageEmpty();
  };
  const requireLogin = async question => {
    stage = 'logged-out-query';
    assert.equal(await page.locator('#query').isEnabled(), true, 'Logged-out query must remain usable');
    assert.equal(await page.locator('#send').isEnabled(), true, 'Logged-out send must remain usable');
    const before = network.filter(row => row.path === '/mcp').length;
    await page.locator('#query').fill(question);
    await page.locator('#send').click();
    await page.locator('#login-dialog').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#query').inputValue(), question, 'Login prompt must preserve the draft');
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'login-username', 'Username should receive focus');
    assert.equal(network.filter(row => row.path === '/mcp').length, before, 'Logged-out submission must not call MCP');
    checks.push({ check: 'logged-out submission requires login', draftPreserved: true, mcpRequests: 0 });
  };
  const login = async (username, password = passwords[username], expectedStatus = 200) => {
    stage = 'login-' + username + '-' + expectedStatus;
    const draft = await page.locator('#query').inputValue();
    await page.locator('#login-username').fill(username);
    await page.locator('#login-password').fill(password);
    const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === '/auth/login');
    await page.locator('#login-submit').click();
    assert.equal(await page.locator('#login-password').inputValue(), '', 'Password must be cleared on submit');
    const response = await responsePromise;
    assert.equal(response.status(), expectedStatus, 'Unexpected login HTTP status');
    assert.ok((response.headers()['cache-control'] || '').split(',').map(value => value.trim().toLowerCase()).includes('no-store'),
      'Login response must disable caching');
    const request = response.request();
    assert.deepEqual(Object.keys(request.postDataJSON()).sort(), ['password', 'username'], 'Login request must contain only credentials');
    assert.equal(request.headers().authorization, undefined, 'Login relay request must not send a bearer');
    const body = await response.json();
    if (expectedStatus !== 200) {
      assert.deepEqual(body, { code: 'INVALID_CREDENTIALS' }, 'Credential errors must be fixed');
      await page.waitForFunction(() => !document.getElementById('login-submit').disabled);
      assert.equal(await page.locator('#login-dialog').isVisible(), true, 'Failed login must remain actionable');
      assert.notEqual(await page.locator('#connection-state').getAttribute('data-state'), 'ready', 'Wrong password must not authenticate');
      assert.ok((await page.locator('#login-status').innerText()).length > 0, 'Wrong password needs visible feedback');
      checks.push({ check: 'wrong password rejected', http: expectedStatus, authenticated: false });
      return;
    }
    secrets.push(body.mcpAccessToken, body.apiAccessToken);
    await page.waitForFunction(() => document.getElementById('connection-state').dataset.state === 'ready');
    await page.locator('#login-dialog').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#query').inputValue(), draft, 'Successful login must restore the draft');
    assert.ok(network.some(row => row.rpcMethod === 'initialize'), 'Authenticated MCP initialize was not sent');
    assert.ok(network.some(row => row.rpcMethod === 'notifications/initialized'), 'MCP initialized notification was not sent');
    assert.ok(network.some(row => row.rpcMethod === 'tools/list'), 'Authenticated tool discovery was not sent');
    assert.ok(bearerTokens.includes('Bearer ' + body.mcpAccessToken), 'MCP must receive the issued MCP audience token');
    assert.ok(!bearerTokens.includes('Bearer ' + body.apiAccessToken), 'Direct API token must never be sent to MCP');
    await assertNoSecretDom();
    checks.push({ check: 'login and authenticated MCP initialization', username, userId: body.user.id, http: 200, expiresIn: body.expiresIn });
  };
  const ask = async (expectedUser, expectedResources) => {
    stage = 'chat-' + expectedUser;
    const responsePromise = page.waitForResponse(response => {
      if (new URL(response.url()).pathname !== '/mcp') return false;
      try { return response.request().postDataJSON()?.params?.name === 'get_my_access'; } catch { return false; }
    });
    await page.locator('#query').fill('접근 가능한 리소스 알려줘');
    await page.locator('#send').click();
    const response = await responsePromise;
    assert.equal(response.status(), 200, 'Authenticated MCP request must succeed');
    const payload = await parseRpc(response);
    assert.equal(payload.result.isError, false, 'MCP tool must return actual success');
    const actual = payload.result.structuredContent;
    assert.equal(actual.user.id, expectedUser, 'Response identity must match the logged-in user');
    assert.deepEqual(actual.resources.map(item => item.id), expectedResources, 'API permission result differs');
    await page.waitForFunction(() => !document.getElementById('send').disabled);
    const rendered = await page.locator('#messages .assistant .bubble').last().innerText();
    for (const resource of expectedResources) assert.ok(rendered.includes(resource), 'Returned resource must be rendered');
    await assertNoSecretDom();
    checks.push({ check: 'real authenticated chat answer', userId: actual.user.id, resources: actual.resources.map(item => item.id), http: 200 });
  };

  stage = 'load-chat';
  await page.goto(process.env.E2E_BASE_URL + '/chat.html');
  assert.equal(await page.locator('#login-password').getAttribute('type'), 'password', 'Password must use a masked input');
  await storageEmpty();
  await requireLogin('내 역할은 뭐야?');
  await login('alice', 'incorrect-local-password', 401);
  await login('alice');
  await ask('alice', ['R100', 'R200', 'R300']);
  if (outputDir) await page.screenshot({ path: path.join(outputDir, 'auth-alice.png'), fullPage: true });
  stage = 'logout';
  await page.locator('#disconnect').click();
  await page.waitForFunction(() => document.getElementById('connection-state').dataset.state === 'idle');
  assert.equal(await page.locator('#messages').innerText(), '', 'Logout must clear prior-user messages');
  assert.equal(await page.locator('#manage-preview').isVisible(), false, 'Logout must clear management preview');
  await assertNoSecretDom();
  checks.push({ check: 'logout clears prior-user state', messagesCleared: true, storageEmpty: true });
  await requireLogin('내 정보 알려줘');
  await login('bob');
  await ask('bob', ['R100', 'R400']);
  assert.ok(!(await page.locator('#messages').innerText()).includes('R300'), 'Alice permissions leaked into Bob session');
  assert.ok(!(await page.locator('#messages').innerText()).includes('(alice)'), 'Alice identity leaked into Bob session');
  if (outputDir) await page.screenshot({ path: path.join(outputDir, 'auth-bob.png'), fullPage: true });
  checks.push({ check: 'account switch isolation', previousUserDataVisible: false });
  stage = 'reload-memory-only';
  await page.reload();
  assert.notEqual(await page.locator('#connection-state').getAttribute('data-state'), 'ready', 'Reload must not recover a stored bearer');
  await requireLogin('내 정보 알려줘');
  await page.locator('#login-password').fill(passwords.alice);
  await page.locator('#login-cancel').click();
  await page.locator('#login-dialog').waitFor({ state: 'hidden' });
  await assertNoSecretDom();
  checks.push({ check: 'reload and cancel clear credentials', storageEmpty: true, authenticated: false });
  assert.ok(network.filter(row => row.path === '/mcp').every(row => row.hasBearer), 'Every MCP request must carry a bearer');
  for (const message of consoleMessages) for (const secret of secrets) assert.ok(!message.includes(secret), 'A credential appeared in browser console output');
  console.log(JSON.stringify({ status: 'PASS', browser: 'Chromium', transport: 'real same-origin HTTP/MCP/API', checks, network }));
} catch (error) {
  // Playwright call logs can include passwords. Never emit stacks or call logs.
  const blocked = ['preflight', 'launch'].includes(stage);
  console.log(JSON.stringify({ status: blocked ? 'BLOCKED' : 'FAIL', stage,
    reason: blocked ? 'Existing Chromium/Playwright could not launch; no installation attempted' : error.code === 'ERR_ASSERTION' ? clean(error.message) : 'Browser action failed or timed out',
    errorType: error.name, checks, network }));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
}
