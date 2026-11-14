// Real backend mutations in the explicitly isolated TESTER database only.
// Fault mode lets the actual server commit, then loses/delays its browser response.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
import { claimsRollbackCompleted } from './browser_contract.mjs';

const mutationNames = new Set(['assign_role_to_user', 'remove_role_from_user', 'attach_policy_to_role', 'detach_policy_from_role', 'attach_resource_to_policy', 'detach_resource_from_policy']);
let browser, page, stage = 'launch';
const events = [], checks = [];
const fixture = async name => (await fs.readFile(path.join(process.env.E2E_FIXTURE_DIR, name + '.jwt'), 'utf8')).trim();
const decode = raw => JSON.parse(raw.startsWith('event:') || raw.startsWith('data:') ? raw.split('\n').find(line => line.startsWith('data:')).slice(5).trim() : raw);
const isMutation = request => {
  try { const value = request.postDataJSON(); return value?.method === 'tools/call' && mutationNames.has(value.params?.name); } catch { return false; }
};
const mutationCount = () => events.filter(event => event.mutation).length;
try {
  const { chromium } = await import(pathToFileURL(process.env.E2E_PLAYWRIGHT_MODULE).href);
  try { browser = await chromium.launch({ headless: true, ...(process.env.E2E_CHROMIUM_EXECUTABLE
    ? { executablePath: process.env.E2E_CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] } : { channel: 'chrome' }) }); }
  catch { console.log(JSON.stringify({ status: 'BLOCKED', reason: 'Existing Chrome could not launch' })); }
  if (browser) {
    const context = await browser.newContext();
    page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('request', request => {
      if (!request.url().endsWith('/mcp') || request.method() !== 'POST') return;
      const body = request.postDataJSON();
      events.push({ method: body.method, tool: body.params?.name || null,
        args: body.method === 'tools/call' ? body.params.arguments : null, mutation: isMutation(request) });
    });
    await page.goto(process.env.E2E_BASE_URL + '/chat.html');
    const openManagement = async () => {
      if (!await page.locator('#management-panel').evaluate(element => element.open)) await page.locator('#discover-manage').click();
      assert.ok(await page.locator('#management-panel').evaluate(element => element.open));
      assert.ok(await page.locator('#manage-first').isVisible());
    };
    const connect = async name => {
      stage = 'connect-' + name;
      if (await page.locator('#login-dialog').isVisible()) await page.locator('#login-cancel').click();
      if (!await page.locator('.advanced-connection').evaluate(element => element.open)) await page.locator('.advanced-connection > summary').click();
      await page.locator('#token').fill(await fixture(name));
      await page.locator('#connect').click();
      await page.waitForFunction(() => document.getElementById('connection-state').dataset.state === 'ready');
    };
    const preview = async operation => {
      stage = 'preview-' + operation;
      await openManagement();
      await page.locator('#manage-operation').selectOption(operation);
      await page.locator('#manage-first').fill('carol');
      await page.locator('#manage-second').fill('role-analyst');
      await page.locator('#manage-preview-button').click();
      await page.waitForFunction(() => {
        const button = document.getElementById('manage-execute');
        return button && !button.disabled && !button.hidden;
      });
      const actor = await page.locator('#manage-actor').innerText();
      const summary = await page.locator('#manage-summary').innerText();
      assert.ok(actor.includes('admin'), 'Preview must name verified current actor');
      assert.ok(summary.includes('carol') && summary.includes('role-analyst'), 'Preview must freeze exact target IDs');
    };
    const executable = async () => await page.locator('#manage-execute').isVisible() && await page.locator('#manage-execute').isEnabled();
    const waitQuiet = () => page.waitForTimeout(250);
    await connect('mcp-admin');
    if (process.env.E2E_BROWSER_MODE === 'management') {
      await preview('assign_role_to_user');
      assert.equal(mutationCount(), 0, 'Preview performed a mutation');
      await page.locator('#manage-cancel').click();
      assert.equal(await executable(), false, 'Cancel left executable preview');
      assert.equal(mutationCount(), 0);
      checks.push({ check: 'preview then cancel', mutationRequests: 0 });

      await preview('assign_role_to_user');
      await page.locator('#manage-second').fill('role-editor');
      assert.equal(await executable(), false, 'Target change did not invalidate preview');
      await page.locator('#manage-second').press('Enter');
      await waitQuiet();
      assert.equal(mutationCount(), 0, 'Enter bypassed explicit execute click');
      await preview('assign_role_to_user');
      await page.locator('#manage-operation').selectOption('remove_role_from_user');
      assert.equal(await executable(), false, 'Operation change did not invalidate preview');
      checks.push({ check: 'target/operation edit and Enter', mutationRequests: 0 });

      await preview('assign_role_to_user');
      await page.locator('#token').fill(await fixture('mcp-bob'));
      assert.equal(await executable(), false, 'Account change kept old actor preview');
      assert.equal(await page.locator('#messages').innerText(), '');
      assert.equal(mutationCount(), 0);
      await connect('mcp-admin');
      checks.push({ check: 'account switch before execute', mutationRequests: 0 });

      await preview('assign_role_to_user');
      const start = events.length;
      const responsePromise = page.waitForResponse(response => isMutation(response.request()));
      stage = 'explicit-double-click';
      await page.locator('#manage-execute').dblclick();
      const response = await responsePromise;
      assert.equal(response.status(), 200);
      const result = decode(await response.text()).result;
      assert.equal(result.isError, false);
      assert.equal(result.structuredContent.operation, 'assign_role_to_user');
      assert.deepEqual(result.structuredContent.relationship, { userId: 'carol', roleId: 'role-analyst' });
      assert.equal(result.structuredContent.present, true);
      await page.waitForFunction(() => Boolean(document.getElementById('manage-status').textContent.trim()));
      await waitQuiet();
      assert.equal(mutationCount(), 1, 'Duplicate click emitted more than one write');
      assert.ok(events.slice(start).some(event => ['get_my_access', 'list_access_entities'].includes(event.tool)), 'Success did not refresh current graph/catalog');
      checks.push({ check: 'explicit double click and refresh', mutationRequests: 1, result: result.structuredContent });

      await preview('remove_role_from_user');
      const removedPromise = page.waitForResponse(response => isMutation(response.request()));
      await page.locator('#manage-execute').click();
      const removed = decode(await (await removedPromise).text()).result;
      assert.equal(removed.isError, false);
      assert.equal(removed.structuredContent.present, false);
      await waitQuiet();
      await connect('mcp-admin-manage-only');
      await openManagement();
      await page.locator('#manage-operation').selectOption('assign_role_to_user');
      await page.locator('#manage-first').fill('carol');
      await page.locator('#manage-second').fill('role-analyst');
      const beforeReadDenied = mutationCount();
      const deniedReadPromise = page.waitForResponse(response => {
        if (!response.url().endsWith('/mcp')) return false;
        try { const body = response.request().postDataJSON(); return body?.method === 'tools/call' && body.params?.name === 'get_my_access'; } catch { return false; }
      });
      await page.locator('#manage-preview-button').click();
      const deniedRead = await deniedReadPromise;
      assert.equal(deniedRead.status(), 200);
      const deniedResult = decode(await deniedRead.text()).result;
      assert.equal(deniedResult.isError, true);
      assert.equal(deniedResult.content[0].text, 'FORBIDDEN');
      await waitQuiet();
      assert.equal(await executable(), false, 'Manage-only UI inferred actor without authorized read');
      assert.equal(mutationCount(), beforeReadDenied, 'Read-denied preview emitted a mutation');
      assert.ok((await page.locator('#manage-status').innerText()).includes('authz.read'), 'UI did not explain missing read scope');
      checks.push({ check: 'manage-only valid targets reach read denial', args: { userId: 'carol', roleId: 'role-analyst' }, tool: 'get_my_access', error: 'FORBIDDEN', mutationRequests: 0 });
    } else {
      // All route fetches below reach the actual MCP/API/MySQL and verify a real success.
      for (const mode of ['lost', 'malformed', 'timeout', 'cancelled', 'late-account']) {
        stage = 'fault-' + mode;
        await connect('mcp-admin');
        await preview(['malformed', 'cancelled'].includes(mode) ? 'remove_role_from_user' : 'assign_role_to_user');
        let committed = false, release;
        const releasePromise = new Promise(resolve => { release = resolve; });
        const before = mutationCount();
        const handler = async route => {
          if (!isMutation(route.request())) { await route.continue(); return; }
          const response = await route.fetch();
          const result = decode(await response.text()).result;
          assert.equal(result.isError, false, 'Fault test did not reach successful real backend mutation');
          committed = true;
          if (mode === 'lost') await route.abort('connectionreset');
          else if (mode === 'malformed') await route.fulfill({ response, body: '{invalid-json' });
          else {
            await releasePromise;
            // Browser cancellation can discard the route after the real server committed.
            try { await route.fulfill({ response }); } catch { /* Expected aborted fetch. */ }
          }
        };
        await page.route('**/mcp', handler);
        await page.locator('#manage-execute').click();
        // Wait for actual committed response in the test driver, never infer it from UI.
        const deadline = Date.now() + 10000;
        while (!committed && Date.now() < deadline) await page.waitForTimeout(50);
        assert.ok(committed, 'Actual backend did not commit within deadline');
        if (mode === 'late-account') {
          await page.locator('#token').fill(await fixture('mcp-bob'));
          release();
          await waitQuiet();
          const status = await page.locator('#manage-status').innerText();
          assert.ok(!status.includes('role-analyst') && !status.includes('carol'), 'Late old-actor mutation result leaked into new actor UI');
          assert.equal(await executable(), false);
        } else {
          if (mode === 'cancelled') await page.locator('#manage-cancel').click();
          await page.waitForFunction(() => /미확인|확인할 수 없|확인되지|알 수 없/.test(document.getElementById('manage-status').textContent));
          release();
          const text = await page.locator('#manage-status').innerText();
          assert.equal(claimsRollbackCompleted(text), false, 'UI falsely claimed rollback after committed write');
          await page.locator('#refresh-access').click();
          await waitQuiet();
        }
        assert.equal(mutationCount(), before + 1, 'Unknown outcome automatically retried mutation');
        await page.unroute('**/mcp', handler);
        checks.push({ check: mode, backendSuccessResponseObserved: true, mutationRequests: 1, responseFault: mode,
          evidenceLimit: 'This browser observation verifies the real successful MCP response before response loss; it does not independently prove per-mode MySQL commit.' });
      }
    }
    console.log(JSON.stringify({ status: 'PASS', mode: process.env.E2E_BROWSER_MODE, checks, events }));
  }
} catch (error) {
  let state;
  try { state = await page.locator('#manage-status').innerText({ timeout: 1000 }); } catch { state = 'Unavailable'; }
  console.log(JSON.stringify({ status: 'FAIL', stage, state, checks, events, errorType: error.name,
    reason: error.code === 'ERR_ASSERTION' ? String(error.message).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, '[REDACTED JWT]') : 'Browser operation failed or timed out' }));
} finally {
  if (browser) await browser.close();
}
