// New capability-discovery UI only; previous UI runs do not satisfy these cases.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
import { grantPathTuple } from './browser_contract.mjs';

const mode = process.env.E2E_BROWSER_MODE?.replace('discovery-', '');
const selectors = JSON.parse(process.env.E2E_UI_SELECTORS || '{}');
const requirements = {
  guide: ['capabilities', 'own_read', 'catalog_load', 'requestable_read', 'manage_open', 'concept_read'], graph: ['own_read', 'graph', 'graph_paths'],
  catalog: ['catalog_entity', 'catalog_load', 'catalog_items', 'catalog_detail_button', 'catalog_detail', 'requestable_read', 'requestable_view'],
  manage: ['manage_open', 'manage_load_targets', 'manage_first_select', 'manage_second_select', 'catalog_entity', 'catalog_load', 'catalog_items', 'catalog_detail_button', 'catalog_detail'],
  switch: ['own_read', 'graph', 'catalog_entity', 'catalog_load', 'catalog_items', 'catalog_detail_button', 'catalog_detail', 'selection', 'manage_open'],
  mobile: ['capabilities', 'own_read', 'graph', 'catalog_entity'],
};
const mutationTools = new Set(['assign_role_to_user', 'remove_role_from_user', 'attach_policy_to_role', 'detach_policy_from_role', 'attach_resource_to_policy', 'detach_resource_from_policy']);
const records = [], requests = [];
let browser, page, stage = 'setup';
const decode = raw => JSON.parse(raw.startsWith('event:') || raw.startsWith('data:') ? raw.split('\n').find(line => line.startsWith('data:')).slice(5).trim() : raw);
const fixture = async name => (await fs.readFile(path.join(process.env.E2E_FIXTURE_DIR, 'mcp-' + name + '.jwt'), 'utf8')).trim();
const mutations = () => requests.filter(request => mutationTools.has(request.tool)).length;
try {
  const missing = (requirements[mode] || []).filter(key => typeof selectors[key] !== 'string' || !selectors[key]);
  if (!requirements[mode] || missing.length) {
    console.log(JSON.stringify({ status: 'BLOCKED', reason: 'New UI handoff selectors missing: ' + missing.join(', '), mode }));
  } else {
    let chromium;
    try {
      ({ chromium } = await import(pathToFileURL(process.env.E2E_PLAYWRIGHT_MODULE).href));
      browser = await chromium.launch({ headless: true, ...(process.env.E2E_CHROMIUM_EXECUTABLE
        ? { executablePath: process.env.E2E_CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] } : { channel: 'chrome' }) });
    } catch {
      console.log(JSON.stringify({ status: 'BLOCKED', reason: 'Existing Chrome/Playwright unavailable', mode }));
    }
    if (browser) {
      const context = await browser.newContext({ viewport: mode === 'mobile' ? { width: 390, height: 844 } : { width: 1280, height: 900 } });
      page = await context.newPage(); page.setDefaultTimeout(15000);
      page.on('request', request => {
        if (!request.url().endsWith('/mcp') || request.method() !== 'POST') return;
        const body = request.postDataJSON();
        requests.push({ method: body.method, tool: body.params?.name || null, args: body.method === 'tools/call' ? body.params.arguments : null });
      });
      const control = key => page.locator(selectors[key]);
      await page.goto(process.env.E2E_BASE_URL + '/chat.html');
      const tabTo = async selector => {
        const visited = [];
        for (let step = 0; step < 100; step++) {
          await page.keyboard.press('Tab');
          visited.push(await page.evaluate(() => ({ tag: document.activeElement.tagName, id: document.activeElement.id })));
          if (await page.locator(selector).evaluate(element => document.activeElement === element)) {
            records.push({ keyboardTabTarget: selector, visited }); return;
          }
        }
        assert.fail('Control was not reachable through natural Tab navigation: ' + selector);
      };
      const openManagement = async () => {
        if (!await page.locator('#management-panel').evaluate(element => element.open)) await control('manage_open').click();
        assert.ok(await page.locator('#management-panel').evaluate(element => element.open));
        assert.ok(await page.locator('#manage-first').isVisible());
      };
      const connect = async (name, keyboard = false) => {
        stage = 'connect-' + name;
        if (await page.locator('#login-dialog').isVisible()) await page.locator('#login-cancel').click();
        if (!await page.locator('.advanced-connection').evaluate(element => element.open)) await page.locator('.advanced-connection > summary').click();
        await page.locator('#token').fill(await fixture(name));
        if (keyboard) { await page.locator('#token').press('Tab'); await page.keyboard.press('Enter'); }
        else await page.locator('#connect').click();
        await page.waitForFunction(() => document.getElementById('connection-state').dataset.state === 'ready');
        assert.equal(await page.locator('#token').inputValue(), '');
      };
      const tool = async (name, args, action, error = null) => {
        const responsePromise = page.waitForResponse(response => {
          if (!response.url().endsWith('/mcp')) return false;
          try {
            const body = response.request().postDataJSON(), actual = body?.params?.arguments;
            return body?.method === 'tools/call' && body.params?.name === name && actual &&
              Object.keys(actual).length === Object.keys(args).length && Object.entries(args).every(([key, value]) => actual[key] === value);
          } catch { return false; }
        });
        await action();
        const response = await responsePromise;
        assert.equal(response.status(), 200);
        assert.deepEqual(response.request().postDataJSON().params.arguments, args);
        const result = decode(await response.text()).result;
        if (error) {
          assert.equal(result.isError, true); assert.ok(error.includes(result.content[0].text));
          records.push({ tool: name, args, error: result.content[0].text }); return null;
        }
        assert.equal(result.isError, false);
        assert.deepEqual(result.structuredContent, JSON.parse(result.content[0].text));
        records.push({ tool: name, args, data: result.structuredContent });
        return result.structuredContent;
      };
      const settle = async () => { await page.waitForFunction(() => !document.getElementById('send').disabled); };
      const ownRead = async keyboard => {
        const data = await tool('get_my_access', {}, async () => {
          if (keyboard) { await tabTo(selectors.own_read); await page.keyboard.press('Enter'); }
          else await control('own_read').click();
        });
        await settle();
        await page.waitForFunction(({ selector, id, name }) => {
          const text = document.querySelector(selector)?.textContent || '';
          return text.includes(id) || text.includes(name);
        }, { selector: selectors.graph, id: data.user.id, name: data.user.name });
        return data;
      };
      const catalog = async entity => {
        await control('catalog_entity').selectOption(entity);
        const data = await tool('list_access_entities', { entity }, () => control('catalog_load').click());
        await settle();
        assert.equal(data.entity, entity);
        assert.deepEqual(await control('catalog_items').locator('option').evaluateAll(options => options.map(option => option.value).filter(Boolean)), data.items.map(item => item.id));
        return data.items;
      };
      const detail = async (entity, id) => {
        await control('catalog_items').selectOption(id);
        const data = await tool('get_access_entity', { entity, id }, () => control('catalog_detail_button').click());
        await settle();
        assert.equal(data.item.id, id);
        assert.ok((await control('catalog_detail').innerText()).includes(id));
        return data.item;
      };
      const preview = async operation => {
        await openManagement();
        assert.equal(await page.locator('#manage-operation').inputValue(), operation);
        const data = await tool('get_my_access', {}, () => page.locator('#manage-preview-button').click());
        await page.waitForFunction(() => !document.getElementById('manage-execute').disabled);
        assert.equal(data.user.id, 'admin');
        assert.ok((await page.locator('#manage-summary').innerText()).includes('carol'));
        assert.ok((await page.locator('#manage-summary').innerText()).includes('role-analyst'));
      };
      if (mode === 'guide') {
        stage = 'disconnected-guidance';
        assert.ok(await control('capabilities').isVisible());
        const text = await control('capabilities').innerText();
        for (const concept of ['역할', '정책', '리소스', '조회', '변경']) assert.ok(text.includes(concept), 'Missing guidance: ' + concept);
        assert.ok(/연결/.test(text + await page.locator('#connection-note').innerText()));
        for (const key of ['own_read', 'catalog_load', 'requestable_read', 'manage_open', 'concept_read']) {
          assert.ok(await control(key).isVisible());
          if (await control(key).isEnabled()) {
            await control(key).click();
            await page.locator('#login-dialog').waitFor({ state: 'visible' });
            assert.ok(await page.locator('#login-username').evaluate(element => document.activeElement === element));
            await page.locator('#login-cancel').click();
          }
        }
        assert.equal(mutations(), 0);
        assert.equal(requests.filter(request => request.method === 'tools/call').length, 0);
        records.push({ guidanceVisibleBeforeConnection: true, mutationRequests: 0 });
      } else if (mode === 'graph') {
        await connect('alice'); stage = 'verified-paths';
        const data = await ownRead(false);
        const rows = await control('graph_paths').allTextContents();
        const expected = [];
        for (const resource of data.resources) for (const grant of resource.grants) {
          expected.push([data.user.id, grant.roleId, grant.policyId, resource.id]);
        }
        const actualTuples = rows.map(grantPathTuple);
        assert.deepEqual(actualTuples.map(tuple => JSON.stringify(tuple)).sort(), expected.map(tuple => JSON.stringify(tuple)).sort(), 'Rendered provenance tuple multiset differs from real response');
        assert.ok(!(await control('graph').innerText()).includes('R400'));
        assert.equal(await page.locator('#token').inputValue(), '');
        // Capture only the actual application result pane, excluding connection credentials.
        await page.setViewportSize({ width: 1280, height: 1900 });
        await control('capabilities').scrollIntoViewIfNeeded();
        await page.locator('.chat').screenshot({ path: path.join(process.env.E2E_OUTPUT_DIR, 'final-guide-and-self-access.png') });
        await control('graph').screenshot({ path: path.join(process.env.E2E_OUTPUT_DIR, 'final-grant-paths.png') });
        records.push({ screenshots: ['final-guide-and-self-access.png', 'final-grant-paths.png'], actor: data.user.id,
          action: 'Connected as Alice and clicked the actual own-access capability; captured verified response without modifying page content.' });
        await page.setViewportSize({ width: 1280, height: 900 });
        await control('graph').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(process.env.E2E_OUTPUT_DIR, 'discovery-desktop-paths.png'), fullPage: true });
        await connect('carol'); const empty = await ownRead(false);
        assert.deepEqual(empty.roles, []);
        assert.ok((await control('graph_paths').allTextContents()).every(text => /없|0/.test(text)));
        assert.ok(/없|0/.test(await control('graph').innerText()));
        assert.equal(await page.locator('#token').inputValue(), '');
        await control('graph').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(process.env.E2E_OUTPUT_DIR, 'discovery-desktop-empty.png'), fullPage: true });
        records.push({ actualPaths: expected.length, displayedPaths: rows.length, noRoleEmptyState: true,
          expectedTuples: expected, displayedTuples: actualTuples, screenshots: ['discovery-desktop-paths.png', 'discovery-desktop-empty.png'] });
      } else if (mode === 'catalog') {
        await connect('alice'); stage = 'permitted-catalog';
        const users = await catalog('users'); assert.deepEqual(users.map(user => user.id), ['alice']);
        await detail('users', 'alice');
        const roles = await catalog('roles'); assert.deepEqual(roles.map(role => role.id), ['role-analyst', 'role-editor']);
        await detail('roles', 'role-analyst');
        const publicRoles = await tool('get_requestable_roles', {}, () => control('requestable_read').click());
        await settle();
        for (const role of publicRoles.roles) {
          assert.deepEqual(Object.keys(role).sort(), ['description', 'id', 'name']);
          assert.ok((await control('requestable_view').innerText()).includes(role.id));
        }
        await connect('admin-read-only');
        const allUsers = await catalog('users'); assert.deepEqual(allUsers.map(user => user.id), ['U900', 'admin', 'alice', 'bob', 'carol']);
        await detail('users', 'carol'); assert.equal(mutations(), 0);
      } else if (mode === 'manage') {
        await connect('admin'); stage = 'catalog-to-management';
        await openManagement(); assert.equal(mutations(), 0);
        await page.locator('#manage-operation').selectOption('assign_role_to_user');
        const [userOptions, roleOptions] = await Promise.all([
          tool('list_access_entities', { entity: 'users' }, () => control('manage_load_targets').click()),
          tool('list_access_entities', { entity: 'roles' }, async () => {}),
        ]);
        await settle();
        assert.ok(userOptions.items.some(item => item.id === 'carol'));
        assert.ok(roleOptions.items.some(item => item.id === 'role-analyst'));
        assert.equal(await control('manage_second_select').locator('option[value="SYSTEM_ADMIN"]').isDisabled(), true);
        await control('manage_first_select').selectOption('carol');
        await control('manage_second_select').selectOption('role-analyst');
        assert.equal(await page.locator('#manage-first').inputValue(), 'carol');
        assert.equal(await page.locator('#manage-second').inputValue(), 'role-analyst');
        await preview('assign_role_to_user'); assert.equal(mutations(), 0);
        await page.locator('#manage-cancel').click(); assert.equal(mutations(), 0);
        await preview('assign_role_to_user');
        const changed = await tool('assign_role_to_user', { userId: 'carol', roleId: 'role-analyst' }, () => page.locator('#manage-execute').click());
        assert.equal(changed.present, true); assert.equal(mutations(), 1); await settle();
        assert.equal(changed.changed, true);
        assert.equal(await page.locator('#token').inputValue(), '');
        await page.setViewportSize({ width: 1280, height: 1900 });
        if (await control('capabilities').evaluate(element => element.open)) await control('capabilities').locator(':scope > summary').click();
        await page.locator('#management-panel').scrollIntoViewIfNeeded();
        await page.locator('#messages').locator(':scope > :first-child').scrollIntoViewIfNeeded();
        assert.ok((await page.locator('#messages').innerText()).includes('변경됨'));
        await page.locator('.chat').screenshot({ path: path.join(process.env.E2E_OUTPUT_DIR, 'final-explicit-management-success.png') });
        records.push({ screenshot: 'final-explicit-management-success.png', actor: 'admin',
          action: 'Selected Carol and analyst role from real catalogs, previewed, then explicitly executed once.',
          observedMutation: changed, mutationRequestsAtCapture: mutations() });
        await page.setViewportSize({ width: 1280, height: 900 });
        await control('capabilities').locator(':scope > summary').click();
        await catalog('users'); const carol = await detail('users', 'carol'); assert.ok(carol.roleIds.includes('role-analyst'));
        await page.locator('#manage-operation').selectOption('remove_role_from_user');
        await page.locator('#manage-first').fill('carol'); await page.locator('#manage-second').fill('role-analyst');
        await preview('remove_role_from_user');
        const removed = await tool('remove_role_from_user', { userId: 'carol', roleId: 'role-analyst' }, () => page.locator('#manage-execute').click());
        assert.equal(removed.present, false); await settle();
        assert.equal(mutations(), 2, 'Exactly one add and one remove request required');
        records.push({ catalogSelectionAndPreviewWrites: 0, explicitExecuteRequests: mutations(), reflectedTargetState: carol.roleIds });
      } else if (mode === 'switch') {
        await connect('alice'); const alice = await ownRead(false); await catalog('users'); await detail('users', 'alice');
        await page.locator('#token').fill(await fixture('bob'));
        for (const key of ['graph', 'catalog_detail', 'selection']) {
          const text = key === 'selection' ? await control(key).inputValue() : await control(key).innerText();
          assert.ok(!text.includes('alice') && !text.includes('R300'), 'Stale actor data in ' + key);
        }
        assert.equal(await page.locator('#manage-execute').isEnabled(), false);
        await connect('bob'); const bob = await ownRead(false);
        assert.notDeepEqual(bob.resources, alice.resources); assert.deepEqual(bob.resources.map(r => r.id), ['R100', 'R400']);
        await connect('admin-read-only');
        await openManagement();
        await page.locator('#manage-operation').selectOption('assign_role_to_user');
        await page.locator('#manage-first').fill('carol'); await page.locator('#manage-second').fill('role-analyst');
        await preview('assign_role_to_user');
        await tool('assign_role_to_user', { userId: 'carol', roleId: 'role-analyst' }, () => page.locator('#manage-execute').click(), ['FORBIDDEN']);
        await settle();
        await connect('carol'); const noRole = await ownRead(false); assert.deepEqual(noRole.roles, []);
        await page.locator('#disconnect').click();
        assert.equal(await page.locator('#token').inputValue(), '');
        assert.ok(!(await control('graph').innerText()).includes('R400'));
        assert.equal(mutations(), 1); assert.equal(records.filter(record => mutationTools.has(record.tool) && !record.error).length, 0);
      } else if (mode === 'mobile') {
        stage = 'mobile-keyboard';
        assert.ok(await control('capabilities').isVisible());
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Page overflows mobile viewport');
        await connect('alice', true); const data = await ownRead(true); assert.equal(data.user.id, 'alice');
        await tabTo('#relationship-title');
        assert.equal(await control('graph').evaluate(element => element.open), true);
        await page.keyboard.press('Enter');
        assert.equal(await control('graph').evaluate(element => element.open), false);
        await page.keyboard.press('Space');
        assert.equal(await control('graph').evaluate(element => element.open), true);
        records.push({ relationshipSummaryNaturalTab: true, enterCloses: true, spaceOpens: true });
        for (const key of ['own_read', 'catalog_entity']) {
          await tabTo(selectors[key]); assert.ok(await control(key).evaluate(element => document.activeElement === element));
        }
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        assert.equal(await page.locator('#token').inputValue(), '');
        const screenshot = path.join(process.env.E2E_OUTPUT_DIR, 'discovery-mobile.png');
        await page.screenshot({ path: screenshot, fullPage: true });
        records.push({ viewport: '390x844', keyboardRead: true, screenshot: 'discovery-mobile.png', mutationRequests: mutations() });
        assert.equal(mutations(), 0);
      }
      console.log(JSON.stringify({ status: 'PASS', mode, records, requests }));
    }
  }
} catch (error) {
  console.log(JSON.stringify({ status: 'FAIL', mode, stage, records, requests, errorType: error.name,
    reason: error.code === 'ERR_ASSERTION' ? String(error.message).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, '[REDACTED JWT]') : 'New UI operation failed or timed out' }));
} finally { if (browser) await browser.close(); }
