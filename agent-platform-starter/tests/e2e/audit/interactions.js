// UI audit, part 2: interactions on every view. Prints OBS lines (observations) for the report.
// Mutates seeded state: restart the preview server before re-running.
// Usage: BASE=http://127.0.0.1:8771 node tests/e2e/audit/interactions.js
const { chromium } = require('playwright');
const path = require('path');
const out = path.join(__dirname, 'shots');
const base = process.env.BASE || 'http://127.0.0.1:8771';
const obs = (k, v) => console.log('OBS ' + k + ': ' + (typeof v === 'string' ? v : JSON.stringify(v)));
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [], requests = [], dialogs = [];
  let dialogAnswer = null;
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  page.on('response', r => { if (r.status() >= 400) errors.push('HTTP ' + r.status() + ' ' + r.request().method() + ' ' + r.url()); });
  page.on('request', r => { if (r.url().includes('/ui/api/')) requests.push(r.method() + ' ' + r.url().split('/ui/api/')[1]); });
  page.on('dialog', d => { dialogs.push(d.type() + ': ' + d.message().slice(0, 120)); d.accept(dialogAnswer != null ? dialogAnswer : (d.type() === 'prompt' ? d.defaultValue() : undefined)); });
  const notice = () => page.textContent('#notice');
  const focusDesc = () => page.evaluate(() => { const a = document.activeElement; return a ? a.tagName + (a.id ? '#' + a.id : '') + (a.className ? '.' + String(a.className).split(' ').join('.') : '') + ' "' + (a.textContent || '').trim().slice(0, 30) + '"' : null; });

  await page.goto(base + '/ui/');
  // Login with wrong password first.
  await page.fill('#password', 'nope'); await page.click('#login-form button[type=submit]'); await sleep(500);
  obs('login wrong password message', await page.textContent('#login-error'));
  await page.fill('#password', 'pw'); await page.click('#login-form button[type=submit]');
  await page.waitForSelector('#workspace:not([hidden])'); await sleep(800);
  obs('focus after login', await focusDesc());
  obs('model override options after fresh login (overview)', await page.$$eval('#task-model-override option', o => o.map(x => x.value || '(default)')));
  obs('model override options after fresh login (thread)', await page.$$eval('#thread-model-override option', o => o.map(x => x.value || '(default)')));
  obs('task-agent options after fresh login', await page.$$eval('#task-agent option', o => o.map(x => x.textContent)));

  // ---------- Overview: run a task with empty and valid text
  await page.click('#submit-task'); await sleep(300);
  obs('overview empty submit validation', await page.evaluate(() => document.querySelector('#task-text').validationMessage));
  await page.fill('#max-tokens', '999999'); await page.fill('#task-text', 'Summarise the Proxmox inventory.');
  await page.click('#submit-task'); await sleep(300);
  obs('overview max-tokens 999999 validation', await page.evaluate(() => document.querySelector('#max-tokens').validationMessage));
  await page.fill('#max-tokens', '4000'); await page.click('#submit-task');
  const t0 = Date.now(); const states = [];
  for (let i = 0; i < 16; i++) { await sleep(500); states.push(((Date.now() - t0) / 1000).toFixed(1) + 's:' + (await page.textContent('#stream-status')) + '|btnDisabled=' + (await page.isDisabled('#submit-task'))); }
  obs('overview task run status timeline', states);
  await page.screenshot({ path: out + '/i-overview-after-run.png', fullPage: true });

  // ---------- Tasks
  await page.click('button[data-view="tasks"]'); await sleep(800);
  await page.fill('#task-search', 'jq'); await sleep(900);
  obs('tasks search jq rows', await page.$$eval('#task-rows tr', r => r.length));
  obs('tasks page label after search', await page.textContent('#task-page'));
  await page.fill('#task-search', 'zzzz-nothing'); await sleep(900);
  obs('tasks no-match text', await page.textContent('#task-rows'));
  await page.fill('#task-search', ''); await sleep(900);
  await page.selectOption('#task-status-filter', 'failed'); await sleep(700);
  obs('tasks filter failed statuses', await page.$$eval('#task-rows .pill', p => [...new Set(p.map(x => x.textContent))]));
  await page.selectOption('#task-status-filter', ''); await sleep(700);
  await page.click('#task-next'); await sleep(700);
  obs('tasks page 2 label', await page.textContent('#task-page'));
  obs('tasks scrollY after paging (top of table expected)', await page.evaluate(() => scrollY));
  await page.click('#task-prev'); await sleep(700);
  // Keyboard: focus first row, Enter, Tab around in drawer, Escape.
  await page.focus('#task-rows tr'); await page.keyboard.press('Enter'); await sleep(800);
  obs('drawer focus on open', await focusDesc());
  const trail = [];
  for (let i = 0; i < 25; i++) { await page.keyboard.press('Tab'); trail.push(await page.evaluate(() => !!document.activeElement.closest('.drawer-panel'))); }
  obs('drawer focus trap: focus inside drawer after each Tab', trail.map(x => x ? 1 : 0).join(''));
  await page.keyboard.press('Shift+Tab');
  await page.focus('#close-detail');
  await page.keyboard.press('Escape'); await sleep(300);
  obs('drawer hidden after Escape', await page.isHidden('#detail'));
  obs('focus after Escape', await focusDesc());
  // Drawer for a task needing approval.
  await page.selectOption('#task-status-filter', 'human_approval_required'); await sleep(700);
  await page.click('#task-rows tr'); await sleep(800);
  await page.screenshot({ path: out + '/i-drawer-approval.png' });
  obs('drawer approval card visible', await page.isVisible('#hla-approval-actions .approval-card'));
  obs('drawer: Continue in conversation shown for approval task', await page.isVisible('#hla-continue-task'));
  obs('drawer Raw toggle works', await (async () => { await page.click('#detail-raw-btn'); await sleep(200); const r = await page.isVisible('#detail-raw'); const p = await page.getAttribute('#detail-raw-btn', 'aria-pressed'); await page.click('#detail-preview-btn'); return { rawVisible: r, ariaPressed: p }; })());
  await page.click('#detail-backdrop', { position: { x: 50, y: 400 } }); await sleep(300);
  obs('drawer closes on backdrop', await page.isHidden('#detail'));
  await page.selectOption('#task-status-filter', ''); await sleep(500);

  // ---------- Knowledge
  await page.click('button[data-view="knowledge"]'); await sleep(900);
  await page.click('#kb-form button[type=submit]'); await sleep(200);
  obs('kb empty submit validation', await page.evaluate(() => document.querySelector('#kb-title').validationMessage));
  await page.fill('#kb-title', 'Proxmox backup schedule'); await page.fill('#kb-body', 'Backups run nightly at 02:00 to pbs-01.');
  await page.click('#kb-form button[type=submit]'); await sleep(800);
  obs('kb after save notice', await notice());
  obs('kb notice visible in viewport after save', await page.evaluate(() => { const r = document.getElementById('notice').getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; }));
  obs('kb entries', await page.$$eval('.kb-entry:not(.fact) strong', e => e.map(x => x.textContent)));
  await page.click('.kb-entry:not(.fact) button:has-text("Edit")'); await sleep(500);
  obs('kb edit: form title', await page.textContent('#kb-form-title'));
  obs('kb edit: submit button label still', await page.textContent('#kb-form button[type=submit]'));
  obs('kb edit: clear button label', await page.textContent('#kb-cancel'));
  await page.fill('#kb-body', 'Backups run nightly at 03:00 to pbs-01.'); await page.click('#kb-form button[type=submit]'); await sleep(800);
  obs('kb entries after edit', await page.$$eval('.kb-entry:not(.fact)', e => e.map(x => x.textContent.slice(0, 80))));
  // Search with Enter key in the knowledge search box
  await page.fill('#kb-search', 'nomatch'); await page.press('#kb-search', 'Enter'); await sleep(700);
  obs('kb Enter in search triggers search (entries shown)', await page.$$eval('#kb-list .kb-entry', e => e.length));
  await page.click('#kb-refresh'); await sleep(600);
  obs('kb after Search button no-match text', await page.textContent('#kb-list'));
  await page.fill('#kb-search', ''); await page.click('#kb-refresh'); await sleep(600);
  await page.click('.kb-entry:not(.fact) button:has-text("Delete")'); await sleep(800);
  obs('kb after delete', await page.textContent('#kb-list'));
  // Facts
  await page.fill('#fact-search', 'jq'); await sleep(900);
  obs('facts search jq', await page.$$eval('.fact code', e => e.map(x => x.textContent)));
  await page.fill('#fact-search', ''); await sleep(900);
  obs('fact-state option labels', await page.$$eval('#fact-state option', o => o.map(x => x.textContent)));
  await page.click('.fact:has-text("htop") button:has-text("Delete")'); await sleep(700);
  obs('facts after delete htop', await page.$$eval('.fact code', e => e.map(x => x.textContent)));
  obs('fact-state counts after delete (stale?)', await page.$$eval('#fact-state option', o => o.map(x => x.textContent)));
  await page.click('.fact .link-button'); await sleep(900);
  obs('fact Open task opens drawer', await page.isVisible('#detail'));
  obs('fact Open task: #detail hidden attr / body.drawer-open / focus', [await page.evaluate(() => document.getElementById('detail').hidden), await page.evaluate(() => document.body.classList.contains('drawer-open')), await focusDesc()]);
  obs('fact Open task: page can still scroll?', await page.evaluate(() => { const y = scrollY; scrollBy(0, 400); const moved = scrollY !== y; scrollTo(0, y); return moved + ' overflow=' + getComputedStyle(document.body).overflow; }));
  await page.keyboard.press('Escape'); await sleep(300);

  // ---------- Agents
  await page.click('button[data-view="agents"]'); await sleep(900);
  await page.click('#agent-form button[type=submit]'); await sleep(200);
  obs('agent empty submit validation', await page.evaluate(() => document.querySelector('#agent-name').validationMessage));
  await page.fill('#agent-name', 'Docs Agent'); await page.fill('#agent-budget', '500');
  await page.click('#agent-form button[type=submit]'); await sleep(400);
  obs('agent budget 500 validation', await page.evaluate(() => document.querySelector('#agent-budget').validationMessage));
  await page.fill('#agent-budget', '0.5'); await page.click('#agent-form button[type=submit]'); await sleep(900);
  obs('agent create notice', await notice());
  obs('agent cards', await page.$$eval('.agent-card h3', e => e.map(x => x.textContent)));
  obs('agent card info for role-less agent', await page.$$eval('.agent-card p', e => e.map(x => x.textContent)));
  await page.click('.agent-card:has-text("Docs Agent") button:has-text("Edit")'); await sleep(800);
  obs('agent edit form title / submit label', [await page.textContent('#agent-form-title'), await page.textContent('#agent-form button[type=submit]')]);
  await page.screenshot({ path: out + '/i-agent-edit.png' });
  await page.click('#agent-clear');
  // overview/thread agent selectors pick up new agent?
  obs('overview task-agent options after creating agent', await page.$$eval('#task-agent option', o => o.map(x => x.textContent)));

  // ---------- Queue
  await page.click('button[data-view="queue"]'); await sleep(900);
  await page.fill('#queue-text', 'Write a haiku about backups.'); await page.click('#queue-form button[type=submit]'); await sleep(1200);
  obs('queue notice', await notice());
  obs('queue jobs', await page.$$eval('#queue-jobs .kb-entry', e => e.map(x => x.textContent.slice(0, 120))));
  await page.screenshot({ path: out + '/i-queue.png', fullPage: true });
  requests.length = 0; await page.click('#refresh'); await sleep(900);
  obs('header Refresh on Queue view: API calls made', requests);
  obs('header Refresh notice', await notice());
  const cancel = await page.$('#queue-jobs button:has-text("Cancel")');
  if (cancel) { await cancel.click(); await sleep(700); obs('queue after cancel', await page.$$eval('#queue-jobs .kb-entry strong', e => e.map(x => x.textContent))); obs('queue cancel notice', await notice()); }

  // ---------- Infrastructure
  await page.click('button[data-view="infrastructure"]'); await sleep(900);
  obs('infra message', await page.textContent('#infra-message'));
  obs('infra Nodes/Guests contents', [await page.textContent('#infra-nodes'), await page.textContent('#infra-guests')]);

  // ---------- Policy
  await page.click('button[data-view="policy"]'); await sleep(1200);
  await page.selectOption('select[data-server="dev"]', '2'); await sleep(100);
  await page.click('button[data-view="overview"]'); await page.click('button[data-view="policy"]'); await sleep(1000);
  obs('policy unsaved change survives view switch (dev select value)', await page.inputValue('select[data-server="dev"]'));
  await page.selectOption('select[data-server="dev"]', '2');
  obs('policy save button indicates dirty state?', await page.evaluate(() => ({ text: document.getElementById('policy-save').textContent, disabled: document.getElementById('policy-save').disabled })));
  await page.click('#policy-reset'); await sleep(200);
  obs('policy discard resets select', await page.inputValue('select[data-server="dev"]'));
  obs('policy discard feedback notice', await notice());
  await page.click('#policy-save'); await sleep(400);
  obs('policy save with no changes notice', await notice());
  await page.selectOption('select[data-server="dev"]', '3'); dialogs.length = 0;
  await page.click('#policy-save'); await sleep(900);
  obs('policy save dialogs', dialogs); obs('policy save notice', await notice());
  obs('policy notice visible in viewport', await page.evaluate(() => { const r = document.getElementById('notice').getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; }));
  // compose projects invalid value
  await page.fill('input[data-projects="dev"]', 'not a path!!'); await page.click('#policy-save'); await sleep(900);
  obs('policy invalid compose project notice', await notice());
  await page.click('#policy-reset');
  // unhinged
  dialogs.length = 0; await page.click('#unhinged-toggle'); await sleep(900);
  obs('unhinged on: banner visible / pill', [await page.isVisible('#unhinged-banner'), await page.textContent('#unhinged-state'), await notice()]);
  await page.screenshot({ path: out + '/i-unhinged-on.png' });
  await page.setViewportSize({ width: 390, height: 844 }); await sleep(300);
  await page.screenshot({ path: out + '/i-unhinged-on-390.png' });
  await page.setViewportSize({ width: 1440, height: 900 });
  dialogs.length = 0; await page.click('#unhinged-banner-off'); await sleep(900);
  obs('unhinged off via banner: dialogs (confirm?)', dialogs);
  obs('unhinged off result', [await page.isVisible('#unhinged-banner'), await page.textContent('#unhinged-state')]);
  // sandbox reset
  await page.click('#sandbox-reset'); await sleep(300);
  obs('sandbox resetting state', [await page.textContent('#sandbox-status'), await page.isDisabled('#sandbox-reset')]);
  await sleep(6000);
  obs('sandbox after reset', [await page.textContent('#sandbox-status'), await notice()]);
  // register app validation
  await page.click('#deploy-register summary'); await sleep(200);
  await page.fill('#dep-name', 'Bad Name'); await page.fill('#dep-path', 'relative/path'); await page.fill('#dep-repo', 'notarepo');
  await page.click('#deploy-form button[type=submit]'); await sleep(400);
  obs('register app: name validation message', await page.evaluate(() => document.querySelector('#dep-name').validationMessage));
  await page.fill('#dep-name', 'blog'); await page.click('#deploy-form button[type=submit]'); await sleep(900);
  obs('register app with relative path / bad repo: notice', await notice());
  await page.screenshot({ path: out + '/i-register-app.png', fullPage: true });
  await page.fill('#dep-path', '/opt/blog'); await page.fill('#dep-repo', 'git@github.com:example/blog.git'); await page.fill('#dep-health', 'not a url');
  await page.click('#deploy-form button[type=submit]'); await sleep(900);
  obs('register app with bad health url: notice', await notice());
  obs('apps listed', await page.$$eval('.deploy-app strong', e => e.map(x => x.textContent)));
  // deploy: cancel prompt, then empty, then real
  dialogAnswer = ''; dialogs.length = 0; await page.click('.deploy-app:has-text("site") button:has-text("Deploy")'); await sleep(500);
  obs('deploy with empty ref: dialogs/notice', [dialogs, await notice()]);
  dialogAnswer = null; dialogs.length = 0;
  const td = Date.now(); await page.click('.deploy-app:has-text("site") button:has-text("Deploy")');
  const dstates = [];
  for (let i = 0; i < 10; i++) { await sleep(700); dstates.push(((Date.now() - td) / 1000).toFixed(1) + 's notice=' + (await notice()) + ' | first row=' + (await page.textContent('#deploy-history tr:first-child')).slice(0, 80) + ' | deployBtnDisabled=' + (await page.isDisabled('.deploy-app:has-text("site") button:has-text("Deploy")'))); }
  obs('deploy timeline', dstates);
  // Double-deploy while running
  dialogs.length = 0;
  await page.click('.deploy-app:has-text("site") button:has-text("Deploy")'); await sleep(200);
  await page.click('.deploy-app:has-text("site") button:has-text("Deploy")'); await sleep(4500);
  obs('two deploys back to back: notice', await notice());
  await page.waitForTimeout(4000);
  const rb = await page.$('#deploy-history button:has-text("Roll back")');
  if (rb) { dialogs.length = 0; await rb.click(); await sleep(500); obs('rollback dialogs + immediate notice', [dialogs, await notice()]); await sleep(4000); obs('rollback result first row', await page.textContent('#deploy-history tr:first-child')); }
  await page.screenshot({ path: out + '/i-deploy-history.png', fullPage: true });

  // ---------- Nav a11y snapshot at phone width
  await page.setViewportSize({ width: 390, height: 844 }); await sleep(300);
  obs('nav accessible names at 390', (await page.accessibility.snapshot({ root: await page.$('aside') })).children.map(c => c.role + ':' + c.name));
  obs('infrastructure nav button in viewport at 390', await page.evaluate(() => { const r = document.querySelector('button[data-view=infrastructure]').getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), innerWidth]; }));
  await page.setViewportSize({ width: 1440, height: 900 });
  // Focus visibility on nav buttons
  await page.focus('button[data-view="tasks"]');
  await page.keyboard.press('Tab'); await page.keyboard.press('Shift+Tab');
  obs('nav focus outline', await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle + ' ' + getComputedStyle(document.activeElement).outlineColor));
  obs('active nav has aria-current', await page.evaluate(() => document.querySelector('button.nav.active').getAttribute('aria-current')));

  console.log('ERRORS ' + JSON.stringify([...new Set(errors)], null, 1));
  await browser.close();
})();
