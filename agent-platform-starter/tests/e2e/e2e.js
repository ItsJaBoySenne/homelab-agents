// Browser E2E against the preview server (real backend, mocked model providers).
const { chromium } = require('playwright');
let failures = 0;
const check = (cond, label) => { console.log((cond ? 'PASS ' : 'FAIL ') + label); if (!cond) failures++; };
// In-page dialogs (HLA_DIALOGS_V1). The browser's own confirm/prompt must never appear: the guard fails the run.
const DLG = 'dialog.ui-dialog[open]';
const nativeGuard = pg => pg.on('dialog', d => { check(false, 'no native ' + d.type() + ' dialog: ' + d.message().slice(0, 80)); d.dismiss().catch(() => {}); });
const openDialog = pg => pg.waitForSelector(DLG, { timeout: 8000 });
// Clicks the dialog's confirm button; with `ok`, also checks that it is named for the action.
async function confirmDialog(pg, ok) {
  const d = await openDialog(pg);
  const label = await d.$eval('.ui-dialog-ok', b => b.textContent);
  if (ok !== undefined) check(label === ok, 'dialog button reads "' + ok + '"' + (label === ok ? '' : ' (got "' + label + '")'));
  await (await d.$('.ui-dialog-ok')).click();
  await d.waitForElementState('hidden');
  return label;
}
// Fills fields by name (inputs, textareas, selects), then confirms.
async function fillDialog(pg, values, ok) {
  const d = await openDialog(pg);
  for (const [name, v] of Object.entries(values)) {
    const f = await d.$('[name="' + name + '"]');
    if (await f.evaluate(e => e.tagName === 'SELECT')) await f.selectOption(String(v)); else await f.fill(String(v));
  }
  return confirmDialog(pg, ok);
}
async function cancelDialog(pg) { const d = await openDialog(pg); await pg.keyboard.press('Escape'); await d.waitForElementState('hidden'); }
// HLA_UI_V3: pages are reached the way a person does: the section in the sidebar (or the phone's tab bar), then
// the page's tab. Home is a section of its own.
async function go(pg, view) {
  const sec = await pg.evaluate(v => sectionOf(v), view);
  await pg.click(`aside .nav-section[data-section="${sec}"]`);
  if (view === 'overview') return;
  const tab = `#subnav button[data-view="${view}"]`;
  await pg.waitForSelector(tab, { state: 'visible', timeout: 5000 });
  await pg.click(tab);
}
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  // HLA_UI_V3: console errors other than failed requests (several checks provoke 4xx answers on purpose).
  const consoleErrors = []; page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text()); });
  nativeGuard(page);
  await page.goto((process.env.BASE || 'http://127.0.0.1:8765') + '/ui/');
  await page.fill('#password', 'pw'); await page.click('#login-form button[type=submit]');
  await page.waitForSelector('#workspace:not([hidden])');

  // "Needs you" inbox on Overview: everything waiting for a person, counted on the nav and in the tab title.
  await page.waitForSelector('#inbox-list .inbox-item');
  check(await page.$$eval('#inbox-list .inbox-item', x => x.length) <= 3, 'inbox shows at most three items before "Show all"');
  // More than six items fold behind "Show all".
  if (await page.isVisible('.inbox-more:has-text("Show all")')) await page.click('.inbox-more');
  const inboxN = await page.$$eval('#inbox-list .inbox-item', x => x.length);
  check(inboxN >= 4 && await page.isVisible('.inbox-command:has-text("setfacl -m g:files:rwx")') && await page.isVisible('.inbox-plan:has-text("Bakery website")')
        && await page.isVisible('.inbox-approval:has-text("Fileserver delete permissions")'), 'inbox lists the command, the paused request and the plan (' + inboxN + ')');
  // A finished project with an open pull request: "PR ready to merge" links to GitHub.
  check(await page.isVisible('.inbox-pr:has-text("PR ready to merge"):has-text("PR #12 · example/site")')
        && await page.getAttribute('.inbox-pr a:has-text("Open PR")', 'href') === 'https://github.com/example/site/pull/12', 'inbox: a PR ready to merge links to the PR');
  const inboxCount = await page.textContent('#inbox-count');
  check(Number(inboxCount) >= inboxN && await page.textContent('#nav-inbox-badge') === inboxCount && (await page.title()).startsWith('(' + inboxCount + ')'), 'inbox count on the nav and in the tab title');
  // Paused tasks from Overview: one opens its drawer; more than two are grouped and open Task history filtered on them.
  if (await page.isVisible('.inbox-approval button:has-text(" tasks")')) {
    await page.click('.inbox-approval button:has-text(" tasks")');
    await page.waitForSelector('#task-rows .pill');
    check(await page.inputValue('#task-status-filter') === 'human_approval_required' && (await page.$$eval('#task-rows .pill', p => [...new Set(p.map(x => x.textContent))])).join() === 'Needs approval',
          'grouped paused tasks open Task history filtered on them');
    await page.click('.work-filters button[data-status=""]');
    await go(page, 'overview');
  } else {
    await page.click('.inbox-approval:has-text("Task history") button:has-text("Review request")');
    await page.waitForSelector('#detail:not([hidden]) .approval-card');
    check(true, 'a paused task opens in the drawer from the inbox');
    await page.keyboard.press('Escape');
  }
  check(!(await page.evaluate(async () => { try { await api('threads', { method: 'POST', headers: UI_HEADERS, body: '{"title":""}' }) } catch (e) { return e.message } })).includes('[object'),
        'validation errors read as text');
  await page.click('.inbox-command:has-text("setfacl") button:has-text("Review command")');
  await page.waitForSelector('#thread-heading:has-text("Shared folder ACL")');
  check(await page.isVisible('.command-card .pill:has-text("Command awaiting approval")') && !(await page.isHidden('#threads')), 'inbox opens the conversation with the waiting command');
  await go(page, 'overview');
  await page.click('.inbox-plan button:has-text("Review plan")');
  await page.waitForSelector('.project-head:has-text("Bakery website") .pill:has-text("Plan ready")');
  check(true, 'inbox opens the plan to review');

  // Conversation: approve read-only from the card, result lands in the same thread.
  await go(page, 'threads');
  await page.click('.thread-item:has-text("Fileserver")');
  await page.waitForSelector('.approval-human_approval_required');
  // A re-render (live updates, polling) keeps a half-typed rejection reason.
  await page.fill('.approval-card input', 'typed before a refresh');
  await page.evaluate(() => openThread(currentThread));
  check(await page.inputValue('.approval-card input') === 'typed before a refresh', 'typed approval reason survives a re-render');
  check(await page.isVisible('.thread-item.active:has-text("Fileserver")'), 'selected conversation highlighted');
  const before = await page.$$eval('#thread-messages .msg', m => m.length);
  // The in-page dialog: wide on a desktop, labelled, Escape cancels (nothing decided), Enter confirms.
  await page.click('.approval-card button:has-text("Approve read-only")');
  const dlg = await openDialog(page);
  const dlgBox = await dlg.boundingBox();
  const dlgA11y = await dlg.evaluate(d => ({ title: document.getElementById(d.getAttribute('aria-labelledby'))?.textContent,
    desc: (d.getAttribute('aria-describedby') || '').split(' ').map(id => document.getElementById(id)?.textContent || '').join(' '),
    focus: document.activeElement.classList.contains('ui-dialog-ok') }));
  check(dlgBox.width >= 560 && dlgBox.width <= 642, 'dialog is wide on a 1440 screen (' + Math.round(dlgBox.width) + 'px)');
  check(dlgA11y.title === 'Approve read-only' && /read-only diagnostics/.test(dlgA11y.desc) && dlgA11y.focus,
        'dialog is labelled and described; focus starts on the confirm button ' + JSON.stringify(dlgA11y));
  await page.keyboard.press('Escape');
  await dlg.waitForElementState('hidden');
  await page.waitForTimeout(300);
  check(await page.isVisible('.approval-card .pill:has-text("Approval required")') && await page.isEnabled('.approval-card button:has-text("Approve read-only")'),
        'Escape cancels the dialog: nothing is decided');
  await page.click('.approval-card button:has-text("Approve read-only")');
  await openDialog(page);
  await page.keyboard.press('Enter');
  await page.waitForSelector('.approval-card .pill:has-text("Approved · completed")', { timeout: 15000 });
  check(await page.isVisible('.approval-card:has-text("Approved by dashboard_session · read-only")'), 'card shows read-only decision and approver');
  await page.waitForFunction(n => document.querySelectorAll('#thread-messages .msg').length > n, before, { timeout: 15000 });
  const last = await page.$eval('#thread-messages .msg:last-child', e => e.textContent);
  check(last.includes('Fileserver permissions'), 'approved result appended to thread');
  check(!(await page.isVisible('.thread-item:has-text("Fileserver") .badge-warn')), 'pending badge cleared');

  // Approve-in-chat: the proposed command runs once, its output is posted, and the agent checks it.
  await page.click('.thread-item:has-text("Shared folder ACL")');
  await page.waitForSelector('.command-card .pill:has-text("Command awaiting approval")');
  check(await page.isVisible('.command-card code:has-text("setfacl -m g:files:rwx")'), 'command card shows the exact command');
  check(await page.isVisible('.thread-item:has-text("Shared folder ACL") .badge-warn'), 'conversation list flags the waiting command');
  await page.click('.command-card button:has-text("Approve & run once")');
  check(/setfacl -m g:files:rwx/.test(await (await openDialog(page)).$eval('.ui-dialog-detail', e => e.textContent)), 'the run-command dialog shows the exact command');
  await confirmDialog(page, 'Run once');
  await page.waitForSelector('.msg.live', { timeout: 5000 });
  await page.waitForSelector('.msg-command:has-text("Command output")', { timeout: 15000 });
  await page.waitForSelector('#thread-messages .msg:last-child.msg-agent:not(.live)', { timeout: 20000 });
  check(await page.isVisible('.command-card .pill:has-text("Ran · exit 0")'), 'card shows the outcome');
  check(await page.isVisible('.command-card:has-text("Approved by dashboard_session")'), 'card shows the approver');
  check(!(await page.isVisible('.command-card button')), 'no buttons after the decision');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/command-approved.png', fullPage: false });

  // Facts on the Knowledge page (its second tab): freshness, previous value, filter.
  await go(page, 'knowledge');
  await page.click('#kb-tabs button[data-tab="facts"]');
  await page.waitForSelector('.fact');
  check(await page.isVisible('.fact.fact-stale:has-text("diag:dev:disk") .fact-pill:has-text("Stale")'), 'stale fact flagged');
  check(await page.isVisible('.fact:has-text("/srv/files/shared") summary:has-text("previous value")'), 'changed fact shows its previous value');
  await page.selectOption('#fact-state', 'unverified');
  await page.waitForFunction(() => [...document.querySelectorAll('.fact')].every(f => f.classList.contains('fact-unverified')) && document.querySelectorAll('.fact').length === 1);
  check(await page.isVisible('.fact:has-text("NOT verified")'), 'freshness filter');
  if (process.env.SHOTS) { await page.selectOption('#fact-state', ''); await page.waitForTimeout(500); await page.screenshot({ path: process.env.SHOTS + '/facts.png', fullPage: true }); }
  await page.selectOption('#fact-state', '');
  // Re-check now: the stale diagnostic runs again (read-only) and the fact is fresh, marked as re-checked.
  await page.waitForSelector('#fact-recheck-status:has-text("can be re-checked")');
  check(await page.isVisible('#fact-recheck-status:has-text("Weekly re-check is off")'), 're-check: weekly schedule shown as off by default');
  await page.click('#fact-recheck');
  await page.waitForSelector('.fact:has-text("diag:dev:disk"):has-text("re-checked")', { timeout: 10000 });
  check(await page.isVisible('.fact.fact-fresh:has-text("diag:dev:disk") .fact-pill:has-text("Fresh")') && await page.isVisible('#fact-recheck-status:has-text("Last run")'),
        're-check now refreshes the stale fact and reports the run');

  // Gateway audit log: rows with agent, server, action and masked commands; filters; details with links.
  // HLA_UI_V3: five sections in the sidebar; every page is a tab of its section and also opens from a link.
  const secs = await page.$$eval('aside .side-nav .nav-section', b => b.filter(x => x.offsetParent).map(x => x.textContent.trim().replace(/\d+$/, '')));
  check(JSON.stringify(secs) === JSON.stringify(['Home', 'Chief', 'Work', 'Infrastructure', 'Settings']) && await page.isHidden('#nav-more'),
        'desktop sidebar: five sections, nothing else ' + JSON.stringify(secs));
  const allViews = { chief: 'chief', threads: 'chief', tasks: 'work', projects: 'work', queue: 'work', infrastructure: 'infrastructure', websites: 'infrastructure',
    agents: 'settings', models: 'settings', knowledge: 'settings', connections: 'settings', mcp: 'settings', policy: 'settings', audit: 'settings', costs: 'settings' };
  const unreachable = [];
  for (const [v, sec] of Object.entries(allViews)) {
    await go(page, v);
    const ok = !(await page.isHidden('#' + v)) && await page.$eval(`aside .nav-section[data-section="${sec}"]`, b => b.classList.contains('active') && b.getAttribute('aria-current') === 'page')
      && await page.$eval(`#subnav button[data-view="${v}"]`, b => b.getAttribute('aria-selected') === 'true')
      && ((await page.textContent('#page-crumb')).length > 0 || (await page.textContent('#page-title')).toLowerCase() === sec);
    if (!ok) unreachable.push(v);
  }
  check(!unreachable.length, 'every page opens from its section and tab, which are marked current ' + JSON.stringify(unreachable));
  // HLA_UI_V4: Settings grouped as AI & Agents, Models & Providers, Knowledge & Memory, Tools & MCP, Connections &
  // Credentials, Security & Permissions, Costs & Usage, Audit & Activity (short tab labels, the page title names the group).
  const groups = [];
  for (const v of ['agents', 'models', 'knowledge', 'mcp', 'connections', 'policy', 'costs', 'audit']) { await go(page, v); groups.push(await page.textContent('#page-title')); }
  const setOrder = await page.$$eval('#subnav .subnav-group[data-section="settings"] button[data-view]', b => b.map(x => x.dataset.view));
  check(JSON.stringify(groups) === JSON.stringify(['AI & Agents', 'Models & Providers', 'Knowledge & Memory', 'Tools & MCP', 'Connections & Credentials', 'Security & Permissions', 'Costs & Usage', 'Audit & Activity'])
        && JSON.stringify(setOrder) === JSON.stringify(['agents', 'models', 'knowledge', 'mcp', 'connections', 'policy', 'costs', 'audit']), 'settings groups in order, each reachable from its tab ' + JSON.stringify(groups));
  // HLA_UI_V5 review fixes: one cyan primary per view, one status source, contrast tokens, no clipped columns,
  // grouped repeated runs, skip link and focus return after the palette.
  const primaries = {};
  for (const v of ['overview', 'chief', 'threads', 'tasks', 'projects', 'queue', 'infrastructure', 'websites', 'agents', 'models', 'knowledge', 'mcp', 'connections', 'policy', 'costs', 'audit']) {
    await go(page, v); await page.waitForTimeout(v === 'infrastructure' || v === 'websites' ? 900 : 400);
    primaries[v] = await page.evaluate(() => [...document.querySelectorAll('main button, main a.button-link')].filter(b => {
      const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0 && /rgb\(39, 199, 232\)|rgb\(75, 211, 239\)/.test(getComputedStyle(b).backgroundColor); }).length);
    if (v === 'chief') await page.click('#car-exit');
  }
  check(Object.values(primaries).every(n => n <= 1), 'at most one cyan primary button per main view ' + JSON.stringify(primaries));
  await go(page, 'overview'); await page.waitForTimeout(600);
  const status = await page.evaluate(() => ({ side: document.getElementById('side-status').dataset.state, home: document.getElementById('home-status').dataset.state,
    sideText: document.getElementById('side-status-text').textContent, homeText: document.getElementById('home-status-text').textContent }));
  check(status.side === status.home && status.homeText.startsWith(status.sideText), 'sidebar status and Home status come from one source ' + JSON.stringify(status));
  const contrast = await page.evaluate(() => {
    const lum = c => { const [r, g, b] = c.match(/\d+/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
    const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
    const probe = (fg, bg) => { const d = document.createElement('div'); d.style.cssText = 'color:var(' + fg + ');background:var(' + bg + ')'; document.body.append(d);
      const cs = getComputedStyle(d); const r = ratio(cs.color, cs.backgroundColor); d.remove(); return r; };
    return Math.min(...['--surface', '--surface-2', '--elevated', '--bg', '--sidebar'].flatMap(bg => [probe('--text-3', bg), probe('--text-2', bg)]));
  });
  check(contrast >= 4.5, 'secondary and muted text reach WCAG AA (4.5:1) on every surface (min ' + contrast.toFixed(2) + ')');
  await page.setViewportSize({ width: 1366, height: 768 });
  const clipped = {};
  for (const v of ['tasks', 'audit']) {
    await go(page, v); await page.waitForTimeout(700);
    clipped[v] = await page.evaluate(() => [...document.querySelectorAll('main section:not([hidden]) .table-wrap')].filter(w => w.offsetParent && w.scrollWidth > w.clientWidth + 2).length);
  }
  await go(page, 'tasks'); await page.waitForTimeout(500);
  const grouped = await page.evaluate(() => { const c = document.querySelector('#task-rows .grp-count'); if (!c) return null;
    const tr = c.closest('tr'); const n = Number(c.textContent.replace('×', '')); return { n, hidden: tr._more.filter(x => x.hidden).length }; });
  await page.setViewportSize({ width: 1440, height: 900 });
  check(clipped.tasks === 0 && clipped.audit === 0 && await page.$$eval('.audit-table thead th', t => t.length).catch(() => 6) <= 6, 'no clipped table columns at 1366 (tasks, audit) ' + JSON.stringify(clipped));
  check(!grouped || (grouped.n >= 2 && grouped.hidden === grouped.n - 1), 'repeated runs fold into one row with ×N ' + JSON.stringify(grouped));
  await page.focus('#task-search'); await page.keyboard.press('Control+k'); await page.waitForSelector('#palette');
  await page.keyboard.press('Escape');
  check(await page.evaluate(() => document.activeElement && document.activeElement.id) === 'task-search', 'focus returns to where it was after closing the palette');
  await page.goto((process.env.BASE || 'http://127.0.0.1:8765') + '/ui/'); await page.waitForSelector('#workspace:not([hidden])'); await page.waitForTimeout(600);
  await page.keyboard.press('Tab');
  const skip = await page.evaluate(() => document.activeElement && document.activeElement.id);
  await page.keyboard.press('Enter');
  check(skip === 'skip-link' && await page.evaluate(() => !!document.activeElement.closest('main section')), 'the first Tab stop is "Skip to content", which jumps into the page');
  await go(page, 'overview');
  const viaLink = [];
  for (const [h, v] of [['#/work/queue', 'queue'], ['#/settings/costs', 'costs'], ['#/infrastructure/websites', 'websites'], ['#audit', 'audit'], ['#/chief', 'chief'], ['#/work/projects', 'projects']]) {
    await page.evaluate(h => { location.hash = h; }, h);
    try { await page.waitForSelector('#' + v + ':not([hidden])', { timeout: 4000 }); } catch (e) { viaLink.push(h); }
  }
  check(!viaLink.length && !(await page.evaluate(() => location.hash)), 'deep links #/section/page and #page open the page (and are cleared) ' + JSON.stringify(viaLink));
  if (await page.isVisible('#car-exit')) await page.click('#car-exit');
  // HLA_UI_V3: Ctrl+K command palette: "Ask Chief" comes first, arrows + Enter open a page, Escape closes.
  await go(page, 'overview');
  await page.keyboard.press('Control+k'); await page.waitForSelector('#palette #palette-input');
  check(await page.evaluate(() => document.activeElement && document.activeElement.id === 'palette-input'), 'palette: Ctrl+K opens it with the search focused');
  await page.keyboard.type('queue');
  const palFirst = await page.textContent('#palette .palette-item[aria-selected=true] .pi-label');
  await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
  await page.waitForSelector('#queue:not([hidden])');
  check(palFirst === 'Ask Chief: queue' && await page.isHidden('#palette') && await page.textContent('#page-title') === 'Queue',
        'palette: "Ask Chief" first, then matching pages; Enter opens the selected page (' + palFirst + ')');
  await page.keyboard.press('Control+k'); await page.waitForSelector('#palette');
  await page.keyboard.press('Escape');
  check(await page.isHidden('#palette'), 'palette: Escape closes it');
  const v3Chief = (await (await page.request.get((process.env.BASE || 'http://127.0.0.1:8765') + '/ui/api/chief')).json()).thread_id;
  const palPosts = []; const onPost = r => { if (r.method() === 'POST' && r.url().endsWith('/ui/api/threads/' + v3Chief + '/messages')) palPosts.push(r.postData()); };
  page.on('request', onPost);
  await page.keyboard.press('Control+k'); await page.keyboard.type('What is running right now?'); await page.keyboard.press('Enter');
  await page.waitForSelector('#chief:not([hidden])');
  await page.waitForFunction(() => [...document.querySelectorAll('.car-chief .car-text')].some(e => e.textContent.trim().length > 3), null, { timeout: 15000 });
  page.off('request', onPost);
  check(palPosts.length === 1 && JSON.parse(palPosts[0]).content === 'What is running right now?' && !JSON.parse(palPosts[0]).voice
        && await page.isVisible('.car-you .car-text:has-text("What is running right now?")'),
        'palette: "Ask Chief" opens the Chief and sends the text as a typed (not spoken) message; the reply shows there');
  check(await page.isVisible('#chief-orb') && await page.isVisible('#chief-input') && await page.isVisible('#car-talk') && await page.isVisible('#car-handsfree'),
        'Chief workspace: orb, text input, tap to talk and hands-free together');
  await page.click('#car-exit');
  // Work: tasks are listed by a concise title (never the conversation prompt), with status filters.
  await go(page, 'tasks'); await page.waitForSelector('#task-rows tr[data-id]');
  const titles = await page.$$eval('#task-rows tr[data-id] .req-text', x => x.map(e => e.textContent));
  check(titles.length > 0 && !titles.some(t => /This is a follow-up|LATEST USER MESSAGE|PREVIOUS CONVERSATION|TASK INSTRUCTIONS/.test(t)) && titles.every(t => t.length <= 80),
        'task rows show concise display titles, not raw prompts (' + titles.slice(0, 3).join(' | ') + ')');
  await page.click('.work-filters button[data-status="failed,incomplete"]');
  await page.waitForFunction(() => document.getElementById('task-status-filter').value === 'failed,incomplete');
  await page.waitForTimeout(400);
  const failedSt = await page.$$eval('#task-rows tr[data-id] .status-cell', x => x.map(e => e.textContent.trim()));
  check(failedSt.every(t => /Failed|Incomplete/i.test(t)) && await page.$eval('.work-filters button[data-status="failed,incomplete"]', b => b.getAttribute('aria-pressed') === 'true'),
        'Work filter "Failed" shows failed and incomplete runs only (' + failedSt.length + ')');
  await page.click('.work-filters button[data-status=""]');
  // Project detail in tabs: Overview, Plan, Log, Files.
  await go(page, 'projects'); await page.waitForSelector('#project-list .thread-item');
  await page.click('#project-list .thread-item'); await page.waitForSelector('#project-main .ptabs');
  const ptabs = await page.$$eval('#project-main .ptabs button', b => b.map(x => x.dataset.ptab));
  await page.click('.ptabs button[data-ptab="log"]');
  const logShown = await page.isVisible('.project-notes') && await page.isHidden('.ptask');
  await page.click('.ptabs button[data-ptab="plan"]');
  check(JSON.stringify(ptabs) === JSON.stringify(['overview', 'plan', 'log', 'files']) && logShown && await page.isVisible('.ptask') && await page.isHidden('.project-notes'),
        'project detail: Overview, Plan, Log and Files tabs show one part at a time ' + JSON.stringify(ptabs));
  // No sideways scroll on Home, Chief and Work at laptop and full-HD widths.
  const wideOver = [];
  for (const w of [1280, 1920]) {
    await page.setViewportSize({ width: w, height: w === 1920 ? 1080 : 800 });
    for (const v of ['overview', 'chief', 'tasks', 'projects']) {
      await go(page, v); await page.waitForTimeout(250);
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) wideOver.push(w + ':' + v);
      if (v === 'chief') await page.click('#car-exit');
    }
  }
  // HLA_UI_V4: and the Infrastructure, Websites, Knowledge and Settings pages at laptop and full-HD widths.
  const wide4 = [];
  for (const w of [1366, 1920]) {
    await page.setViewportSize({ width: w, height: w === 1920 ? 1080 : 768 });
    for (const v of ['infrastructure', 'websites', 'knowledge', 'agents', 'models', 'mcp', 'connections', 'policy', 'costs', 'audit']) {
      await go(page, v); await page.waitForTimeout(250);
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) wide4.push(w + ':' + v);
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  check(!wideOver.length, 'no sideways scroll on Home, Chief and Work at 1280 and 1920 px ' + JSON.stringify(wideOver));
  check(!wide4.length, 'no sideways scroll on Infrastructure, Websites, Knowledge and Settings at 1366 and 1920 px ' + JSON.stringify(wide4));
  await go(page, 'audit');
  await page.waitForSelector('.audit-row');
  const auditN = await page.$$eval('.audit-row', r => r.length);
  check(auditN >= 12 && await page.isVisible('.audit-row:has-text("Free-shell command") .audit-cmd:has-text("--password=[redacted]")')
        && !(await page.textContent('#audit-rows')).includes('hunter2'), 'audit rows listed, credentials masked (' + auditN + ')');
  check(await page.isVisible('.audit-row:has-text("Approved command") .audit-approver:has-text("jordan")'), 'audit shows who approved a command');
  check((await page.$$eval('#audit-server option', o => o.map(x => x.value))).includes('jellyfin') && (await page.$$eval('#audit-agent option', o => o.map(x => x.textContent))).includes('Infra Agent'),
        'audit filters list servers and agents');
  await page.selectOption('#audit-server', 'jellyfin');
  await page.waitForFunction(() => { const r = [...document.querySelectorAll('.audit-row .audit-server')]; return r.length && r.every(x => x.textContent === 'jellyfin') });
  check(true, 'audit server filter');
  await page.click('#audit-clear');
  await page.fill('#audit-q', 'nginx');
  await page.waitForFunction(() => { const r = [...document.querySelectorAll('.audit-row')]; return r.length === 2 && r.every(x => x.textContent.includes('nginx')) });
  check(true, 'audit text search');
  await page.click('#audit-clear');
  await page.waitForFunction(n => document.querySelectorAll('.audit-row').length === n, auditN);
  check(auditN === 50 && await page.isVisible('#audit-more'), 'audit shows 50 rows, then Load more');
  await page.click('#audit-more');
  await page.waitForFunction(() => document.querySelectorAll('.audit-row').length > 50);
  const ids = await page.$$eval('.audit-row', r => r.map(x => Number(x.dataset.id)));
  check(new Set(ids).size === ids.length && ids.every((v, i) => !i || v < ids[i - 1]) && await page.isHidden('#audit-more'),
        'Load more appends older rows, newest first, until the end (' + ids.length + ')');
  const longRow = page.locator('.audit-row:has(.audit-expand)').first();
  await longRow.locator('.audit-expand').click();
  check(await longRow.locator('.audit-cmd.open').count() === 1 && await page.isHidden('#audit-detail'), 'a long command expands in place without opening details');
  await page.click('.audit-row:has-text("Approved command"):has-text("restart nginx")');
  await page.waitForSelector('#audit-detail:not([hidden])');
  check(await page.isVisible('#audit-detail-facts dd:has-text("jordan")') && (await page.textContent('#audit-detail-cmd')) === 'sudo systemctl restart nginx', 'audit details show approver and full command');
  await page.keyboard.press('Escape');
  await page.waitForSelector('#audit-detail', { state: 'hidden' });
  await page.click('.audit-row:has-text("Diagnostic"):has-text("fs-01")');
  await page.waitForSelector('#audit-detail-links button:has-text("Open conversation")');
  await page.click('#audit-detail-links button:has-text("Open conversation")');
  await page.waitForFunction(() => !document.getElementById('threads').hidden && document.querySelector('#thread-messages .msg'));
  check(await page.isHidden('#audit-detail') && (await page.textContent('#thread-heading')) !== 'Select a conversation', 'an audit row opens the conversation it came from');

  // Queue: readable statuses instead of raw values.
  await go(page, 'queue');
  await page.waitForSelector('#queue-jobs .queue-head');
  const qs = await page.$$eval('#queue-jobs .queue-head .pill', p => p.map(x => x.textContent));
  check(['Waiting', 'Completed', 'Failed', 'Cancelled'].every(l => qs.includes(l)) && !(await page.textContent('#queue-jobs')).match(/\bqueued ·|cancelled ·/)
        && await page.isVisible('#queue-jobs .queue-head button:has-text("Open task")'), 'queue jobs show readable statuses ' + JSON.stringify(qs));
  await go(page, 'threads');

  // Deployments on the Policy page: history, rollback offered on the last good deploy, deploy runs and lands in history.
  await go(page, 'policy');
  await page.waitForSelector('#deploy-history tr');
  check(await page.isVisible('.deploy-app:has-text("public-website:/opt/site")'), 'registered app listed');
  check(await page.isVisible('#deploy-history tr:has-text("feature/new-header") .pill:has-text("Rolled back")'), 'rolled-back deploy in history');
  check(await page.isVisible('#deploy-history button:has-text("Roll back to a1b2c3d")'), 'rollback offered on the last good deploy');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/deployments.png', fullPage: true });
  // GitHub: the service row explains its levels; repositories are allowed one by one, merges per repository.
  check(await page.isVisible('#policy-servers tr:has-text("github") .req-sub:has-text("open PRs and comment")'), 'github service listed with its levels');
  // HLA_UI_V5: the GitHub allow-list lives in one place, Connections › GitHub; Security links there.
  check(await page.isVisible('#github-policy-line a:has-text("Connections › GitHub")') && !(await page.$('#policy #github-panel')), 'Security links to the GitHub allow-list instead of repeating it');
  await page.click('#github-policy-line a');
  await page.waitForSelector('#connections:not([hidden]) #gh-panel #github-panel');
  check(await page.isVisible('#github-panel:has-text("No repositories allowed yet")'), 'no GitHub repositories allowed by default');
  await page.fill('#github-repo', 'example/site');
  await page.click('#github-panel button:has-text("Allow repository")');
  await page.waitForSelector('#github-panel .deploy-app:has-text("example/site")');
  await page.click('#github-panel .deploy-app button:has-text("Allow merges")');
  await confirmDialog(page);
  await page.waitForSelector('#github-panel .deploy-app:has-text("merges allowed from level 4")');
  check(await page.isVisible('#github-panel button:has-text("Disallow merges")'), 'a repository is allowed, then merges are allowed for it');
  await page.click('#github-panel .deploy-app button:has-text("Remove")');
  await confirmDialog(page, 'Remove repository');
  await page.waitForSelector('#github-panel:has-text("No repositories allowed yet")');
  check(true, 'a repository can be removed again');
  await go(page, 'policy');
  await page.click('.deploy-app button:has-text("Deploy…")');
  // A single-line prompt: the ref is preselected; Enter in the field confirms.
  check(await (await openDialog(page)).$eval('input[name=value]', i => i.value === 'main' && document.activeElement === i), 'deploy asks for the ref in a field that has focus, main by default');
  await page.fill(DLG + ' input[name=value]', 'main');
  await page.keyboard.press('Enter');
  await page.waitForSelector('#deploy-history tr .pill:has-text("Running")', { timeout: 5000 });
  check(true, 'deploy shows as running');
  await page.waitForFunction(() => document.querySelectorAll('#deploy-history .pill').length === 3 && !document.querySelector('#deploy-history .st-running'), null, { timeout: 15000 });
  check(await page.isVisible('#deploy-history tr:first-child .pill:has-text("Deployed")') && await page.isVisible('#deploy-history tr:first-child button:has-text("Roll back to c3d4e5f")'),
        'deploy finished; rollback now targets the commit it replaced');
  // Connections (HLA_CONNECTIONS_V1): write-only credentials, the GitHub card and repository picker, HTTP tools.
  await go(page, 'policy');
  check(await page.isVisible('#github-policy-line a') && await page.isVisible('#policy-servers tr:has-text("http_tools") .req-sub:has-text("Connections page")'),
        'Policy page: GitHub links to Connections; http_tools is a service with its levels');
  await page.click('#github-policy-line a');
  await page.waitForSelector('#cred-list:has-text("No credentials stored yet")');
  await page.waitForSelector('#gh-status .pill:has-text("No token")');
  check(!(await page.isHidden('#connections')) && await page.isVisible('#gh-repos:has-text("Connect a token")'), 'Connections: no credentials yet, GitHub without a token');
  await page.click('#cred-add summary');
  const credSecret = 'wk_live_' + 'Q4z'.repeat(8);
  await page.fill('#cred-name', 'WEATHER_API_KEY'); await page.selectOption('#cred-kind', 'api_key');
  await page.fill('#cred-value', credSecret); await page.fill('#cred-desc', 'Weather API');
  check(await page.getAttribute('#cred-value', 'type') === 'password', 'the credential value is typed into a password field');
  await page.click('#cred-form button[type=submit]');
  await page.waitForSelector('#cred-list .conn-item:has-text("WEATHER_API_KEY")');
  check(!(await page.content()).includes(credSecret) && await page.inputValue('#cred-value') === '' && await page.isVisible('#cred-list .conn-item:has-text("API key"):has-text("fingerprint")'),
        'credential listed with kind and fingerprint, never its value; the field is cleared');
  const credApi = await page.evaluate(async () => (await fetch('/ui/api/credentials')).text());
  check(!credApi.includes(credSecret) && credApi.includes('"fingerprint"') && !credApi.includes('"value"'), 'the credentials API returns metadata only');
  const fp1 = await page.textContent('#cred-list .conn-item:has-text("WEATHER_API_KEY") .conn-meta');
  await page.click('#cred-list .conn-item:has-text("WEATHER_API_KEY") button:has-text("Replace")');
  check(await page.inputValue('#cred-name') === 'WEATHER_API_KEY' && await page.$eval('#cred-name', e => e.readOnly) && await page.inputValue('#cred-value') === '',
        'Replace keeps the name and starts with an empty value');
  await page.fill('#cred-value', credSecret + 'X'); await page.click('#cred-form button[type=submit]');
  await page.waitForFunction(fp => { const m = document.querySelector('#cred-list .conn-item .conn-meta'); return m && m.textContent !== fp }, fp1);
  check(true, 'replacing a value changes its fingerprint');
  // GitHub: the token goes into the credential store; the card shows source and login; repositories are picked from a list.
  await page.click('#gh-token summary');
  await page.fill('#gh-token-value', 'github_pat_' + 'A1b'.repeat(10));
  await page.click('#gh-token-form button[type=submit]');
  await page.waitForSelector('#gh-status:has-text("Signed in as")');
  check(await page.isVisible('#gh-status:has-text("hla-bot")') && await page.isVisible('#gh-status:has-text("credential store")')
        && await page.isVisible('#cred-list .conn-item:has-text("GITHUB_TOKEN"):has-text("used by GitHub")'), 'GitHub card: connected, login and token source; GITHUB_TOKEN listed');
  await page.waitForSelector('#gh-repos .gh-repo:has-text("example/site")');
  check(await page.$$eval('#gh-repos .gh-repo', r => r.length) === 9 && await page.isVisible('#gh-repo-filter'), 'the repository picker lists what the token reaches, with a filter');
  await page.check('#gh-repos .gh-repo:has-text("example/site") input[data-repo]');
  await page.waitForSelector('#gh-repos .gh-repo.on:has-text("example/site") .gh-merge');
  check(await page.evaluate(() => !!policyData.policy.github.repos['example/site']), 'checking a repository adds it to the allowlist');
  await page.check('#gh-repos .gh-repo.on:has-text("example/site") .gh-merge input');
  await confirmDialog(page, 'Allow merges');
  await page.waitForFunction(() => policyData.policy.github.repos['example/site']?.merge === true);
  check(await page.$eval('#github-panel', p => p.textContent.includes('example/site') && p.textContent.includes('merges allowed')), 'merges allowed per repository; the Policy page shows the same allowlist');
  await page.fill('#gh-repo-filter', 'bakery');
  check(await page.$$eval('#gh-repos .gh-repo', r => r.length) === 1, 'the filter narrows the list');
  await page.fill('#gh-repo-filter', '');
  await page.selectOption('#gh-level', '4');
  await page.waitForFunction(() => policyData.policy.services.github.level === 4);
  check(true, 'the github service level is set from the Connections page');
  await page.click('#gh-test');
  await page.waitForSelector('#gh-status:has-text("Signed in as")');
  check(true, 'Test connection checks the token again');
  // HTTP tools: create with a path parameter, an enum query parameter and the credential; test it.
  await page.click('#ht-new');
  await page.fill('#ht-name', 'weather_now');
  await page.fill('#ht-desc', 'Current weather for a city. Use for questions about the weather.');
  await page.fill('#ht-base', 'http://api.weather.test/v1');
  await page.fill('#ht-path', '/cities/{city}/now');
  await page.click('#ht-param-add'); await page.click('#ht-param-add');
  const prow = n => `#ht-params .ht-param:nth-child(${n})`;
  await page.fill(prow(1) + ' [data-f=name]', 'city'); await page.selectOption(prow(1) + ' [data-f=in]', 'path'); await page.fill(prow(1) + ' [data-f=description]', 'city name');
  await page.fill(prow(2) + ' [data-f=name]', 'units'); await page.fill(prow(2) + ' [data-f=enum]', 'metric, imperial');
  await page.selectOption('#ht-cred', 'WEATHER_API_KEY');
  await page.click('#ht-form button[type=submit]');
  await page.waitForSelector('#notice:has-text("https")');
  check(await page.isVisible('#ht-form'), 'an http:// base URL is refused (https only) and the form stays open');
  await page.fill('#ht-base', 'https://api.weather.test/v1');
  await page.click('#ht-form button[type=submit]');
  await page.waitForSelector('#ht-list .ht-item:has-text("weather_now")');
  check(await page.isHidden('#ht-form') && await page.isVisible('#ht-list .ht-item code:has-text("https://api.weather.test/v1/cities/{city}/now?units")')
        && await page.isVisible('#ht-list .ht-item:has-text("credential WEATHER_API_KEY")') && await page.isVisible('#ht-list .ht-item .pill:has-text("level 1")'),
        'HTTP tool saved: URL with query names, credential by name, level 1 for GET');
  check(await page.isVisible('#cred-list .conn-item:has-text("WEATHER_API_KEY"):has-text("used by tool weather_now")'), 'the credential shows which tool uses it');
  await page.click('#ht-list .ht-item:has-text("weather_now") button:has-text("Test")');
  await page.fill('.ht-test [data-param=city]', 'Ghent'); await page.selectOption('.ht-test [data-param=units]', 'metric');
  await page.click('.ht-test button[type=submit]');
  await page.waitForSelector('.ht-result .pill:has-text("HTTP 200")');
  const preview = await page.textContent('.ht-preview');
  check(preview.includes('"authorized": true') && preview.includes('/v1/cities/Ghent/now') && preview.includes('"units"') && !(await page.content()).includes(credSecret),
        'Test: status and response; the gateway added the credential (never shown)');
  await page.click('#ht-list .ht-item:has-text("weather_now") button:has-text("Edit")');
  check(await page.inputValue('#ht-name') === 'weather_now' && await page.$eval('#ht-name', e => e.readOnly) && await page.$$eval('#ht-params .ht-param', r => r.length) === 2
        && await page.inputValue('#ht-cred') === 'WEATHER_API_KEY', 'Edit opens the saved definition');
  await page.click('#ht-cancel');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/connections.png', fullPage: true });
  await go(page, 'threads');

  // Schedules: add one to a conversation, run it now, the scheduled prompt and its reply appear in the thread.
  await page.click('.thread-item:has-text("Deploy monitoring")');
  await page.waitForSelector('#thread-heading:has-text("Deploy monitoring")');
  await page.click('#schedule-toggle');
  await page.waitForSelector('#schedule-list .muted:has-text("No schedules")');
  await page.fill('#schedule-prompt', 'Give me a short status overview');
  await page.selectOption('#schedule-interval', '1440');
  await page.click('#schedule-form button[type=submit]');
  await page.waitForSelector('.schedule-row:has-text("every day")');
  check(await page.isVisible('.thread-item:has-text("Deploy monitoring") .badge-sched'), 'schedule marked in the conversation list');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/schedules.png' });
  const beforeRun = await page.$$eval('#thread-messages .msg:not(.live)', m => m.length);
  await page.click('.schedule-row button:has-text("Run now")');
  await page.waitForFunction(n => document.querySelectorAll('#thread-messages .msg:not(.live)').length >= n + 2 && !document.querySelector('.msg.live'), beforeRun, { timeout: 20000 });
  check(await page.isVisible('#thread-messages .msg-user .msg-head strong:text-is("Scheduled")'), 'scheduled prompt shown as Scheduled, then answered');
  await page.click('.schedule-row button:has-text("Delete")');
  await confirmDialog(page, 'Delete schedule');
  await page.waitForSelector('#schedule-list .muted:has-text("No schedules")');
  check(true, 'schedule deleted');

  // Projects: review and edit the plan, approve, tasks run with handoffs, approve the gated deploy, completed.
  await go(page, 'projects');
  await page.waitForSelector('.project-head:has-text("Bakery website") .pill:has-text("Plan ready")');
  check((await page.$$('.ptask')).length === 3 && await page.isVisible('.ptask:nth-child(3) .badge-gate'), 'plan shows 3 tasks, deploy gated');
  // HLA_UI_V4: compact task cards; a task's fields open in a drawer (Edit).
  check(await page.isHidden('.ptask:nth-child(1) input[aria-label="Task title"]') && await page.isVisible('.ptask:nth-child(1) .ptask-edit-btn'),
        'plan: compact task cards, the fields open on Edit');
  await page.click('.ptask:nth-child(1) .ptask-edit-btn');
  await page.fill('#ptask-drawer input[aria-label="Task title"]', 'Research bakery.example (products and tone)');
  await page.click('#ptask-drawer button:has-text("Save task")');
  await page.waitForSelector('#notice:has-text("Task saved")');
  check(await page.isVisible('.ptask:nth-child(1) .ptask-title:has-text("products and tone")'), 'plan: the card shows the saved title');
  await page.keyboard.press('Escape');
  await page.waitForSelector('#ptask-drawer', { state: 'hidden' });
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/project-plan.png', fullPage: true });
  await page.click('.ptask:nth-child(2) .ptask-edit-btn');
  check(await page.isVisible('#ptask-drawer label:has-text("Done when") input'), 'plan fields have visible labels');
  await page.fill('#ptask-drawer textarea[aria-label="Task description"]', 'Static HTML/CSS, served by nginx on port 8080 (edited, not saved).');
  await page.keyboard.press('Escape');
  await page.waitForSelector('#ptask-drawer', { state: 'hidden' });
  await page.click('.project-actions button:has-text("Approve plan")');
  const startMsg = await (await openDialog(page)).$eval('.ui-dialog-message', e => e.textContent);
  check(/^Start this project\?/.test(startMsg), 'Approve plan asks in an in-page dialog first');
  await confirmDialog(page, 'Approve & start');
  await page.waitForSelector('.project-head .pill:has-text("Running")');
  check(await page.isVisible('.ptask:nth-child(1) .ptask-title:has-text("products and tone")'), 'edited title kept after approval');
  check(await page.isVisible('.ptask:nth-child(2) .ptask-desc:has-text("edited, not saved")'), 'unsaved plan edits are saved when the plan is approved');
  // Polling re-renders a running project: a half-typed note and an opened section stay.
  await page.click('.ptabs button[data-ptab="overview"]');
  await page.click('.project-rules summary');
  await page.click('.ptabs button[data-ptab="log"]');
  await page.fill('.note-form input', 'half-typed note');
  await page.click('.project-head h2');
  await page.waitForTimeout(3600);
  check(await page.inputValue('.note-form input') === 'half-typed note' && await page.$eval('.project-rules', d => d.open)
        && await page.$eval('.ptabs button[data-ptab="log"]', b => b.getAttribute('aria-selected') === 'true'), 'typed note, open brief and the open tab survive polling');
  await page.fill('.note-form input', '');
  await page.click('.project-head h2');
  await page.click('.ptabs button[data-ptab="plan"]');
  await page.waitForSelector('.ptask:nth-child(3) .pill:has-text("Needs approval")', { timeout: 60000 });
  await go(page, 'overview');
  await page.waitForFunction(() => { const m = document.querySelector('.inbox-more'); if (m && /Show all/.test(m.textContent)) m.click();
    return [...document.querySelectorAll('.inbox-gate')].some(x => /Deploy to staging/.test(x.textContent) && x.offsetParent); }, null, { timeout: 20000 });
  check(await page.isVisible('.inbox-gate button:has-text("Approve start")'), 'gated task waits in the inbox with an Approve button');
  await page.click('.inbox-gate button:has-text("Open project")');
  await page.waitForSelector('.ptask.flash:has-text("Deploy to staging")');
  check(true, 'inbox opens the project at the gated task');
  check(await page.isVisible('.ptask:nth-child(1) .pill:has-text("Done")') && await page.isVisible('.ptask:nth-child(2) .pill:has-text("Done")'),
        'tasks ran in order and finished');
  await page.click('.ptask:nth-child(2) summary:has-text("Handoff")');
  check(await page.isVisible('.ptask:nth-child(2) .ptask-result:has-text("port 8080")'), 'handoff visible on the task');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/project-gate.png', fullPage: true });
  await page.click('.ptask:nth-child(3) button:has-text("Approve start")');
  await confirmDialog(page, 'Approve start');
  await page.waitForSelector('.project-head .pill:has-text("Completed")', { timeout: 60000 });
  await page.click('.ptabs button[data-ptab="log"]');
  check(await page.isVisible('.project-log li:has-text("Project completed")'), 'project completed and logged');
  await page.click('.ptabs button[data-ptab="plan"]');
  // Send back…: a roomy textarea for a long note; the note reaches the API unchanged (intercepted, so the project stays completed).
  const longNote = 'Please rework the contact section.\n' + Array.from({ length: 14 }, (_, i) => `- F-${i + 1}: fix finding number ${i + 1} from the review, keep the tone.`).join('\n');
  let redoBody = null;
  await page.route('**/ui/api/projects/*/tasks/*/redo', r => { redoBody = r.request().postDataJSON(); r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }); });
  await page.click('.ptask:nth-child(2) button:has-text("Send back…")');
  const sendBack = await openDialog(page);
  check(await sendBack.$eval('textarea[name=value]', t => t.rows >= 6 && t.getBoundingClientRect().height >= 140 && document.activeElement === t),
        'Send back asks for the note in a large textarea that has focus');
  await page.fill(DLG + ' textarea[name=value]', longNote);
  await page.keyboard.press('Enter');  // a newline in a textarea, not a submit
  check(await page.isVisible(DLG) && (await page.inputValue(DLG + ' textarea[name=value]')).endsWith('\n'), 'Enter in a textarea adds a line instead of submitting');
  await page.fill(DLG + ' textarea[name=value]', longNote);
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/dialog-send-back.png' });
  await page.keyboard.press('Control+Enter');
  await sendBack.waitForElementState('hidden');
  await page.waitForSelector('#notice:has-text("Sent back for rework")');
  check(redoBody && redoBody.note === longNote, 'Send back: the long note is sent as typed (' + (redoBody ? redoBody.note.length : 0) + ' chars)');
  await page.unroute('**/ui/api/projects/*/tasks/*/redo');
  await page.click('.ptask:nth-child(2) button:has-text("Open conversation")');
  await page.waitForSelector('#thread-heading:has-text("Bakery website: Build the static site")');
  check(await page.isVisible('#thread-messages .msg:has-text("TASK STATUS: done")') && await page.isVisible('#thread-project-back'), 'task conversation opens, with a way back');
  await page.click('#thread-project-back');
  await page.waitForSelector('.project-head:has-text("Bakery website")');
  // Rounds: the completed project takes new work; the earlier round stays as finished history.
  await page.click('.project-next summary');
  await page.fill('.project-next textarea', 'Add a contact page with the opening hours.');
  await page.click('.project-next button:has-text("Plan this round")');
  await page.waitForSelector('.project-head .pill:has-text("Plan ready")', { timeout: 15000 });
  const rounds = await page.$$eval('.project-round > summary', s => s.map(x => x.textContent));
  check(rounds.length === 2 && rounds[0].startsWith('Round 2 · Add a contact page') && rounds[1].startsWith('Round 1') && rounds[1].includes('3 of 3 done'),
        'round 2 planned on the completed project, round 1 kept ' + JSON.stringify(rounds));
  check((await page.$$('.project-round[open] .ptask input[aria-label="Task title"]')).length === 3 &&
        (await page.$$('.project-round:not([open]) .ptask input')).length === 0, 'only the new round is editable');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/project-round.png', fullPage: true });
  // Project knowledge: finished tasks left their handoff; a note added here reaches the Knowledge page with its project.
  // HLA_UI_V3: knowledge and files are on the project's Files tab (opened there).
  await page.click('.ptabs button[data-ptab="files"]');
  if (!(await page.$eval('.project-kb', d => d.open))) await page.click('.project-kb > summary');
  await page.waitForSelector('.project-kb .origin-auto');
  check((await page.$$('.project-kb .origin-auto')).length >= 2, 'project knowledge holds the handoffs of finished tasks');
  await page.fill('.project-kb-form input', 'Staging URL');
  await page.fill('.project-kb-form textarea', 'Staging runs at https://staging.example.test behind staging-auth.');
  await page.click('.project-kb-form button[type=submit]');
  await page.waitForSelector('.project-kb .kb-entry:has-text("Staging URL") .origin-human');
  check(await page.isVisible('.project-kb[open]'), 'project knowledge stays open after adding a note');
  await page.click('.project-kb .kb-entry:has-text("Staging URL") button:has-text("Edit")');
  await page.fill('.project-kb .kb-entry textarea', 'Staging runs at https://staging.example.test (staging-auth).');
  await page.click('.project-kb .kb-entry button:has-text("Save")');
  await page.waitForSelector('.project-kb .kb-entry:has-text("(staging-auth)")');
  check(true, 'project note edited in place');
  await go(page, 'knowledge');
  await page.click('#kb-tabs button[data-tab="notes"]');
  await page.waitForSelector('#kb-project option:has-text("Bakery website")', { state: 'attached' });
  await page.selectOption('#kb-project', { label: 'Bakery website' });
  await page.waitForSelector('#kb-list .kb-entry:has-text("Staging URL") .link-pill:has-text("Bakery website")');
  check(await page.$eval('#kb-note-project', s => s.options[s.selectedIndex].text) === 'Bakery website', 'Knowledge page filters by project; new notes default to it');
  // HLA_UI_V4: a searchable library; a row shows title, description, where it belongs and when it changed, and
  // opens the full note in a drawer with Edit; New note uses the same drawer.
  const kbRow = '#kb-list .kb-entry:has-text("Staging URL")';
  check(await page.isVisible(kbRow + ' .row-sub:has-text("staging.example.test")') && await page.isVisible(kbRow + ' .row-side:has-text("Project")'),
        'knowledge library: a compact row with a one-line description and its category');
  await page.click(kbRow + ' .row-title strong');
  await page.waitForSelector('#kb-drawer:not([hidden]) .kb-note-body:has-text("(staging-auth)")');
  check(await page.isVisible('#kb-drawer .facts:has-text("Bakery website")'), 'knowledge: a row opens the full note in a drawer');
  await page.click('#kb-drawer button:has-text("Edit")');
  await page.waitForSelector('#kb-drawer #kb-form');
  check(await page.inputValue('#kb-title') === 'Staging URL' && await page.inputValue('#kb-note-project') !== '', 'knowledge: Edit opens the note in the form');
  await page.fill('#kb-body', 'Staging runs at https://staging.example.test (staging-auth). Deploys from the staging branch.');
  await page.click('#kb-submit');
  await page.waitForSelector('#kb-drawer .kb-note-body:has-text("Deploys from the staging branch")');
  check(/Note updated/.test(await page.textContent('#notice')), 'knowledge: saving shows the updated note');
  await page.keyboard.press('Escape');
  await page.click('#kb-new');
  await page.waitForSelector('#kb-drawer:not([hidden]) #kb-form');
  await page.fill('#kb-title', 'Backup window');
  await page.fill('#kb-body', 'Nightly backups run at 02:00; avoid restarts between 02:00 and 03:00.');
  await page.click('#kb-submit');
  await page.waitForSelector('#kb-list .kb-entry:has-text("Backup window")');
  await page.keyboard.press('Escape');
  await page.fill('#kb-search', 'backups');
  await page.waitForFunction(() => { const r = [...document.querySelectorAll('#kb-list .kb-entry')]; return r.length === 1 && /Backup window/.test(r[0].textContent); });
  check(true, 'knowledge: New note saves into the library; search narrows it');
  await page.fill('#kb-search', 'zzz-nothing-here');
  await page.waitForSelector('#kb-list .empty-state:has-text("No notes match")');
  await page.fill('#kb-search', '');
  await page.waitForSelector('#kb-list .kb-entry:has-text("Staging URL") .link-pill');
  await page.click('#kb-list .kb-entry:has-text("Staging URL") .link-pill');
  await page.waitForSelector('.project-head:has-text("Bakery website")');
  check(await page.isHidden('#kb-drawer'), 'a project note links back to its project (the link does not open the note)');
  await page.click('#project-new-toggle');
  check(await page.inputValue('#project-merge') === 'direct' && (await page.getAttribute('#project-merge', 'title')).includes('branch protection'),
        'new projects default to direct merges; pull requests explain their setup');
  await page.selectOption('#project-merge', 'pr');
  check(await page.isVisible('.merge-policy-hint:has-text("branch protection")'), 'choosing pull requests shows what they need');
  await page.selectOption('#project-merge', 'direct');
  await page.fill('#project-title', 'Monitoring dashboard');
  await page.fill('#project-goal', 'Set up a small monitoring dashboard for all servers in the sandbox.');
  await page.click('#project-form button[type=submit]');
  await page.waitForSelector('.project-head:has-text("Monitoring dashboard")');
  await page.waitForSelector('.project-head .pill:has-text("Plan ready")', { timeout: 15000 });
  check(true, 'new project planned');
  // Limits…: one form for budget, runs and tasks at a time (was two prompts in a row); inline validation; any cents allowed.
  await page.click('.project-facts button:has-text("Limits…")');
  const limits = await openDialog(page);
  check(JSON.stringify(await limits.$$eval('.ui-field-label', l => l.map(x => x.textContent))) === JSON.stringify(['Budget in USD', 'Maximum runs', 'Tasks at a time'])
        && await limits.$eval('input[name=budget_usd]', i => i.step === 'any' && document.activeElement === i), 'Limits is one form: budget, runs, tasks at a time');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/dialog-limits.png' });
  await page.keyboard.press('Escape');
  await limits.waitForElementState('hidden');
  await page.waitForFunction(() => document.activeElement && document.activeElement.textContent === 'Limits…', null, { timeout: 3000 }).catch(() => {});
  check(await page.evaluate(() => document.activeElement.textContent) === 'Limits…', 'focus returns to the button that opened the dialog');
  await page.click('.project-facts button:has-text("Limits…")');
  await openDialog(page);
  await page.fill(DLG + ' input[name=budget_usd]', '0');
  await page.fill(DLG + ' input[name=max_runs]', '2.5');
  await page.click(DLG + ' .ui-dialog-ok');
  check(await page.isVisible(DLG) && /0.05 or more/.test(await page.textContent(DLG + ' #' + await page.$eval(DLG + ' input[name=budget_usd]', i => i.id) + '-error'))
        && await page.$eval(DLG + ' input[name=max_runs]', i => i.getAttribute('aria-invalid') === 'true'), 'Limits: invalid values are explained inline and nothing is saved');
  await fillDialog(page, { budget_usd: '7.35', max_runs: '55', parallel: '3' }, 'Save limits');
  await page.waitForSelector('.project-facts:has-text("of 55 ·")');
  check(await page.isVisible('.project-facts:has-text("$7.35")') && await page.isVisible('.project-facts:has-text("3 at a time")'), 'Limits form saves budget, runs and tasks at a time');
  check(await page.isVisible('.project-facts select[aria-label="How code changes land"]') && await page.inputValue('.project-facts select[aria-label="How code changes land"]') === 'direct',
        'the merge policy is shown and editable before the project runs');
  await page.selectOption('.project-facts select[aria-label="How code changes land"]', 'pr');
  await page.click('.ptabs button[data-ptab="log"]');
  await page.waitForSelector('.project-log li:has-text("merge_policy=pr")');
  check(true, 'merge policy changed from the project page');
  await page.click('#project-list .thread-item:has-text("Login form")');
  await page.waitForSelector('.project-head:has-text("Login form") ~ .ptabs'); await page.click('.ptabs button[data-ptab="plan"]');
  await page.waitForSelector('.ptask .badge:has-text("PR #12 · open")');
  check(await page.getAttribute('.ptask a.badge:has-text("PR #12")', 'href') === 'https://github.com/example/site/pull/12'
        && await page.isVisible('.project-facts:has-text("pull requests")'), 'task card shows its PR with a link; a finished project shows its merge policy');
  await go(page, 'threads');

  // Composer (General worker, streamed reply): Enter sends once, live bubble, Shift+Enter adds a newline.
  await page.click('.thread-item:has-text("Backup")');
  await page.waitForSelector('#thread-heading:has-text("Backup")');
  await page.fill('#thread-input', 'line one');
  await page.press('#thread-input', 'Shift+Enter');
  check((await page.inputValue('#thread-input')).includes('\n'), 'Shift+Enter inserts newline');
  check(await page.inputValue('#thread-output-tokens') === '' && (await page.getAttribute('#thread-output-tokens', 'placeholder')).startsWith('Unlimited'),
        'Max tokens empty by default = unlimited');
  await page.fill('#thread-input', 'What is the current uptime of the docker host?');
  const n0 = await page.$$eval('#thread-messages .msg:not(.live)', m => m.length);
  await page.press('#thread-input', 'Enter');
  await page.press('#thread-input', 'Enter');   // second Enter must not double-send
  await page.waitForSelector('.msg.live');
  check(await page.isVisible('#thread-stop') && (await page.textContent('#thread-send')) === 'Queue', 'Stop shown and Send becomes Queue while running');
  await page.waitForSelector('.msg.live .msg-body', { timeout: 8000 });
  const partial = await page.$eval('.msg.live .msg-body', e => e.textContent.length);
  check(partial > 0, 'reply streams into the live bubble');
  await page.waitForSelector('.msg.live', { state: 'detached', timeout: 20000 });
  const msgs = await page.$$eval('#thread-messages .msg', m => m.map(x => x.className));
  check(msgs.length === n0 + 2, `exactly one user + one agent message added (${msgs.length - n0})`);
  check(await page.inputValue('#thread-input') === '', 'composer cleared');
  check((await page.textContent('#thread-send')) === 'Send' && await page.isHidden('#thread-stop'), 'composer back to idle');

  // Queue: a message sent while the agent works waits in a dashed bubble, then runs by itself.
  await page.fill('#thread-input', 'First of two'); await page.press('#thread-input', 'Enter');
  await page.waitForSelector('.msg.live');
  await page.fill('#thread-input', 'Second of two'); await page.press('#thread-input', 'Enter');
  await page.waitForSelector('.msg.queued:has-text("Second of two")');
  check(await page.isDisabled('#thread-send'), 'only one follow-up can be queued');
  await page.waitForFunction(() => !document.querySelector('.msg.queued') && !document.querySelector('.msg.live')
    && [...document.querySelectorAll('#thread-messages .msg-user')].pop()?.textContent.includes('Second of two'), null, { timeout: 30000 });
  const lastTwo = await page.$$eval('#thread-messages .msg', m => m.slice(-2).map(x => x.className.includes('msg-user') ? 'user' : 'agent'));
  check(lastTwo.join() === 'user,agent', 'queued follow-up answered after the first');

  // Stop: partial answer kept with a note; composer returns to idle.
  await page.fill('#thread-input', 'A long investigation'); await page.press('#thread-input', 'Enter');
  await page.waitForSelector('.msg.live .msg-body', { timeout: 8000 });
  await page.click('#thread-stop');
  await page.waitForSelector('.msg-agent:last-child:has-text("Stopped before the agent finished")', { timeout: 10000 });
  check(/Fileserver permissions/.test(await page.$eval('.msg-agent:last-child', e => e.textContent)), 'partial answer kept after stop');
  check(await page.isHidden('#thread-stop'), 'Stop hidden after stopping');

  // New conversation from the sidebar.
  await page.click('#thread-new-toggle');
  await page.fill('#thread-title', 'E2E created');
  await page.selectOption('#thread-agent', { label: 'Infra Agent' });
  await page.click('#thread-create button[type=submit]');
  await page.waitForSelector('#thread-heading:has-text("E2E created")');
  check(await page.isHidden('#thread-create'), 'new conversation form closes after create');
  check(await page.isVisible('.empty-state:has-text("No messages yet")'), 'empty conversation state');

  // Tool-using agent: steps appear live, then stay (collapsed) on the finished reply.
  await page.fill('#thread-input', 'Check the current state of fs-01'); await page.press('#thread-input', 'Enter');
  await page.waitForSelector('.msg.live .live-label');
  check(/Thinking|Running/.test(await page.textContent('.msg.live .live-label')) && (await page.title()).startsWith('● Working'),
        'live status line and tab title while working');
  await page.waitForSelector('.msg.live .steps li.step-running .step-spinner', { timeout: 10000 });
  check(/Running diagnostic · fs-01/.test(await page.textContent('.msg.live .live-label')), 'status names the running step');
  // Checked while the step runs: the reply streams for only ~2 s, so it can finish during the scroll checks below.
  check(/Diagnostic\s*fs-01 identity/.test(await page.textContent('.msg.live .steps li')), 'live tool step shows tool and target');
  await page.waitForSelector('.msg.live .md-view.streaming', { timeout: 15000 });
  check(/Writing/.test(await page.textContent('.msg.live .live-label')), 'tool agent answer streams live');
  // Scrolling up while it streams shows a jump button instead of pulling the view down.
  const fullSize = page.viewportSize(); await page.setViewportSize({ width: fullSize.width, height: 560 });  // the chat is tall now: make it scroll
  await page.$eval('#thread-messages', b => { b.scrollTop = 0; });
  await page.waitForSelector('#thread-jump:not([hidden])', { timeout: 8000 });
  await page.click('#thread-jump');
  await page.waitForFunction(() => { const b = document.getElementById('thread-messages'); return b.scrollHeight - b.scrollTop - b.clientHeight < 80; });
  check(await page.isHidden('#thread-jump'), 'jump to latest returns to the live reply');
  await page.setViewportSize(fullSize);
  await page.waitForSelector('.msg.live', { state: 'detached', timeout: 20000 });
  const summary = await page.textContent('.msg-agent:last-child .steps summary');
  check(summary === '2 tool calls' && !(await page.$eval('.msg-agent:last-child .steps', d => d.open)), 'finished reply keeps collapsed tool steps');
  check(await page.isVisible('.msg-agent:last-child .copy-btn') && !(await page.title()).startsWith('●'), 'finished reply has Copy; tab title restored');

  // HLA_ATTACHMENTS_V1: a photo and a document in the message box (thumbnail and chip), sent with the message, listed
  // under Files, previewed; the model of this agent cannot see images, and the box says so before sending.
  const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAEAAAAAwCAIAAAAuKetIAAAAYElEQVR4nO3YQQ3AIBBFwVI1SKxE5FQCIjhMSN4o+C972zG/9dzs1QNOFaAVoBWgFaAVoBWgFaAVoBWgjX9NveHI9RcoQCtAK0Ab/UaxArQCtAK0ArQCtAK0ArQCtAK0DXIyBQ58bv7TAAAAAElFTkSuQmCC', 'base64');
  await page.setInputFiles('#thread-file', [{ name: 'scratch.txt', mimeType: 'text/plain', buffer: Buffer.from('remove me') }]);
  await page.waitForSelector('#thread-attachments .att-chip:has-text("scratch.txt")');
  await page.click('#thread-attachments .att-remove');
  check(await page.isHidden('#thread-attachments'), 'attachments: a picked file can be removed before sending');
  await page.setInputFiles('#thread-file', [{ name: 'rack.png', mimeType: 'image/png', buffer: PNG },
    { name: 'runbook.md', mimeType: 'text/markdown', buffer: Buffer.from('# Runbook\nRestart jellyfin with docker compose up -d.\n') }]);
  await page.waitForSelector('#thread-attachments .att-thumb img');
  await page.waitForFunction(() => !document.querySelector('#thread-attachments .att-uploading'), null, { timeout: 8000 });
  check(await page.isVisible('#thread-attachments .att-chip:has-text("runbook.md")'), 'attachments: thumbnail and file chip in the message box');
  await page.waitForSelector('#thread-attachments .att-note:has-text("cannot see images")', { timeout: 8000 }).catch(() => {});
  check(await page.isVisible('#thread-attachments .att-note:has-text("cannot see images")'), 'attachments: warns that this model cannot see images');
  await page.fill('#thread-input', 'What do these files show?'); await page.press('#thread-input', 'Enter');
  await page.waitForSelector('.msg-user:has-text("What do these files show?") .att-thumb img', { timeout: 10000 });
  check(await page.isVisible('.msg-user:has-text("What do these files show?") .att-chip:has-text("runbook.md")') && await page.isHidden('#thread-attachments'),
        'attachments: shown on the sent message, the message box is empty again');
  check(await page.$eval('.msg-user:has-text("What do these files show?") .att-thumb img', i => i.complete && i.naturalWidth === 64), 'attachments: the photo loads from the server');
  await page.waitForSelector('.msg.live', { state: 'detached', timeout: 20000 });
  await page.click('#thread-files-toggle');
  await page.waitForSelector('#thread-files-panel .files-row');
  check(await page.$$eval('#thread-files-panel .files-row', r => r.length) === 2, 'attachments: Files lists both files of the conversation');
  await page.click('#thread-files-panel .att-preview');
  const attDlg = await openDialog(page);
  check((await attDlg.textContent()).includes('Restart jellyfin with docker compose'), 'attachments: document preview shows its text');
  await confirmDialog(page, 'Close');
  await page.click('#thread-files-toggle');
  check(errors.length === 0, 'attachments: no page errors (' + errors.join(' | ').slice(0, 200) + ')');
  await page.fill('#thread-filter', 'E2E');
  // The filter applies at once and again after the server search; wait for it to settle.
  await page.waitForFunction(() => document.querySelectorAll('#thread-list .thread-item').length === 1, null, { timeout: 5000 }).catch(() => {});
  check((await page.$$('#thread-list .thread-item')).length === 1, 'conversation filter');
  // Rename inline from the conversation menu (Enter saves).
  await page.click('#thread-menu-toggle'); await page.click('#thread-rename');
  await page.fill('.rename-input', 'E2E renamed'); await page.press('.rename-input', 'Enter');
  await page.waitForSelector('#thread-heading:has-text("E2E renamed")');
  await page.fill('#thread-filter', '');
  await page.waitForSelector('.thread-item:has-text("E2E renamed")');
  check(true, 'conversation renamed from its menu');
  // Search finds a conversation by what was said in it.
  await page.fill('#thread-filter', 'current state of fs-01');
  await page.waitForFunction(() => { const t = [...document.querySelectorAll('#thread-list .thread-item')]; return t.length === 1 && t[0].textContent.includes('E2E renamed'); }, null, { timeout: 5000 }).catch(() => {});
  check((await page.$$eval('#thread-list .thread-item', t => t.map(x => x.textContent))).join('|').includes('E2E renamed'), 'search matches message text');
  await page.press('#thread-filter', 'Escape');
  check(await page.inputValue('#thread-filter') === '', 'Escape clears the search');
  // Archive: leaves the list, listed under Archived, can come back.
  await page.click('#thread-menu-toggle'); await page.click('#thread-archive');
  await page.waitForSelector('.empty-state:has-text("Archived")');
  await page.waitForFunction(() => ![...document.querySelectorAll('.thread-item')].some(t => t.textContent.includes('E2E renamed')), null, { timeout: 5000 }).catch(() => {});
  check(!(await page.isVisible('.thread-item:has-text("E2E renamed")')), 'archived conversation leaves the list');
  await page.click('#thread-archived');
  await page.click('.thread-item:has-text("E2E renamed")');
  await page.waitForSelector('#thread-sub:has-text("Archived")');
  await page.click('#thread-menu-toggle'); await page.click('#thread-archive:has-text("Unarchive")');
  await page.waitForFunction(() => !document.getElementById('thread-sub').textContent.includes('Archived'));
  await page.click('#thread-archived');
  await page.waitForSelector('.thread-item:has-text("E2E renamed")');
  check(true, 'archived conversation found under Archived and restored');
  // Delete (confirmed): gone from the list.
  await page.click('.thread-item:has-text("E2E renamed")');
  await page.waitForSelector('#thread-heading:has-text("E2E renamed")');
  await page.click('#thread-menu-toggle'); await page.click('#thread-delete');
  await confirmDialog(page, 'Delete conversation');
  await page.waitForSelector('#notice:has-text("Deleted")');
  check(!(await page.isVisible('.thread-item:has-text("E2E renamed")')) && await page.isHidden('#thread-menu-toggle'), 'conversation deleted after confirming');

  // Tasks: filter, drawer, reject with reason, history, Escape.
  await go(page, 'tasks');
  await page.click('.work-filters button[data-status="human_approval_required"]');
  await page.waitForTimeout(400);
  const statuses = await page.$$eval('#task-rows .pill', p => [...new Set(p.map(x => x.textContent))]);
  check(statuses.length === 1 && statuses[0] === 'Needs approval', `status filter (${statuses})`);
  await page.click('#task-rows tr:first-child');
  await page.waitForSelector('#detail:not([hidden]) .approval-card');
  check(await page.evaluate(() => document.activeElement.id === 'close-detail'), 'focus moves into drawer');
  await page.fill('#detail .approval-card input', 'not now');
  await page.click('#detail .approval-card button:has-text("Reject")');
  // A danger confirm: red, named for the action, focus on Cancel (Enter there cancels nothing by accident).
  const rej = await openDialog(page);
  check(await rej.evaluate(d => d.classList.contains('ui-dialog-danger') && d.querySelector('.ui-dialog-ok').classList.contains('danger')
        && document.activeElement === d.querySelector('.ui-dialog-cancel') && /not now/.test(d.querySelector('.ui-dialog-detail').textContent)),
        'reject is a danger dialog: red button, focus on Cancel, the reason shown');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/dialog-danger.png' });
  await confirmDialog(page, 'Reject request');
  await page.waitForSelector('#detail .timeline li:has-text("Rejected")');
  check(await page.isVisible('#detail .timeline li:has-text("not now")'), 'rejection reason in history');
  check(await page.isHidden('#hla-approval-actions'), 'approval controls gone after decision');
  await page.keyboard.press('Escape');
  check(await page.isHidden('#detail'), 'Escape closes drawer');
  await page.fill('#task-search', 'zzzz-no-match'); await page.waitForTimeout(600);
  check(await page.isVisible('.empty-row:has-text("No tasks match")'), 'empty filter state');
  await page.fill('#task-search', ''); await page.click('.work-filters button[data-status=""]'); await page.waitForTimeout(600);
  await page.click('#task-next'); await page.waitForTimeout(300);
  check((await page.textContent('#task-page')).startsWith('26'), 'pagination to next page');

  // Policy: edit a server level, persisted; unhinged mode shows a banner everywhere until turned off.
  await go(page, 'policy');
  await page.waitForSelector('#policy-servers select[data-server="jellyfin"]');
  check(await page.inputValue('select[data-server="dev"]') === '5' && await page.inputValue('select[data-server="jellyfin"]') === '1', 'policy shows seeded server levels');
  check(await page.inputValue('#policy-agents select[data-name="Infra Agent"]') === '5', 'policy shows agent level');
  check(await page.inputValue('select[data-server="sandbox"]') === '6', 'sandbox seeded at level 6 (free shell)');
  await page.waitForFunction(() => document.querySelector('#sandbox-desc').textContent.includes('192.168.40.204'));
  check(!(await page.isDisabled('#sandbox-reset')) && /Not reset yet/.test(await page.textContent('#sandbox-status')), 'sandbox panel ready to reset');
  await page.click('#sandbox-reset');
  await confirmDialog(page, 'Reset sandbox');
  await page.waitForFunction(() => /Last reset .* done/.test(document.querySelector('#sandbox-status').textContent), null, { timeout: 15000 });
  check(/reset to its clean snapshot/.test(await page.textContent('#notice')), 'sandbox reset from the Policy page');
  check(await page.inputValue('select[data-server="homeassistant"]') === '1' && /save dashboards/.test(await page.textContent('#policy-servers')),
        'Home Assistant listed with its own level (read-only at first)');
  await page.selectOption('select[data-server="jellyfin"]', '3');
  await page.selectOption('select[data-server="homeassistant"]', '4');
  await page.click('#policy-save');
  await confirmDialog(page, 'Apply 2 changes');
  await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('Permissions saved'));
  await go(page, 'overview'); await go(page, 'policy');
  await page.waitForTimeout(400);
  check(await page.inputValue('select[data-server="jellyfin"]') === '3' && await page.inputValue('select[data-server="homeassistant"]') === '4', 'server and Home Assistant level changes persisted');
  check(await page.isVisible('#policy-servers tr:has-text("secrets"):has-text("Agents never see the values")'), 'secrets listed with its level meanings');
  check(await page.isHidden('#unhinged-banner'), 'no unhinged banner while off');
  await page.selectOption('#unhinged-hours', '8');
  await page.click('#unhinged-toggle');
  await confirmDialog(page, 'Turn on unhinged mode');
  await page.waitForSelector('#unhinged-banner:not([hidden])');
  check(/8:|until/.test(await page.textContent('#unhinged-banner-text')) && (await page.textContent('#unhinged-state')).startsWith('On'), 'unhinged on with end time');
  await go(page, 'tasks');
  check(await page.isVisible('#unhinged-banner'), 'unhinged banner visible on other pages');
  await page.evaluate(() => window.scrollTo(0, 2000)); await page.waitForTimeout(200);
  const uh = await page.evaluate(() => { const r = document.getElementById('unhinged-banner').getBoundingClientRect(); const side = document.getElementById('side-status');
    return { top: Math.round(r.top), inView: r.top >= 0 && r.bottom <= innerHeight, side: side.dataset.state, sideText: document.getElementById('side-status-text').textContent }; });
  await page.evaluate(() => window.scrollTo(0, 0));
  check(uh.inView && uh.side === 'warn' && /Unhinged on/.test(uh.sideText), 'unhinged stays in view after scrolling, and the sidebar status says so ' + JSON.stringify(uh));
  await page.click('#unhinged-banner-off');
  await page.waitForSelector('#unhinged-banner', { state: 'hidden' });
  check(true, 'banner Turn off switches unhinged mode off');
  await go(page, 'policy');
  await page.reload(); await page.waitForSelector('#workspace:not([hidden])');
  await page.waitForFunction(() => !document.getElementById('policy').hidden, null, { timeout: 5000 }).catch(() => {});
  check(await page.textContent('#page-title') === 'Security & Permissions' && await page.textContent('#page-crumb') === 'Settings', 'reload returns to the page that was open (Settings › Security & Permissions)');

  // Costs: totals, a daily bar chart (inline SVG), breakdowns, top runs, budgets; period switch; models per kind of work.
  await go(page, 'costs');
  await page.waitForSelector('#cost-chart svg');
  await page.click('#costs .seg button[data-days="30"]');
  await page.waitForFunction(() => document.querySelectorAll('#cost-chart .cost-bar').length === 30);
  const costView = await page.evaluate(() => ({ spent: document.querySelector('#cost-totals .metric-value')?.textContent, marks: document.querySelectorAll('#cost-chart .cost-mark').length,
    label: document.querySelector('#cost-chart svg title')?.textContent || '', kinds: document.querySelectorAll('#cost-kinds .cost-bar-row').length,
    models: document.querySelectorAll('#cost-models tbody tr').length, top: document.querySelectorAll('#cost-top tbody tr.linked').length,
    budgets: [...document.querySelectorAll('#cost-budgets .cost-budget')].map(b => b.textContent) }));
  check(/^\$0\.\d+$/.test(costView.spent) && costView.marks >= 5 && costView.label.startsWith('Daily spend over 30 days') && costView.kinds >= 2 && costView.models >= 1
        && costView.top === 10 && costView.budgets.some(t => t.includes('Bakery website') && t.includes('of $')), 'costs page: totals, chart, breakdowns, top runs and budgets ' + JSON.stringify(costView));
  await page.click('#costs .seg button[data-days="7"]');
  await page.waitForFunction(() => document.querySelectorAll('#cost-chart .cost-bar').length === 7);
  check(await page.getAttribute('#costs .seg button[data-days="7"]', 'aria-pressed') === 'true' && (await page.textContent('#cost-totals .metric-foot')).includes('7 days'),
        'costs page: period switch to 7 days');
  await page.hover('#cost-chart .cost-bar:last-of-type');
  check(await page.isVisible('#cost-chart .cost-tip'), 'costs page: hovering a day shows its figures');
  // HLA_UI_V4: totals, chart and budgets first; the breakdowns and top runs fold open on request.
  check(await page.isVisible('#cost-totals') && await page.isVisible('#cost-budgets') && await page.isHidden('#cost-top'), 'costs page: totals first, breakdown folded');
  await page.click('#cost-breakdown > summary');
  await page.click('#cost-top tbody tr.linked');
  await page.waitForSelector('#detail:not([hidden])');
  check(true, 'costs page: a top run opens its task');
  await page.keyboard.press('Escape');
  // Models per kind of work moved to the Models page; Costs links to it.
  await page.click('#cost-open-models');
  await page.waitForSelector('#models-table tbody tr[data-model]');
  check(await page.textContent('#page-title') === 'Models & Providers', 'costs page links to the Models page');
  await page.waitForSelector('#model-routing select[aria-label="Planning model"]');
  await page.selectOption('#model-routing select[aria-label="Planning model"]', 'anthropic/claude-sonnet-5.5');
  await page.waitForFunction(() => document.getElementById('notice').textContent.includes('Planning: anthropic/claude-sonnet-5.5'));
  const routed = await page.evaluate(async () => (await (await fetch('/ui/api/model-routing')).json()).kinds.find(k => k.kind === 'plan'));
  check(routed.model === 'anthropic/claude-sonnet-5.5' && routed.source === 'dashboard', 'models page: planning model saved');
  await page.selectOption('#model-routing select[aria-label="Planning model"]', '__default');
  await page.waitForFunction(() => document.getElementById('notice').textContent.includes('Planning: back to the default'));

  // Models page (HLA_MODEL_REGISTRY_V1): registry seeded from the env, catalog search, add, default, disable, test.
  const seededIds = await page.$$eval('#models-table tbody tr[data-model]', r => r.map(x => x.dataset.model).filter(m => !/-sub\//.test(m)).sort());  // subscription models are not seeded from the env
  check(JSON.stringify(seededIds) === JSON.stringify(['anthropic/claude-sonnet-5.5', 'qwen/qwen3-235b-a22b']), 'models: registry seeded from AGENT_ALLOWED_MODELS ' + JSON.stringify(seededIds));
  await page.click('#models-sync');
  await page.waitForFunction(() => document.getElementById('notice').textContent.includes('Prices refreshed for 2 models'));
  await page.waitForFunction(() => document.querySelector('#models-table tr[data-model="anthropic/claude-sonnet-5.5"] td[data-label="In / 1M"]')?.textContent === '$3.00');
  const sonnetRow = await page.$eval('#models-table tr[data-model="anthropic/claude-sonnet-5.5"]', r => ({ name: r.querySelector('.model-name strong').textContent,
    ctx: r.querySelector('td[data-label="Context"]').textContent, out: r.querySelector('td[data-label="Out / 1M"]').textContent, tools: r.querySelector('.model-tools').textContent }));
  check(sonnetRow.name === 'Anthropic: Claude Sonnet 5.5' && sonnetRow.ctx === '1M' && sonnetRow.out === '$15.00' && sonnetRow.tools === 'Yes', 'models: refresh prices fills label, context, prices and tools ' + JSON.stringify(sonnetRow));
  await page.fill('#models-catalog-q', 'haiku');
  await page.waitForFunction(() => document.querySelectorAll('#models-catalog .model-card').length === 1 && document.querySelector('#models-catalog .model-card').textContent.includes('anthropic/claude-haiku-5'));
  const cards = await page.$$eval('#models-catalog .model-card', c => c.map(x => x.textContent));
  check(cards.length === 1 && cards[0].includes('200K context') && cards[0].includes('$1.00 in · $5.00 out per 1M') && cards[0].includes('tool calls'), 'models: catalog search shows price, context and tools ' + JSON.stringify(cards));
  await page.click('#models-catalog button[aria-label="Add anthropic/claude-haiku-5"]');
  await page.waitForSelector('#models-table tr[data-model="anthropic/claude-haiku-5"]');
  await page.waitForFunction(() => [...document.querySelectorAll('#agent-model option')].some(x => x.value === 'anthropic/claude-haiku-5'), null, { timeout: 5000 }).catch(() => {});
  check(await page.isVisible('#models-catalog button:has-text("Added")') && await page.$$eval('#agent-model option', o => o.some(x => x.value === 'anthropic/claude-haiku-5')),
        'models: added from the catalog, offered in the agent picker');
  await page.fill('#models-catalog-q', 'storyteller');
  await page.waitForFunction(() => document.querySelectorAll('#models-catalog .model-card').length === 1 && document.querySelector('#models-catalog .model-card.no-tools'));
  check(await page.isVisible('#models-catalog .model-card.no-tools .badge-warn:has-text("no tool calls")'), 'models: a model without tool calls is flagged');
  // HLA_XAI_V1: the xAI source; without XAI_API_KEY the page says so. Registry rows name their provider.
  await page.click('#models-src-xai');
  await page.waitForSelector('#models-catalog p.error:has-text("XAI_API_KEY not set")');
  check((await page.getAttribute('#models-src-xai', 'aria-pressed')) === 'true' && (await page.textContent('#models-src-hint')).includes('XAI_API_KEY is not set')
        && await page.isVisible('#models-table tr[data-model="anthropic/claude-sonnet-5.5"] .model-provider:has-text("OpenRouter")'),
        'models: xAI source explains a missing key; rows show their provider');
  await page.click('#models-src-openrouter');
  await page.waitForFunction(() => document.querySelectorAll('#models-catalog .model-card').length >= 1);
  await page.fill('#models-manual-id', 'vendor/manual-model');
  await page.fill('#models-manual-label', 'Manual one');
  await page.click('#models-manual-add');
  await page.waitForSelector('#models-table tr[data-model="vendor/manual-model"]');
  check(await page.textContent('#notice').then(t => t.includes('Not in the OpenRouter catalog')) && await page.textContent('#models-table tr[data-model="vendor/manual-model"] .model-tools') === 'Unknown',
        'models: manual add by id, warned that the catalog does not know it');
  await page.selectOption('#models-default', 'anthropic/claude-haiku-5');
  await page.waitForFunction(() => document.getElementById('notice').textContent.includes('Default model: anthropic/claude-haiku-5'));
  const defaultNow = await page.evaluate(async () => (await (await fetch('/ui/api/models')).json()).default);
  check(defaultNow.model === 'anthropic/claude-haiku-5' && defaultNow.source === 'dashboard' && (await page.textContent('#models-default-hint')).includes('(set here)'), 'models: default model set');
  await page.click('#models-table tr[data-model="anthropic/claude-haiku-5"] .model-enabled input');
  await page.waitForFunction(() => document.getElementById('notice').textContent.includes('default model'));
  check(await page.isChecked('#models-table tr[data-model="anthropic/claude-haiku-5"] .model-enabled input'), 'models: the default model cannot be disabled');
  await page.selectOption('#models-default', '');
  await page.waitForFunction(() => document.getElementById('notice').textContent.includes('back to OPENROUTER_WORKER_MODEL'));
  await page.click('#models-table tr[data-model="anthropic/claude-haiku-5"] .model-enabled input');
  await page.waitForFunction(() => document.getElementById('notice').textContent.includes('disabled'));
  await page.waitForFunction(() => ![...document.querySelectorAll('#thread-model-override option')].some(x => x.value === 'anthropic/claude-haiku-5'), null, { timeout: 5000 }).catch(() => {});
  check(await page.$eval('#models-table tr[data-model="anthropic/claude-haiku-5"]', r => r.classList.contains('model-off'))
        && !(await page.$$eval('#thread-model-override option', o => o.some(x => x.value === 'anthropic/claude-haiku-5'))), 'models: disabled, gone from the model pickers');
  await page.click('#models-table tr[data-model="qwen/qwen3-235b-a22b"] button:has-text("Test")');
  await page.waitForSelector('#models-table tr[data-model="qwen/qwen3-235b-a22b"] .model-test.ok');
  check((await page.textContent('#models-table tr[data-model="qwen/qwen3-235b-a22b"] .model-test')).includes('“pong”'), 'models: test call shows latency and the reply');
  const usedBy = await page.textContent('#models-table tr[data-model="qwen/qwen3-235b-a22b"] .model-used');
  await page.click('#models-table tr[data-model="qwen/qwen3-235b-a22b"] button:has-text("Remove")');
  await confirmDialog(page, 'Remove model');
  await page.waitForFunction(() => document.getElementById('notice').textContent.includes('Still used by'));
  check(/agent/.test(usedBy) && await page.isVisible('#models-table tr[data-model="qwen/qwen3-235b-a22b"]'), 'models: a model in use is not removed, the page says by what (' + usedBy + ')');
  await page.click('#models-table tr[data-model="vendor/manual-model"] button:has-text("Edit")');
  await page.fill('#models-table tr[data-model="vendor/manual-model"] input[aria-label="Notes"]', 'only for tests');
  await page.click('#models-table tr[data-model="vendor/manual-model"] button:has-text("Save")');
  await page.waitForSelector('#models-table tr[data-model="vendor/manual-model"] .model-notes:has-text("only for tests")');
  await page.click('#models-table tr[data-model="vendor/manual-model"] button:has-text("Remove")');
  await confirmDialog(page);
  await page.waitForSelector('#models-table tr[data-model="vendor/manual-model"]', { state: 'detached' });
  check(true, 'models: edit notes, remove an unused model');
  // HLA_SUBSCRIPTIONS_V1: Claude and ChatGPT plans as model engines (Settings › Models › Subscriptions). The preview
  // runs the fake Claude Code / Codex CLIs (tests/fake_engines): Claude signed in (max), ChatGPT signed out; ChatGPT's sign-in is a
  // device code that the fake approves after ~4 s, Claude's (after signing out) a pasted code (the fake accepts good#fakestate123).
  const SUBS_SHOTS = process.env.SUBS_SHOTS;
  const subsShot = async (pg, name) => { if (SUBS_SHOTS) await pg.screenshot({ path: SUBS_SHOTS + '/' + name + '.png', fullPage: false }); };
  const subsRowsOf = pg => pg.$$eval('#subs-list .subs-row', r => r.map(x => ({ id: x.dataset.engine, pill: x.querySelector('.pill').textContent, text: x.textContent })));
  const subsPill = (pg, id) => pg.textContent('#subs-list .subs-row[data-engine="' + id + '"] .pill');
  const subsGroup = (pg, sel) => pg.$eval(sel, s => { const g = s.querySelector('optgroup.subs-group');
    return g ? { label: g.label, opts: Object.fromEntries([...g.children].map(o => [o.value, { off: o.disabled, text: o.textContent }])) } : null; });
  await go(page, 'models');
  await page.waitForSelector('#subs-list .subs-row[data-engine="chatgpt"]');
  const subsRows = await subsRowsOf(page);
  check(subsRows.length === 2 && subsRows[0].id === 'claude' && subsRows[0].pill === 'Connected' && subsRows[0].text.includes('owner@example.com · Max')
        && subsRows[1].pill === 'Signed out' && /version \d+\.\d+/.test(subsRows[1].text), 'subscriptions: one row per engine with status, account · plan and version ' + JSON.stringify(subsRows.map(r => [r.id, r.pill])));
  check(await page.evaluate(() => document.querySelector('#models > .panel') === document.getElementById('subs-panel')), 'subscriptions: the panel opens Settings › Models');
  const subsPrimary = await page.$$eval('#models button, #models a.button-link', b => b.filter(x => x.offsetParent && /rgb\(39, 199, 232\)|rgb\(75, 211, 239\)/.test(getComputedStyle(x).backgroundColor)).map(x => x.textContent.trim()));
  check(JSON.stringify(subsPrimary) === '["Sign in"]', 'subscriptions: "Sign in" (ChatGPT) is the page\'s one primary ' + JSON.stringify(subsPrimary));
  check(await page.isVisible('#subs-list .subs-row[data-engine="claude"] button:has-text("Test")') && !(await page.$('#subs-list .subs-row[data-engine="claude"] button:has-text("Sign in")')),
        'subscriptions: a connected engine offers Test, not Sign in');
  // Pickers: a "Subscriptions" group; the signed-out engine's models are disabled with "(sign in first)" (also the one the list leaves out).
  await go(page, 'chief'); await page.waitForFunction(() => document.querySelectorAll('#car-model option').length > 0);  // the Chief's model list fills when it opens
  await page.click('#car-exit'); await go(page, 'models'); await page.waitForSelector('#model-routing select[aria-label="Planning model"]');
  // A signed-out engine's model: the first model the engine lists (test data from the backend, not hard-coded).
  const SUB_OFF = await page.evaluate(() => SUBS.data.engines.find(e => e.id === 'chatgpt').models[0].id);
  const pickers = {};
  for (const sel of ['#agent-model', '#thread-model-override', '#task-model-override', '#models-default', '#model-routing select[aria-label="Planning model"]', '#car-model'])
    pickers[sel] = await subsGroup(page, sel);
  const pickOk = g => g && g.label === 'Subscriptions' && g.opts['claude-sub/sonnet'] && !g.opts['claude-sub/sonnet'].off
    && g.opts[SUB_OFF] && g.opts[SUB_OFF].off && g.opts[SUB_OFF].text.endsWith('(sign in first)');
  check(Object.values(pickers).every(pickOk), 'model pickers (agent, conversation, task, default, routing, Chief) group subscription models; signed-out ones disabled '
        + JSON.stringify(Object.fromEntries(Object.entries(pickers).map(([k, g]) => [k, !!pickOk(g)]))));
  check(!(await page.$$eval('#agent-model > option', o => o.some(x => /-sub\//.test(x.value)))), 'model pickers: subscription models only inside their group');
  // Test: one short request on the plan, the result under the row.
  await page.click('#subs-list .subs-row[data-engine="claude"] button:has-text("Test")');
  await page.waitForSelector('#subs-list .subs-row[data-engine="claude"] .model-test.ok');
  check((await page.textContent('#subs-list .subs-row[data-engine="claude"] .model-test')).match(/\d+ ms · “pong”/), 'subscriptions: Test shows the time and the reply');
  // An offered subscription model (no registry row) can be the default model; then back to the environment default.
  await page.selectOption('#models-default', 'claude-sub/sonnet');
  await page.waitForFunction(() => document.getElementById('notice').textContent.includes('Default model: claude-sub/sonnet'));
  const subDefault = await page.evaluate(() => document.getElementById('models-default').value);
  await page.selectOption('#models-default', '');
  await page.waitForFunction(() => document.getElementById('notice').textContent.includes('back to'));
  check(subDefault === 'claude-sub/sonnet', 'models: a signed-in subscription model can be the default model');
  // The "Plan" pill next to a picker while a subscription model is chosen (agent drawer).
  await go(page, 'agents'); await page.click('#agent-new');
  await page.waitForSelector('#agent-model', { state: 'visible' });
  await page.selectOption('#agent-model', 'claude-sub/sonnet');
  const planPill = await page.evaluate(() => { const p = document.getElementById('agent-model').nextElementSibling; return p && p.classList.contains('plan-pill') && !p.hidden && p.offsetParent !== null ? p.textContent : null; });
  const opts = await page.$$eval('#agent-model option', o => o.filter(x => !/-sub\//.test(x.value)).map(x => x.value));
  await page.selectOption('#agent-model', opts[0]);
  check(planPill === 'Plan' && await page.evaluate(() => document.getElementById('agent-model').nextElementSibling.hidden), 'model pickers: a small "Plan" pill while a subscription model is chosen');
  await page.keyboard.press('Escape');
  // Wide (1920) and phone (390) widths: both rows in view, no sideways scroll.
  for (const w of [1920, 390]) {
    await page.setViewportSize({ width: w, height: w === 1920 ? 1080 : 844 });
    await go(page, 'models'); await page.waitForSelector('#subs-list .subs-row[data-engine="chatgpt"]');
    const lay = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth > innerWidth + 1,
      rows: [...document.querySelectorAll('#subs-list .subs-row')].map(r => { const b = r.getBoundingClientRect(); return b.width > 0 && b.left >= 0 && b.right <= innerWidth + 1; }),
      acts: [...document.querySelectorAll('#subs-list .subs-actions button')].every(b => { const r = b.getBoundingClientRect(); return r.right <= innerWidth + 1 && r.height >= 24; }) }));
    check(!lay.overflow && lay.rows.length === 2 && lay.rows.every(Boolean) && lay.acts, 'subscriptions panel at ' + w + ' px: both rows and their actions in view ' + JSON.stringify(lay));
    if (SUBS_SHOTS) { await page.evaluate(() => { document.getElementById('notice').textContent = ''; scrollTo(0, 0); document.querySelector('main').scrollTop = 0; }); await subsShot(page, 'panel-' + w); }
  }
  // ChatGPT on the phone: Sign in → drawer (bottom sheet) → starting → waiting with the sign-in page and a device code → done.
  await page.click('#subs-list .subs-row[data-engine="chatgpt"] button:has-text("Sign in")');
  await page.waitForSelector('#subs-drawer:not([hidden])');
  const started = await page.textContent('#subs-drawer .subs-flow');
  await page.waitForSelector('#subs-user-code', { timeout: 8000 });
  const dev = await page.evaluate(() => { const a = document.getElementById('subs-open'), p = document.querySelector('#subs-drawer .drawer-panel').getBoundingClientRect();
    return { code: document.getElementById('subs-user-code').textContent, href: a && a.href, target: a && a.target, rel: a && a.rel, label: a && a.textContent.trim(),
      fits: p.left >= 0 && p.right <= innerWidth + 1, pill: document.querySelector('#subs-drawer .drawer-meta .pill').textContent }; });
  check(started.includes('Starting the sign-in') && dev.code === 'FAKE-12345' && dev.href === 'https://auth.openai.com/codex/device' && dev.target === '_blank' && /noopener/.test(dev.rel)
        && dev.label === 'Open sign-in page' && dev.fits && dev.pill === 'Signing in…', 'sign-in drawer: the sign-in page opens in a new tab, the device code is large, the sheet fits 390 px ' + JSON.stringify(dev));
  check(await page.isVisible('#subs-drawer button:has-text("Copy")') && await subsPill(page, 'chatgpt') === 'Signing in…', 'sign-in drawer: the code can be copied; the row shows "Signing in…"');
  await subsShot(page, 'drawer-device-390');
  await page.waitForSelector('#subs-drawer .subs-result.ok', { timeout: 12000 });
  check((await page.textContent('#subs-drawer .subs-result.ok')).startsWith('Connected'), 'sign-in drawer: polling every 2 s reaches "Connected" with the account and plan');
  await page.click('#subs-done'); await page.waitForSelector('#subs-drawer', { state: 'hidden' });
  await page.waitForFunction(() => document.querySelector('#subs-list .subs-row[data-engine="chatgpt"] .pill')?.textContent === 'Connected');
  await page.waitForFunction(id => { const o = document.querySelector('#agent-model option[value="' + id + '"]'); return o && !o.disabled; }, SUB_OFF, { timeout: 5000 }).catch(() => {});
  check(await page.$eval('#agent-model option[value="' + SUB_OFF + '"]', o => !o.disabled && !o.textContent.includes('sign in first')), 'after sign-in its models can be picked');
  await page.setViewportSize({ width: 1440, height: 900 });
  // Claude: Sign out (in the More menu, confirmed) → its models disabled in the pickers.
  await page.click('#subs-list .subs-row[data-engine="claude"] .subs-more');
  await page.click('.menu-item:has-text("Sign out")');
  await confirmDialog(page, 'Sign out');
  await page.waitForFunction(() => document.querySelector('#subs-list .subs-row[data-engine="claude"] .pill')?.textContent === 'Signed out');
  await page.waitForFunction(() => document.querySelector('#thread-model-override option[value="claude-sub/sonnet"]')?.disabled === true, null, { timeout: 5000 }).catch(() => {});
  check(await page.$eval('#thread-model-override option[value="claude-sub/sonnet"]', o => o.disabled && o.textContent.endsWith('(sign in first)')), 'sign out (confirmed): the engine is signed out and its models are disabled');
  // Claude: Sign in → Cancel (login/cancel) → Sign in again → paste the code → Connected.
  await page.click('#subs-list .subs-row[data-engine="claude"] button:has-text("Sign in")');
  await page.waitForSelector('#subs-code-input', { timeout: 8000 });
  await page.click('#subs-cancel'); await page.waitForSelector('#subs-drawer', { state: 'hidden' });
  await page.waitForFunction(() => document.querySelector('#subs-list .subs-row[data-engine="claude"] .pill')?.textContent === 'Signed out');
  check(true, 'sign-in drawer: Cancel stops the sign-in');
  await page.click('#subs-list .subs-row[data-engine="claude"] button:has-text("Sign in")');
  await page.waitForSelector('#subs-code-input', { timeout: 8000 });
  check(!(await page.$('#subs-user-code')) && /^https:\/\/claude\.com\/cai\/oauth\/authorize\?/.test(await page.getAttribute('#subs-open', 'href')),
        'sign-in drawer: a pasted-code sign-in shows the sign-in page and a "Paste the code" field');
  await page.fill('#subs-code-input', 'good#fakestate123');
  await page.waitForTimeout(2300);  // a poll in between must keep the typed code
  check(await page.inputValue('#subs-code-input') === 'good#fakestate123', 'sign-in drawer: polling keeps a code being typed');
  await subsShot(page, 'drawer-code-1440');
  await page.click('#subs-code-submit');
  await page.waitForSelector('#subs-drawer .subs-result.ok', { timeout: 8000 });
  check((await page.textContent('#subs-drawer .subs-result.ok')).includes('owner@example.com · Max'), 'sign-in drawer: the pasted code signs in');
  await page.click('#subs-done');
  await page.waitForFunction(() => document.querySelector('#subs-list .subs-row[data-engine="claude"] .pill')?.textContent === 'Connected');
  if (SUBS_SHOTS) { await page.evaluate(() => { document.getElementById('notice').textContent = ''; scrollTo(0, 0); }); await subsShot(page, 'panel-1440-connected'); }
  // Costs and runs: subscription runs read "Subscription", not $0.00.
  await go(page, 'costs'); await page.waitForSelector('#cost-models tbody tr', { state: 'attached' });
  const subCost = await page.$$eval('#cost-models tbody tr', r => r.map(x => [...x.cells].map(c => c.textContent)).find(c => c[0] === 'claude-sub/sonnet'));
  check(subCost && subCost[1] === 'Subscription' && subCost[3] === 'Subscription' && (await page.textContent('#cost-range')).includes('on subscriptions'), 'costs: subscription runs read "Subscription" ' + JSON.stringify(subCost));
  await go(page, 'tasks'); await page.fill('#task-search', 'Draft release notes');
  await page.waitForFunction(() => [...document.querySelectorAll('#task-rows tr[data-id]')].some(r => r.textContent.includes('claude-sub/sonnet')));
  const subRun = await page.$$eval('#task-rows tr[data-id]', r => r.filter(x => x.textContent.includes('claude-sub/sonnet')).map(x => x.querySelector('td.num').textContent));
  await page.click('#task-rows tr[data-id]:has-text("claude-sub/sonnet")');
  await page.waitForSelector('#detail:not([hidden])');
  const subFact = await page.$$eval('#detail-facts dt', d => { const c = d.find(x => x.textContent === 'Cost'); return c ? c.nextElementSibling.textContent : null; });
  check(subRun.length >= 1 && subRun.every(t => t === 'Subscription') && subFact === 'Subscription (your plan)', 'runs on a subscription model: "Subscription" in the list and the details ' + JSON.stringify([subRun, subFact]));
  await page.keyboard.press('Escape'); await page.fill('#task-search', '');
  // MCP page (HLA_MCP_V1): servers from the gateway registry, tools with hints and levels, overrides, add/refresh/remove.
  // HLA_UI_V5: one row per server; the server (level, connection, actions, tools) opens in a drawer.
  const mcpOpen = async srv => {
    if (await page.$('#mcp-drawer:not([hidden]) .mcp-server[data-server="' + srv + '"]')) return;
    if (await page.$('#mcp-drawer:not([hidden])')) { await page.keyboard.press('Escape'); await page.waitForSelector('#mcp-drawer', { state: 'hidden' }); }
    await page.click('.mcp-row[data-server="' + srv + '"]');
    await page.waitForSelector('#mcp-drawer:not([hidden]) .mcp-server[data-server="' + srv + '"]');
  };
  await go(page, 'mcp');
  await page.waitForSelector('.mcp-row[data-server="notion"]');
  check(await page.textContent('#page-title') === 'Tools & MCP' && (await page.$$eval('.mcp-row', x => x.map(e => e.dataset.server))).join() === 'files-lan,notion,weather-down'
        && !(await page.$('#mcp-servers .mcp-tool, #mcp-servers select')), 'MCP page lists the registered servers as rows (no tools or selects on the page)');
  check(await page.isVisible('.mcp-row[data-server="notion"] .pill:has-text("6 tools")') && await page.isVisible('.mcp-row[data-server="notion"] .row-side:has-text("Level 1")'), 'MCP: a row shows the tool count and level');
  await mcpOpen('notion');
  check(await page.isVisible('#mcp-drawer .mcp-server[data-server="notion"] .pill:has-text("6 tools")') && await page.inputValue('select[data-mcp-level="notion"]') === '1',
        'MCP: tool count and the service level of mcp:notion (in the drawer)');
  await mcpOpen('weather-down');
  check(await page.isVisible('.mcp-row[data-server="weather-down"] .pill:has-text("Error")') && await page.isVisible('.mcp-server[data-server="weather-down"] .mcp-error:has-text("mcp_unreachable")'),
        'MCP: a server that failed to connect shows its error');
  await mcpOpen('files-lan');
  check(await page.isVisible('.mcp-server[data-server="files-lan"] .badge:has-text("FILES_MCP_KEY is not stored")') && await page.isVisible('.mcp-server[data-server="files-lan"] .badge:has-text("LAN allowed")')
        && await page.isVisible('.mcp-row[data-server="files-lan"] .badge:has-text("credential missing")'),
        'MCP: a missing credential and private-network access are flagged (row and drawer)');
  await mcpOpen('notion');
  const mcpRow = (srv, t) => '.mcp-server[data-server="' + srv + '"] .mcp-tool[data-tool="' + t + '"]';
  check(await page.isVisible(mcpRow('notion', 'search') + ' .badge:has-text("read-only")') && await page.isVisible(mcpRow('notion', 'delete_block') + ' .badge:has-text("destructive")'),
        'MCP: tool hints are shown');
  check(await page.inputValue(mcpRow('notion', 'update_page') + ' select') === '6' && await page.inputValue(mcpRow('notion', 'search') + ' select') === '1'
        && await page.inputValue(mcpRow('notion', 'create_page') + ' select') === ''
        && /Default · level 4/.test(await page.textContent(mcpRow('notion', 'create_page') + ' select')), 'MCP: default (4, read-only hints not trusted) and overridden level per tool');
  check(await page.isVisible(mcpRow('notion', 'export_workspace') + ' .badge:has-text("new — review")') && await page.isVisible(mcpRow('notion', 'export_workspace') + '.mcp-tool-off')
        && await page.isVisible(mcpRow('notion', 'export_workspace') + ' .mcp-tool-eff:has-text("off until you review it")')
        && await page.isVisible('.mcp-server[data-server="notion"] .mcp-tools summary:has-text("1 new to review")'), 'MCP: a tool the server added later is off until reviewed');
  check(await page.isVisible(mcpRow('notion', 'create_page') + ' .badge:has-text("changed: description")'), 'MCP: a changed description is flagged');
  await page.click(mcpRow('notion', 'export_workspace') + ' button.mcp-enable');
  await page.waitForSelector(mcpRow('notion', 'export_workspace') + ':not(.mcp-tool-off)');
  check(/export_workspace enabled at level 4/.test(await page.textContent('#notice')) && await page.inputValue(mcpRow('notion', 'export_workspace') + ' select') === '4'
        && !(await page.isVisible(mcpRow('notion', 'export_workspace') + ' .badge:has-text("new — review")')), 'MCP: Enable reviews a new tool (an override at its default level)');
  check(await page.isVisible(mcpRow('notion', 'create_page') + ' .mcp-tool-eff:has-text("above the server level")'), 'MCP: tools above the server level are marked');
  await page.selectOption(mcpRow('notion', 'delete_block') + ' select', 'off');
  await page.waitForSelector(mcpRow('notion', 'delete_block') + '.mcp-tool-off');
  check(/delete_block: disabled/.test(await page.textContent('#notice')), 'MCP: a tool can be disabled');
  await page.selectOption(mcpRow('notion', 'delete_block') + ' select', '');
  await page.waitForSelector(mcpRow('notion', 'delete_block') + ':not(.mcp-tool-off)');
  check(await page.inputValue(mcpRow('notion', 'delete_block') + ' select') === '', 'MCP: back to the default level');
  await page.selectOption('select[data-mcp-level="notion"]', '4');
  await confirmDialog(page, 'Set level 4');
  await page.waitForFunction(() => /mcp:notion is now at level 4/.test(document.getElementById('notice').textContent));
  await page.waitForSelector(mcpRow('notion', 'create_page') + ' .mcp-tool-eff:has-text("agents at level 4+")');
  check(true, 'MCP: raising the service level brings tools within reach');
  await page.keyboard.press('Escape'); await page.waitForSelector('#mcp-drawer', { state: 'hidden' });
  await go(page, 'policy');
  await page.waitForSelector('#policy-servers select[data-server="mcp:notion"]');
  check(await page.inputValue('select[data-server="mcp:notion"]') === '4' && await page.isVisible('#policy-servers tr:has-text("mcp:notion") .req-sub:has-text("per-tool levels on the MCP page")'),
        'MCP: the server is a service on the Policy page');
  await go(page, 'mcp');
  await page.waitForSelector('.mcp-row[data-server="notion"]');
  await page.click('#mcp-add');
  check(!(await page.isHidden('#mcp-form-panel')) && await page.isHidden('#mcp-cred-label'), 'MCP: the add form opens, no credential without auth');
  await page.fill('#mcp-name', 'linear'); await page.fill('#mcp-url', 'https://mcp.linear.app/mcp');
  await page.selectOption('#mcp-auth', 'bearer');
  check(await page.isVisible('#mcp-cred-label') && JSON.stringify(await page.$$eval('#mcp-cred option', o => o.map(x => x.value))) === JSON.stringify(['', 'GITHUB_TOKEN', 'NOTION_TOKEN', 'HA_MCP_KEY']),
        'MCP: the credential picker lists stored names');
  await page.selectOption('#mcp-cred', 'GITHUB_TOKEN');
  await page.click('#mcp-save');
  await page.waitForSelector('.mcp-row[data-server="linear"] .pill:has-text("2 tools")');
  await mcpOpen('linear');
  check(await page.isHidden('#mcp-form-panel') && await page.inputValue('select[data-mcp-level="linear"]') === '0'
        && await page.isVisible('.mcp-server[data-server="linear"] .mcp-meta:has-text("Bearer token · GITHUB_TOKEN")'), 'MCP: a new server is saved, its tools loaded, at level 0');
  await page.click('.mcp-server[data-server="linear"] button:has-text("Edit")');
  check(await page.isHidden('#mcp-drawer') && await page.isDisabled('#mcp-name') && await page.inputValue('#mcp-url') === 'https://mcp.linear.app/mcp' && await page.inputValue('#mcp-cred') === 'GITHUB_TOKEN',
        'MCP: the edit form is filled in');
  await page.fill('#mcp-url', 'http://mcp.linear.app/mcp'); await page.click('#mcp-save');
  await page.waitForFunction(() => /allow plain http/.test(document.getElementById('notice').textContent));
  check(!(await page.isHidden('#mcp-form-panel')), 'MCP: the gateway refuses plain http to a public host and the form stays open');
  await page.click('#mcp-cancel');
  // A URL with a token in it: kept in the credential store, picked by name.
  await page.click('#mcp-add');
  await page.fill('#mcp-name', 'secret-url');
  await page.check('#mcp-url-secret');
  check(await page.isHidden('#mcp-url-label') && await page.isVisible('#mcp-url-cred-label'), 'MCP: "URL is stored as a credential" swaps the URL field for a credential picker');
  await page.selectOption('#mcp-url-cred', 'HA_MCP_KEY');
  await page.click('#mcp-save');
  await page.waitForSelector('.mcp-row[data-server="secret-url"]');
  await mcpOpen('secret-url');
  await page.waitForSelector('.mcp-server[data-server="secret-url"] .mcp-meta:has-text("URL from credential HA_MCP_KEY")');
  check(await page.isHidden('#mcp-form-panel'), 'MCP: a server whose URL is a credential is saved and shows the credential name only');
  await page.click('.mcp-server[data-server="secret-url"] button:has-text("Remove")');
  await confirmDialog(page, 'Remove server');
  await page.waitForSelector('.mcp-row[data-server="secret-url"]', { state: 'detached' });
  check(await page.isHidden('#mcp-drawer'), 'MCP: removing the open server closes its drawer');
  await mcpOpen('weather-down');
  await page.click('.mcp-server[data-server="weather-down"] button:has-text("Refresh tools")');
  await page.waitForFunction(() => /mcp_unreachable/.test(document.getElementById('notice').textContent));
  check(true, 'MCP: a failed refresh says why');
  if (process.env.SHOTS) { await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: process.env.SHOTS + '/mcp.png', fullPage: true }); }
  await mcpOpen('linear');
  await page.click('.mcp-server[data-server="linear"] button:has-text("Remove")');
  await confirmDialog(page);
  await page.waitForSelector('.mcp-row[data-server="linear"]', { state: 'detached' });
  check(/linear removed/.test(await page.textContent('#notice')), 'MCP: a server can be removed');
  // Websites (HLA_WEBSITES_V1): the inventory of public-website from the (mocked) gateway, with the real parsing and matching.
  await go(page, 'overview');
  await page.waitForSelector('#ws-tile-text .ws-tile-count');
  const tileText = await page.textContent('#ws-tile-text');
  check(/4 up/.test(tileText) && /1 degraded/.test(tileText) && /1 down/.test(tileText) && await page.$eval('#ws-tile', t => t.classList.contains('ws-tile-alert')),
        'Overview tile: websites up / degraded / down from the last check (' + tileText.trim() + ')');
  if (await page.isVisible('.inbox-more:has-text("Show all")')) await page.click('.inbox-more');
  check(await page.isVisible('.inbox-website:has-text("Production website down"):has-text("shop")') && await page.isVisible('.inbox-website:has-text("HTTP 502")')
        && await page.getAttribute('.inbox-website a:has-text("Open site")', 'href') === 'https://shop.example.net/', 'inbox: a production website that is down');
  await page.click('.inbox-website button:has-text("Open Websites")');
  // HLA_UI_V4: compact status rows; a site's details (addresses, certificate, containers, Traefik routes) open in a drawer.
  await page.waitForSelector('#ws-drawer:not([hidden]) .ws-details[data-id="shop"]');
  check(await page.textContent('#page-title') === 'Websites' && await page.isVisible('.ws-row[data-id="shop"] .ws-status:has-text("Down")')
        && await page.isVisible('#ws-drawer .ws-details[data-id="shop"] .ws-url-facts:has-text("HTTP 502")') && await page.isVisible('#ws-drawer .ws-cont-row:has-text("shop-web-1")'),
        'the inbox item opens the Websites view with that site\'s details in the drawer');
  check(JSON.stringify(await page.$$eval('.ws-table thead th', t => t.map(x => x.textContent))) === JSON.stringify(['Website', 'Status', 'Response', 'Checked'])
        && !(await page.$('.ws-row .ws-cont-row, .ws-row button')), 'websites: compact rows (Website, Status, Response, Checked; the environment is the group), no details or buttons in the rows');
  await page.keyboard.press('Escape');
  await page.waitForSelector('#ws-drawer', { state: 'hidden' });
  const wsHtml = await page.content();
  check(!wsHtml.includes('PREVIEWSECRETHASH') && !wsHtml.includes('apr1'), 'no basicAuth hash reaches the page');
  check(JSON.stringify(await page.$$eval('.ws-group', g => g.map(x => x.dataset.env))) === JSON.stringify(['production', 'staging', 'test']),
        'websites grouped by environment');
  check(await page.isVisible('.ws-row[data-id="production"] .ws-status:has-text("Up")') && await page.isVisible('.ws-row[data-id="production"] .ws-url-facts:has-text("HTTP 200 · 184 ms")')
        && await page.isVisible('.ws-row[data-id="production"] .ws-env:has-text("guessed")'), 'production: up, HTTP code and response time, environment marked as guessed');
  const link = await page.$eval('.ws-row[data-id="production"] a.ws-url', a => ({ href: a.href, target: a.target, rel: a.rel }));
  check(link.href === 'https://example.net/' && link.target === '_blank' && /noopener/.test(link.rel), 'URLs open in a new tab with rel=noopener');
  check(await page.isVisible('.ws-row[data-id="northwind-staging"] .ws-status:has-text("Up · protected")') && await page.isVisible('.ws-row[data-id="northwind-staging"] .badge:has-text("login / allow-list")'),
        'staging behind basicAuth shows as up · protected');
  const shopGroup = await page.$eval('.ws-row[data-id="shop"]', r => { let g = r.previousElementSibling; while (g && !g.classList.contains('ws-group')) g = g.previousElementSibling; return g && g.dataset.env; });
  check(shopGroup === 'production' && !(await page.isVisible('.ws-row[data-id="shop"] .ws-env-guessed')) && !(await page.$('.ws-envs, .ws-envcell'))
        && await page.isVisible('.ws-row[data-id="shop"] .ws-reason:has-text("exited (code 137)")'), 'shop: in the production group (by label, not guessed; one environment filter), down with the reason');
  check(await page.isVisible('.ws-row[data-id="route:old-blog"] .badge:has-text("route without a running container")') && await page.isVisible('.ws-row[data-id="uptime-kuma"] .badge:has-text("no public URL")')
        && await page.getAttribute('.ws-row[data-id="uptime-kuma"] a.ws-url', 'href') === 'http://192.168.30.11:3001', 'a route without a container and a container without a route (LAN URL)');
  check(await page.isVisible('.ws-src:has-text("Routes from the Traefik API")') && await page.isVisible('#ws-source:has-text("1 other Traefik route points to other machines")')
        && /4 up/.test(await page.textContent('.ws-total-up')) === false && await page.textContent('.ws-total-up .ws-total-n') === '3'
        && await page.textContent('.ws-total-down .ws-total-n') === '1', 'data source and summary counts shown');
  await page.click('.ws-row[data-id="northwind-staging"]');
  await page.waitForSelector('#ws-drawer:not([hidden]) .ws-details[data-id="northwind-staging"]');
  const wsd = '#ws-drawer .ws-details[data-id="northwind-staging"]';
  check(await page.isVisible(wsd + ' .ws-mws .badge:has-text("staging-auth (basicauth)")')
        && await page.isVisible(wsd + ' .ws-mws .badge:has-text("staging-ipallow (ipallowlist)")')
        && await page.isVisible(wsd + ' .ws-backend:has-text("http://192.168.30.11:8082")'), 'details: routes with middlewares by name and type, backend (in the drawer)');
  await page.keyboard.press('Escape');
  await page.click('#ws-filter button[data-env="staging"]');
  check(JSON.stringify(await page.$$eval('.ws-site', r => r.map(x => x.dataset.id))) === JSON.stringify(['northwind-staging']), 'filter by environment');
  await page.click('#ws-filter button[data-env="all"]');
  await page.fill('#ws-search', 'shop');
  check(JSON.stringify(await page.$$eval('.ws-site', r => r.map(x => x.dataset.id))) === JSON.stringify(['shop']), 'search by name');
  await page.fill('#ws-search', ''); await page.dispatchEvent('#ws-search', 'input');
  await page.click('.ws-row[data-id="uptime-kuma"]');
  await page.click('#ws-drawer .ws-edit-btn');
  await page.waitForSelector('#ws-drawer .ws-edit[data-id="uptime-kuma"]');
  await page.selectOption('.ws-edit[data-id="uptime-kuma"] select', 'test');
  await page.fill('.ws-edit[data-id="uptime-kuma"] input[name="display_name"]', 'Uptime Kuma');
  await page.fill('.ws-edit[data-id="uptime-kuma"] textarea', 'Monitoring, LAN only');
  await page.click('.ws-edit[data-id="uptime-kuma"] button[type=submit]');
  await page.waitForFunction(() => { const r = document.querySelector('.ws-row[data-id="uptime-kuma"]'); if (!r) return false; let g = r.previousElementSibling; while (g && !g.classList.contains('ws-group')) g = g.previousElementSibling; return g && g.dataset.env === 'test' && !r.querySelector('.ws-env-guessed'); });
  check(await page.isVisible('.ws-row[data-id="uptime-kuma"] .ws-title:has-text("Uptime Kuma")') && /Uptime Kuma saved/.test(await page.textContent('#notice'))
        && await page.isVisible('#ws-drawer .ws-details[data-id="uptime-kuma"] .ws-facts:has-text("Monitoring, LAN only")') && !(await page.isVisible('#ws-drawer .ws-edit'))
        && await page.$eval('.ws-row[data-id="uptime-kuma"]', r => { let p = r.previousElementSibling; while (p && !p.classList.contains('ws-group')) p = p.previousElementSibling; return p && p.dataset.env === 'test'; }),
        'edit: environment override, display name and notes saved; the site moves to Test');
  await page.keyboard.press('Escape');
  await page.waitForSelector('#ws-drawer', { state: 'hidden' });
  await page.click('#ws-refresh');
  await page.waitForFunction(() => /Websites checked: 6 sites|Checked \d+ s ago; showing that result/.test(document.getElementById('notice').textContent));
  check(await page.isVisible('.ws-src:has-text("checked just now")') && await page.$$eval('.ws-site', r => r.length) === 6,
        'Refresh reads everything again (or shows a read from the last 15 s) (' + await page.textContent('#notice') + ')');
  await page.waitForTimeout(15500);
  await page.click('#ws-refresh');
  await page.waitForFunction(() => /Websites checked: 6 sites/.test(document.getElementById('notice').textContent));
  check(true, 'Refresh after 15 s runs a fresh check');
  if (process.env.SHOTS) { await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: process.env.SHOTS + '/websites.png', fullPage: false }); await page.screenshot({ path: process.env.SHOTS + '/websites-full.png', fullPage: true }); }
  // Domains & routes (HLA_ROUTES_V1): the real gateway route code on a simulated traefik host (preview server).
  // HLA_UI_V4: routes are the second tab of Websites; the tab shows how many requests wait.
  check(await page.textContent('#ws-routes-count') === '1' && await page.isHidden('#rt-panel'), 'Domains & routes is its own tab, with the waiting request counted');
  await page.click('#ws-tabs button[data-tab="routes"]');
  await page.waitForSelector('#rt-panel .rt-request');
  check(await page.isVisible('#rt-panel h2:has-text("Domains & routes")') && await page.isVisible('.rt-request:has-text("alpha-staging.example.net")')
        && await page.isVisible('#rt-status:has-text("example.com, example.net")') && await page.isVisible('.rt-empty'),
        'routes panel: domains, an agent\'s pending request, no managed routes yet');
  await page.click('#rt-others summary');
  check(await page.isVisible('.rt-other:has-text("nw-staging@file"):has-text("staging.northwind.example")') && await page.isVisible('.rt-other .badge:has-text("read-only")')
        && !(await page.isVisible('.rt-other button')), 'hand-written routes are listed read-only (no buttons)');
  await page.click('#rt-new');
  await page.waitForSelector('#rt-form-box:not([hidden]) #rt-sub');
  check(JSON.stringify(await page.$$eval('#rt-domain option', o => o.map(x => x.value))) === JSON.stringify(['example.com', 'example.net']), 'new route: domain dropdown from the domains');
  await page.selectOption('#rt-domain', 'example.com');
  await page.fill('#rt-sub', '-bad');
  check(await page.isDisabled('#rt-save') && await page.isVisible('#rt-preview .ws-bad'), 'new route: an invalid subdomain is flagged and cannot be saved');
  await page.fill('#rt-sub', 'shop-staging');
  check(/https:\/\/shop-staging\.example\.com\//.test(await page.textContent('#rt-preview')) && await page.isEnabled('#rt-save'), 'new route: live preview of the address');
  await page.selectOption('#rt-env', 'staging');
  check(await page.inputValue('#rt-protection') === 'lan_only+basic_auth' && await page.isVisible('#rt-user'), 'staging sets LAN only + password (login user shown)');
  const backends = await page.$$eval('#rt-backend option', o => o.map(x => x.value + ' ' + x.textContent));
  check(backends.some(b => b.startsWith('8082 northwind-staging-web-1')) && backends.some(b => b.startsWith('other')), 'backend dropdown: containers and ports from the inventory, plus another port');
  await page.selectOption('#rt-backend', '8082');
  await page.waitForSelector('#rt-dns .rt-dns-warn');
  check(await page.isVisible('#rt-dns:has-text("No DNS record")') && await page.isVisible('#rt-dns code:has-text("shop-staging A 203.0.113.10")'),
        'DNS check inline: missing, with the record to add and a warning');
  await page.click('#rt-save');
  await page.waitForSelector('.rt-route[data-name="shop-staging-example-com"]', { timeout: 15000 });
  const rrow = '.rt-route[data-name="shop-staging-example-com"]';
  check(/Route saved: https:\/\/shop-staging\.example\.com\//.test(await page.textContent('#notice')) && await page.isHidden('#rt-form-box')
        && await page.isVisible(rrow + ' .badge:has-text("LAN only + password")') && await page.isVisible(rrow + ' .rt-cred:has-text("TRAEFIK_BASIC_SHOP_STAGING_EXAMPLE_COM")')
        && await page.isVisible(rrow + ' .pill:has-text("Live")') && await page.isVisible(rrow + ' .rt-dns-state:has-text("No DNS record")')
        && await page.isVisible(rrow + ' .rt-cell:has-text("northwind-staging-web-1")'), 'staging route created: listed with protection, secret name, Traefik live, DNS warning');
  check(!(await page.content()).includes('PreviewBasicAuthPassword') && !(await page.content()).includes('$apr1$'), 'neither the password nor its hash reaches the browser');
  await page.click(rrow + ' .rt-edit');
  await page.waitForSelector('#rt-form-box:not([hidden]) #rt-protection');
  check(await page.inputValue('#rt-sub') === 'shop-staging' && await page.inputValue('#rt-backend') === '8082', 'edit: the form shows the route');
  await page.selectOption('#rt-protection', 'lan_only');
  await page.click('#rt-save');
  await page.waitForFunction(r => { const e = document.querySelector(r + ' .badge'); return e && e.textContent === 'LAN only' && !document.querySelector(r + ' .rt-cred'); }, rrow, { timeout: 15000 });
  check(true, 'edit protection: now LAN only, no login');
  // A host a hand-written router serves is refused with that router's name.
  await page.click('#rt-new');
  await page.selectOption('#rt-domain', 'example.net');
  await page.fill('#rt-sub', 'alpha');
  await page.selectOption('#rt-backend', '8083');
  await page.click('#rt-save');
  await page.waitForSelector('#rt-form-error:not([hidden])', { timeout: 15000 });
  check(/alpha\.example\.net is already served by router alpha \(file, hand-written\)/.test(await page.textContent('#rt-form-error')), 'a host served by a hand-written route is refused with its router');
  await page.click('#rt-form-box button:has-text("Cancel")');
  await page.click(rrow + ' .rt-delete');
  await confirmDialog(page, 'Delete route');
  await page.waitForSelector(rrow, { state: 'detached', timeout: 15000 });
  check(/deleted/.test(await page.textContent('#notice')) && await page.isVisible('.rt-empty'), 'delete (confirmed): the route is gone');
  // An agent's request, approved from "Needs you".
  await go(page, 'overview');
  await page.click('#inbox-refresh');
  if (await page.isVisible('.inbox-more:has-text("Show all")')) await page.click('.inbox-more');
  await page.waitForSelector('.inbox-route_request:has-text("alpha-staging.example.net")');
  check(await page.isVisible('.inbox-route_request .pill:has-text("Route")') && await page.isVisible('.inbox-route_request:has-text("staging · lan_only+basic_auth")'),
        'inbox: the agent\'s route request with environment and protection');
  await page.click('.inbox-route_request button:has-text("Approve")');
  await confirmDialog(page, 'Create route');
  await page.waitForSelector('.inbox-route_request', { state: 'detached', timeout: 15000 });
  check(/Route created: https:\/\/alpha-staging\.example\.net\//.test(await page.textContent('#notice')), 'inbox: Approve creates the route; the item leaves "Needs you"');
  await page.evaluate(() => openWebsite());
  await page.click('#ws-tabs button[data-tab="routes"]');
  await page.waitForSelector('.rt-route[data-name="alpha-staging-example-net"]');
  check(await page.isVisible('.rt-route[data-name="alpha-staging-example-net"] .rt-dns-state:has-text("DNS ok")') && !(await page.isVisible('.rt-request')),
        'the approved route is listed (DNS ok) and no longer requested');
  if (process.env.SHOTS) { await page.evaluate(() => document.getElementById('rt-panel').scrollIntoView()); await page.screenshot({ path: process.env.SHOTS + '/routes.png', fullPage: false }); }
  // Projects: staging and production addresses on the new-project form; the project page offers "Create route".
  await go(page, 'projects');
  await page.click('#project-new-toggle');
  await page.waitForSelector('#project-domains #project-staging-domain');
  await page.selectOption('#project-staging-domain', 'example.com');
  await page.fill('#project-staging-sub', 'bakery-staging');
  await page.fill('#project-production-sub', 'bakery');
  await page.selectOption('#project-production-domain', 'example.net');
  check(await page.textContent('[data-picker="project-staging"] .rt-picker-prev') === 'https://bakery-staging.example.com/', 'project form: staging address from domain dropdown + subdomain');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/project-domains-form.png', fullPage: false });
  await page.fill('#project-title', 'Bakery shop');
  await page.fill('#project-goal', 'Build the Bakery shop and serve it on its staging address.');
  await page.click('#project-form button[type=submit]');
  await page.waitForSelector('.project-head:has-text("Bakery shop")');
  await page.waitForSelector('.project-domains-line a:has-text("bakery-staging.example.com")');
  check(await page.isVisible('.project-domains-line a:has-text("bakery.example.net")') && await page.isVisible('.project-domains-line .project-create-route'),
        'project page: both addresses with Create route');
  // Addresses…: staging and production in one form (was two prompts), a host name checked before it is sent.
  await page.click('.project-domains-line button:has-text("Addresses…")');
  const doms = await openDialog(page);
  check(await doms.$eval('input[name=staging]', i => i.value === 'bakery-staging.example.com') && await doms.$eval('input[name=production]', i => i.value === 'bakery.example.net'),
        'Addresses form shows both current addresses');
  await page.fill(DLG + ' input[name=production]', 'https://shop.example.net/');
  await page.click(DLG + ' .ui-dialog-ok');
  check(await page.isVisible(DLG) && await page.isVisible(DLG + ' .ui-field-error:has-text("full host name")'), 'Addresses form: a URL instead of a host name is explained inline');
  await fillDialog(page, { production: 'Shop.example.net' }, 'Save addresses');
  await page.waitForSelector('.project-domains-line a:has-text("shop.example.net")');
  check(await page.isVisible('.project-domains-line a:has-text("bakery-staging.example.com")') && !(await page.isVisible('.project-domains-line a:has-text("bakery.example.net")')),
        'project domains form saves staging and production together');
  await page.click('.project-domains-line .project-domain:has-text("bakery-staging") .project-create-route');
  await page.waitForSelector('#rt-form-box:not([hidden]) #rt-sub');
  check(await page.inputValue('#rt-sub') === 'bakery-staging' && await page.inputValue('#rt-domain') === 'example.com' && await page.inputValue('#rt-env') === 'staging'
        && await page.inputValue('#rt-protection') === 'lan_only+basic_auth', 'Create route opens the route form prefilled from the project');
  await page.click('#rt-form-box button:has-text("Cancel")');
  // HLA_PROXMOX_V1: Infrastructure shows both nodes from the gateway; create a VM (form, spec preview, background job);
  // an agent's create request waits as an approval card with the full spec and runs once when approved.
  await go(page, 'infrastructure');
  await page.click('#infra-tabs button[data-tab="vms"]');
  await page.waitForSelector('#px-vms .px-vm[data-node="pve2"]', { timeout: 15000 });
  check(await page.isVisible('#px-vms .px-vm[data-node="proxmox"][data-vmid="104"]:has-text("dev")') && await page.isVisible('#px-vms .px-vm[data-node="pve2"][data-vmid="204"]:has-text("sandbox-vm")')
        && await page.isVisible('#px-vms .px-vm[data-node="proxmox"][data-vmid="109"] .badge:has-text("protected")'), 'infrastructure: both nodes with their VMs');
  // HLA_UI_V4: an overview from the real data first; rows show Name | State | CPU | RAM | Host and carry no buttons.
  check(/Proxmox nodes\s*2 of 2/.test(await page.textContent('#infra-overview')) && /Virtual machines/.test(await page.textContent('#infra-overview'))
        && JSON.stringify(await page.$$eval('.px-table thead th', t => t.map(x => x.textContent))) === JSON.stringify(['Name', 'State', 'CPU', 'RAM', 'Host'])
        && !(await page.$('#px-vms .px-vm button')), 'infrastructure: overview strip, compact VM table without per-row buttons');
  await page.fill('#px-search', 'sandbox');
  check(JSON.stringify(await page.$$eval('#px-vms .px-vm', r => r.map(x => x.dataset.vmid))) === JSON.stringify(['204']), 'infrastructure: filter VMs by name');
  await page.fill('#px-search', '');
  await page.click('#infra-tabs button[data-tab="nodes"]');
  await page.click('#px-nodes .px-node[data-node="pve2"]');
  await page.waitForSelector('#px-drawer:not([hidden]) .px-node-detail[data-node="pve2"]');
  check(await page.inputValue('#px-drawer .px-level select') === '5' && await page.isVisible('#px-drawer .px-cap-ok:has-text("Create VM")'),
        'infrastructure: the node drawer has the node level and the token permissions');
  await page.click('#px-drawer button:has-text("Create VM")');
  let d = await openDialog(page);
  check(await d.$eval('[name="vmid"]', e => e.value) === '111' && await d.$eval('[name="gateway"]', e => e.value) === '192.168.10.1'
        && await d.$eval('[name="bridge"]', e => e.value) === 'vmbr0' && await d.$eval('[name="storage"]', e => e.value) === 'local-lvm',
        'create form: VMID from the next free one on both nodes, detected gateway, bridge and storage');
  await fillDialog(page, { name: 'dev', ip: '192.168.10.210/24' }, 'Preview');
  d = await openDialog(page);
  check(/Not possible yet: .*dev/.test(await d.textContent()), 'create form: the gateway\'s refusal is shown and the form stays open');
  await fillDialog(page, { name: 'grafana' }, 'Preview');
  d = await openDialog(page);
  const specText = await d.textContent();
  check(specText.includes('proxmox create_vm on pve2') && specText.includes('vmid: 111') && specText.includes('192.168.10.210/24, gateway 192.168.10.1')
        && specText.includes('SSH_KEY_GRAFANA'), 'create: the spec preview before anything happens');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/proxmox-preview.png' });
  await confirmDialog(page, 'Create VM');
  await page.waitForSelector('.px-job:has-text("grafana") .pill:has-text("Creating")', { timeout: 8000 });
  check(true, 'create: runs in the background with a job line');
  await page.waitForSelector('.px-job:has-text("grafana") .pill:has-text("Created")', { timeout: 30000 });
  await page.click('#infra-tabs button[data-tab="vms"]');
  await page.waitForSelector('#px-vms .px-vm[data-node="pve2"][data-vmid="111"] .px-badge-managed', { timeout: 10000 });
  await page.click('#infra-tabs button[data-tab="servers"]');
  const srv = '[data-pane="servers"] .px-server[data-server="grafana"]';
  check(await page.isVisible(srv + ':has-text("192.168.10.210")') && await page.isVisible(srv + ' .px-fp:has-text("SHA256:")')
        && await page.inputValue(srv + ' select') === '0', 'create: the VM is managed and a server at level 0 with its host key');
  await page.selectOption(srv + ' select', '1');
  await page.waitForSelector('#notice:has-text("grafana is now at level 1")');
  check(await page.inputValue(srv + ' select') === '1', 'created server: level shortcut');
  // A VM's drawer: facts, agent access, and the power and snapshot actions folded into one menu.
  await page.click('#infra-tabs button[data-tab="vms"]');
  await page.click('#px-vms .px-vm[data-vmid="111"]');
  await page.waitForSelector('#px-drawer:not([hidden]) .px-detail[data-vmid="111"]');
  check(await page.inputValue('#px-drawer .px-server[data-server="grafana"] select') === '1' && /192\.168\.10\.210/.test(await page.textContent('#px-drawer .drawer-body')),
        'VM drawer: specs, IP and the created server\'s level');
  await page.keyboard.press('Escape');
  await page.click('#px-vms .px-vm[data-vmid="110"]');
  await page.waitForSelector('#px-drawer:not([hidden]) .px-detail[data-vmid="110"]');
  check(await page.isVisible('#px-drawer .px-actions-btn') && !(await page.isVisible('#px-drawer .menu')), 'VM drawer: the actions are folded into one menu');
  await page.click('#px-drawer .px-actions-btn');
  check(await page.isVisible('#px-drawer .menu .menu-item:has-text("Start")') && await page.isVisible('#px-drawer .menu .menu-item:has-text("Roll back")')
        && !(await page.isVisible('#px-drawer .menu .menu-item:has-text("Destroy")')), 'VM drawer: power and snapshot actions; no Destroy for a VM not created here');
  await page.click('#px-drawer .menu .menu-item:has-text("Start")');
  await confirmDialog(page, 'Start');
  await page.waitForSelector('#px-vms .px-vm[data-vmid="110"] .pill:has-text("Running")', { timeout: 10000 });
  await page.waitForSelector('#px-drawer .drawer-meta .pill:has-text("Running")', { timeout: 5000 });
  check(true, 'power: Start through an in-page confirmation; the open drawer follows the new state');
  await page.keyboard.press('Escape');
  await page.click('#px-vms .px-vm[data-vmid="111"]');
  await page.click('#px-drawer .px-actions-btn');
  await page.click('#px-drawer .menu .menu-item:has-text("Destroy")');
  d = await openDialog(page);
  check((await d.textContent()).includes('cannot be undone'), 'destroy: says what it removes');
  await cancelDialog(page);
  await page.keyboard.press('Escape');
  await page.waitForSelector('#px-drawer', { state: 'hidden' });
  // The agent's request in its conversation: the full spec on the card, approve once.
  const mv = await page.evaluate(async () => (await (await fetch('/ui/api/threads?q=Monitoring')).json()).threads.find(t => t.title === 'Monitoring VM'));
  await go(page, 'threads');
  await page.click('.thread-item:has-text("Monitoring VM")');
  await page.waitForSelector('.proxmox-card .pill:has-text("Proxmox request awaiting approval")');
  const card = await page.textContent('.proxmox-card');
  check(card.includes('vmid: 205') && card.includes('memory: 4096 MiB') && card.includes('192.168.10.205/24') && card.includes('on proxmox:pve2'),
        'approval card: the full spec of the agent\'s create request');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/proxmox-card.png' });
  await page.click('.proxmox-card button:has-text("Approve & run once")');
  await confirmDialog(page, 'Approve');
  await page.waitForSelector('.proxmox-card .pill:has-text("Approved · done")', { timeout: 40000 });
  check(!!mv, 'approval card: approved, the gateway created the VM once');
  await go(page, 'infrastructure');
  await page.click('#infra-tabs button[data-tab="vms"]');
  await page.waitForSelector('#px-vms .px-vm[data-node="pve2"][data-vmid="205"]:has-text("monitoring") .px-badge-managed', { timeout: 15000 });
  await page.click('#infra-tabs button[data-tab="servers"]');
  check(await page.isVisible('[data-pane="servers"] .px-server[data-server="monitoring"]'), 'approval card: VM 205 monitoring is on the Infrastructure view');
  check(await page.isVisible('#infra-alerts .alert-row:has-text("Omada controller: certificate to confirm")'), 'infrastructure: the controller\'s certificate is one of the warnings');
  await page.click('#infra-alerts .alert-row:has-text("Omada controller")');
  // HLA_OMADA_V1: the Omada panel shows the controller and the certificate to trust; nothing connects before that.
  await page.waitForSelector('#om-body .pill', { timeout: 15000 });
  check(await page.isVisible('#om-body .pill:has-text("Certificate to confirm")') && (await page.textContent('#om-body')).includes('5.15.20.18')
        && (await page.textContent('#om-body')).includes('omadacId c0ffee12preview') && await page.inputValue('#om-level') === '5',
        'omada panel: controller version, omadacId, level and a certificate to confirm');
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/omada-panel.png' });
  await page.click('#om-trust');
  d = await openDialog(page);
  check((await d.textContent()).includes('Trust Omada certificate'), 'omada panel: trusting asks first, with the fingerprint');
  await confirmDialog(page, 'Trust certificate');
  await page.waitForSelector('#om-body .pill:has-text("Connected")', { timeout: 15000 });
  check((await page.textContent('#om-body')).includes('1 site: Home') && !(await page.isVisible('#om-trust')), 'omada panel: connected after trust, sites listed');
  // The agent's DHCP reservation waits as an Omada card; approving runs it once.
  await go(page, 'threads');
  await page.click('.thread-item:has-text("Monitoring DHCP")');
  await page.waitForSelector('.omada-card .pill:has-text("Omada change awaiting approval")', { timeout: 15000 });
  const omCard = await page.textContent('.omada-card');
  check(omCard.includes('omada write on controller') && omCard.includes('"ip": "192.168.10.205"') && omCard.includes('the Omada controller'),
        'omada card: the exact write (method, path, body)');
  await page.click('.omada-card button:has-text("Approve & run once")');
  d = await openDialog(page);
  check((await d.textContent()).includes('Approve Omada change'), 'omada card: confirmation names the controller');
  await confirmDialog(page, 'Approve');
  await page.waitForSelector('.omada-card .pill:has-text("Approved · done")', { timeout: 40000 });
  check(true, 'omada card: approved, the gateway wrote it once');
  await page.evaluate(() => fetch('/ui/api/logout', { method: 'POST', headers: { 'x-agent-ui': '1' } }));
  // A background request may notice the ended session first and hide the workspace: click without waiting for it.
  await page.evaluate(() => document.querySelector('button[data-view="tasks"]').click());
  await page.waitForSelector('#login:not([hidden])', { timeout: 5000 }).catch(() => {});
  check(await page.isVisible('#login-error:has-text("session ended")'), 'an expired session returns to sign-in and says so');

  // Phone: list → thread → back.
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  phone.on('pageerror', e => errors.push(e.message)); nativeGuard(phone);
  await phone.goto((process.env.BASE || 'http://127.0.0.1:8765') + '/ui/');
  await phone.fill('#password', 'pw'); await phone.click('#login-form button[type=submit]');
  await phone.waitForSelector('#workspace:not([hidden])');
  await go(phone, 'threads'); await phone.waitForSelector('.thread-item');
  check(await phone.isHidden('.chat-main'), 'phone shows list first');
  await phone.click('.thread-item:has-text("Backup")'); await phone.waitForSelector('#thread-heading:has-text("Backup")');
  check(await phone.isHidden('.chat-sidebar') && await phone.isVisible('#thread-back'), 'phone shows thread with back button');
  await phone.click('#thread-back');
  check(await phone.isVisible('.chat-sidebar'), 'back returns to list');
  // Phone layout: bottom tab bar with labels, no sideways scroll on any page, logout reachable.
  const bar = await phone.$eval('aside', a => { const r = a.getBoundingClientRect(); return { bottom: Math.round(r.bottom), top: Math.round(r.top), pos: getComputedStyle(a).position }; });
  check(bar.pos === 'fixed' && bar.bottom === 844 && bar.top > 700, 'phone tab bar sits at the bottom ' + JSON.stringify(bar));
  // HLA_UI_V4: Log out and the approver status live in the profile sheet behind the header's avatar.
  check(await phone.isHidden('#logout-mobile'), 'phone header: no Log out button next to the icons');
  await phone.click('#profile-mobile');
  await phone.waitForSelector('#profile-sheet:not([hidden]) #logout-mobile');
  check(await phone.isVisible('aside .nav-section[data-section="work"] span') && await phone.isVisible('#logout-mobile') && /Approver/.test(await phone.textContent('#profile-sheet')),
        'phone tabs have labels; Log out and the approver status are in the profile sheet');
  await phone.keyboard.press('Escape');
  await phone.waitForSelector('#profile-sheet', { state: 'hidden' });
  // HLA_UI_V3: the tab bar holds the five sections and fits without scrolling; a section's pages are tabs at the top.
  const tabs = await phone.$$eval('aside .nav', b => b.filter(x => getComputedStyle(x).display !== 'none' && x.offsetParent).map(x => x.dataset.section));
  const fits = await phone.$eval('aside', a => a.scrollWidth <= a.clientWidth + 1);
  const tapSize = await phone.$$eval('aside .nav-section', b => Math.min(...b.map(x => Math.min(x.getBoundingClientRect().width, x.getBoundingClientRect().height))));
  check(JSON.stringify(tabs) === JSON.stringify(['home', 'chief', 'work', 'infrastructure', 'settings']) && fits && tapSize >= 44,
        'phone tab bar: Home, Chief, Work, Infrastructure, Settings; fits; targets at least 44 px ' + JSON.stringify(tabs) + ' ' + tapSize);
  await phone.click('aside .nav-section[data-section="settings"]');
  const setTabs = await phone.$$eval('#subnav .subnav-group:not([hidden]) button', b => b.map(x => x.dataset.view));
  check(['agents', 'models', 'knowledge', 'connections', 'mcp', 'policy', 'audit', 'costs'].every(v => setTabs.includes(v)) && await phone.evaluate(() => {
    const n = document.getElementById('subnav'); return n.scrollWidth >= n.clientWidth && document.documentElement.scrollWidth <= innerWidth + 1; }),
        'phone Settings: its pages are tabs in a strip that scrolls on its own, not the page ' + JSON.stringify(setTabs));
  await phone.click('#subnav button[data-view="audit"]');
  await phone.waitForSelector('.audit-row');
  check(!(await phone.isHidden('#audit')) && await phone.$eval('aside .nav-section[data-section="settings"]', b => b.classList.contains('active') && b.getAttribute('aria-current') === 'page')
        && !(await phone.$eval('aside .nav-section[data-section="home"]', b => b.classList.contains('active'))), 'a Settings page opens and the Settings tab shows it is current');
  check(await phone.$eval('.audit-table thead', t => getComputedStyle(t).display === 'none') && await phone.$eval('.audit-row', r => getComputedStyle(r).display === 'grid')
        && await phone.isHidden('#audit-filters'), 'phone audit: cards instead of a table, filters folded away');
  await go(phone, 'overview');
  check(!(await phone.$eval('aside .nav-section[data-section="settings"]', b => b.classList.contains('active'))), 'Settings is no longer current on Home');
  await go(phone, 'mcp');
  await phone.click('.mcp-row[data-server="notion"]');
  await phone.waitForSelector('#mcp-drawer .mcp-server[data-server="notion"] .mcp-tool');
  check(await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1) && await phone.$eval('.mcp-tool', r => getComputedStyle(r).gridTemplateColumns.split(' ').length === 1)
        && await phone.$eval('#mcp-drawer .drawer-panel', p => p.getBoundingClientRect().right <= innerWidth + 1),
        'phone MCP page: rows, the server opens as a sheet, no sideways scroll, tools stack');
  if (process.env.SHOTS) await phone.screenshot({ path: process.env.SHOTS + '/mcp-phone.png', fullPage: true });
  await phone.keyboard.press('Escape'); await phone.waitForSelector('#mcp-drawer', { state: 'hidden' });
  await go(phone, 'overview');
  // Phone Websites: from Infrastructure, cards without sideways scroll; #websites opens the view.
  await go(phone, 'websites');
  await phone.waitForSelector('.ws-row[data-id="production"]');
  check(await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1
        && [...document.querySelectorAll('.ws-site')].every(e => e.getBoundingClientRect().right <= innerWidth + 1))
        && await phone.$eval('.ws-table thead', h => getComputedStyle(h).display === 'none'), 'phone Websites: rows stack, no sideways scroll');
  if (process.env.SHOTS) { await phone.evaluate(() => window.scrollTo(0, 0)); await phone.screenshot({ path: process.env.SHOTS + '/websites-phone.png', fullPage: false }); await phone.screenshot({ path: process.env.SHOTS + '/websites-phone-full.png', fullPage: true }); }
  await go(phone, 'overview');
  await phone.goto((process.env.BASE || 'http://127.0.0.1:8765') + '/ui/#websites');
  await phone.waitForSelector('#websites:not([hidden]) .ws-site', { timeout: 8000 });
  check(!(await phone.evaluate(() => location.hash)) && await phone.textContent('#page-title') === 'Websites', 'a #websites link opens the Websites view');
  await go(phone, 'overview');
  const wide = [];
  for (const v of await phone.$$eval('button[data-view]', b => b.map(x => x.dataset.view))) {
    await phone.evaluate(v => document.querySelector(`button[data-view="${v}"]`).click(), v); await phone.waitForTimeout(250);
    if (await phone.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)) wide.push(v);
  }
  check(wide.length === 0, 'no page scrolls sideways on a phone ' + JSON.stringify(wide));
  // HLA_HUD_V1: the redesigned Overview on phones (390 and 360 px): no sideways scroll on the main tabs, the tab bar
  // stays at the bottom, and the command channel sends to the Chief's conversation and shows the reply.
  for (const w of [390, 360]) {
    await phone.setViewportSize({ width: w, height: 800 });
    const over = [];
    for (const v of ['overview', 'threads', 'projects', 'tasks', 'chief']) {
      await phone.evaluate(v => document.querySelector(`button[data-view="${v}"]`).click(), v); await phone.waitForTimeout(300);
      if (await phone.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) over.push(v + ":" + await phone.evaluate(() => { const e = [...document.querySelectorAll("main *")].filter(x => x.getBoundingClientRect().right > innerWidth + 1 && getComputedStyle(x).position !== "fixed").pop(); return e ? e.tagName + "." + e.className : "?"; }));
      if (v === 'chief') await phone.click('#car-exit');
    }
    const tabbar = await phone.$eval('aside', a => { const r = a.getBoundingClientRect(); return getComputedStyle(a).position === 'fixed' && Math.round(r.bottom) === innerHeight && r.height > 40; });
    check(over.length === 0 && tabbar, `HUD at ${w}px: no sideways scroll on Overview, Conversations, Projects, Chief; tab bar at the bottom ` + JSON.stringify(over));
    // HLA_UI_V4: the Infrastructure, Websites, Knowledge and Settings pages fit too.
    const over4 = [];
    for (const v of ['infrastructure', 'websites', 'knowledge', 'agents', 'models', 'mcp', 'connections', 'policy', 'costs', 'audit']) {
      await phone.evaluate(v => document.querySelector(`button[data-view="${v}"]`).click(), v); await phone.waitForTimeout(350);
      if (await phone.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) over4.push(v);
    }
    check(over4.length === 0, `phase 4 pages at ${w}px: no sideways scroll ` + JSON.stringify(over4));
  }
  await phone.setViewportSize({ width: 390, height: 844 });
  await phone.evaluate(() => document.querySelector('button[data-view="overview"]').click());
  const chiefId = (await (await phone.request.get((process.env.BASE || 'http://127.0.0.1:8765') + '/ui/api/chief')).json()).thread_id;
  const cmdPosts = []; phone.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/ui/api/threads/' + chiefId + '/messages')) cmdPosts.push(r.postData()); });
  check(await phone.isVisible('#core') && await phone.isVisible('.kpis') && await phone.isVisible('#cmd-input') && await phone.isHidden('#task-form'), 'Home: Ask Chief with the orb, inline metrics; the launch form waits behind New task');
  await phone.fill('#cmd-input', 'How are the projects doing?'); await phone.click('#cmd-send');
  await phone.waitForFunction(() => !document.getElementById('cmd-reply').hidden && document.getElementById('cmd-send').disabled === false
    && document.getElementById('cmd-reply-body').textContent.trim().length > 0 && !/^How are the projects doing\?$/.test(document.getElementById('cmd-reply-body').textContent.trim()), null, { timeout: 15000 });
  check(cmdPosts.length === 1 && JSON.parse(cmdPosts[0]).content === 'How are the projects doing?' && await phone.inputValue('#cmd-input') === '',
        'command channel: the text goes to the Chief\'s conversation and the reply shows on Overview');
  await phone.click('#ov-new-task'); await phone.waitForSelector('#launch-sheet:not([hidden]) #task-text');
  await phone.keyboard.press('Escape');
  check(await phone.isHidden('#launch-sheet'), 'New task opens the launch form in a sheet; Escape closes it');
  // The Costs page once its data and chart are in (the loop above may look before they load).
  await phone.evaluate(() => document.querySelector('button[data-view="costs"]').click());
  await phone.waitForSelector('#cost-chart svg'); await phone.evaluate(() => { document.getElementById('cost-breakdown').open = true; }); await phone.waitForSelector('#cost-top tbody tr.linked');
  check(await phone.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
        && document.querySelector('#cost-chart svg').getBoundingClientRect().right <= innerWidth), 'phone: the Costs page and its chart fit the width');
  await phone.evaluate(() => document.querySelector('button[data-view="models"]').click());
  await phone.waitForSelector('#models-table tbody tr[data-model]');
  await phone.fill('#models-catalog-q', 'claude'); await phone.waitForSelector('#models-catalog .model-card');
  check(await phone.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
        && [...document.querySelectorAll('#models-table tr[data-model], #models-catalog .model-card')].every(e => e.getBoundingClientRect().right <= innerWidth))
        && await phone.$eval('.models-table thead', t => getComputedStyle(t).display === 'none'), 'phone: the Models page shows cards and fits the width');

  await phone.evaluate(() => { document.querySelector('button[data-view="websites"]').click(); });
  await phone.click('#ws-tabs button[data-tab="routes"]');
  await phone.waitForSelector('#rt-panel .rt-route');
  check(await phone.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
        && [...document.querySelectorAll('#rt-panel .rt-route, #rt-panel .rt-heading button')].every(e => e.getBoundingClientRect().right <= innerWidth)),
        'phone: Domains & routes fits the width');
  await phone.click('#rt-new'); await phone.waitForSelector('#rt-form-box:not([hidden]) #rt-sub');
  check(await phone.evaluate(() => [...document.querySelectorAll('#rt-form-box input, #rt-form-box select')].every(e => e.getBoundingClientRect().right <= innerWidth)),
        'phone: the route form fits the width');
  if (process.env.SHOTS) { await phone.evaluate(() => document.getElementById('rt-panel').scrollIntoView()); await phone.screenshot({ path: process.env.SHOTS + '/phone-routes.png', fullPage: false }); }
  await phone.click('#rt-form-box button:has-text("Cancel")');
  // Phone dialogs: nearly full width, buttons side by side at the bottom of the sheet; Escape cancels.
  await phone.evaluate(() => document.querySelector('button[data-view="policy"]').click());
  await phone.waitForSelector('#sandbox-reset:not([disabled])');
  await phone.click('#sandbox-reset');
  const pd = await openDialog(phone);
  const pbox = await pd.boundingBox(), pacts = await pd.$eval('.ui-dialog-actions', a => { const r = a.getBoundingClientRect(), b = [...a.children].map(x => x.getBoundingClientRect());
    return { bottom: Math.round(r.bottom), sameRow: b.length === 2 && Math.abs(b[0].top - b[1].top) < 2, widths: b.map(x => Math.round(x.width)) }; });
  check(pbox.width >= 0.9 * 390 && pbox.x >= 0 && pbox.x + pbox.width <= 390 && Math.abs(pacts.bottom - (pbox.y + pbox.height)) <= 2 && pacts.sameRow && pacts.widths.every(w => w >= 140),
        'phone dialog: ' + Math.round(pbox.width) + 'px of 390, buttons side by side at the bottom ' + JSON.stringify(pacts));
  if (process.env.SHOTS) await phone.screenshot({ path: process.env.SHOTS + '/dialog-danger-phone.png' });
  await cancelDialog(phone);
  check(/Last reset/.test(await phone.textContent('#sandbox-status')) && !/Resetting/.test(await phone.textContent('#sandbox-status')), 'phone: Escape cancels the reset');
  if (process.env.SHOTS) {
    const plist = await phone.evaluate(async () => (await (await fetch('/ui/api/projects')).json()).projects || []);
    const mon = plist.find(p => p.title === 'Monitoring dashboard'), wil = plist.find(p => p.title === 'Login form');  // finished: tasks can be sent back
    if (mon) { await phone.goto((process.env.BASE || 'http://127.0.0.1:8765') + '/ui/#project=' + mon.id); await phone.click('.project-facts button:has-text("Limits…")');
      await openDialog(phone); await phone.screenshot({ path: process.env.SHOTS + '/dialog-limits-phone.png' }); await cancelDialog(phone); }
    if (wil) { await phone.goto((process.env.BASE || 'http://127.0.0.1:8765') + '/ui/#project=' + wil.id); await phone.click('.ptabs button[data-ptab="plan"]'); await phone.click('.ptask button:has-text("Send back…") >> nth=0');
      await openDialog(phone); await phone.fill(DLG + ' textarea', 'Please rework the contact section.\n- F-1: the opening hours are wrong.\n- F-3: the map link is broken.');
      await phone.screenshot({ path: process.env.SHOTS + '/dialog-send-back-phone.png' }); await cancelDialog(phone); }
  }
  // Phone: the Infrastructure view fits the width.
  await phone.evaluate(() => document.querySelector('button[data-view="infrastructure"]').click());
  await phone.waitForSelector('#px-vms .px-vm[data-node="pve2"]', { timeout: 15000 });
  check(await phone.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
        && [...document.querySelectorAll('#px-vms .px-vm, #infra-overview .metric-tile')].every(e => e.getBoundingClientRect().right <= innerWidth + 1)),
        'phone: Infrastructure fits the width');
  await phone.click('#px-vms .px-vm[data-vmid="104"]');
  await phone.waitForSelector('#px-drawer:not([hidden])');
  check(await phone.evaluate(() => { const r = document.querySelector('#px-drawer .drawer-panel').getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1; }),
        'phone: the VM drawer opens as a sheet within the width');
  await phone.keyboard.press('Escape');
  if (process.env.SHOTS) await phone.screenshot({ path: process.env.SHOTS + '/phone-infrastructure.png' });
  // Installable: manifest with icons, service worker active, the app shell opens offline.
  const pwa = await phone.evaluate(async () => {
    const m = await (await fetch(document.querySelector('link[rel=manifest]').href)).json();
    const icons = await Promise.all(m.icons.map(i => fetch(i.src).then(r => r.ok)));
    const reg = await navigator.serviceWorker.ready;
    return { display: m.display, scope: m.scope, sizes: m.icons.map(i => i.sizes + ':' + i.purpose), iconsOk: icons.every(Boolean), active: !!reg.active };
  });
  check(pwa.display === 'standalone' && pwa.scope === '/ui/' && pwa.sizes.includes('512x512:maskable') && pwa.sizes.includes('192x192:any') && pwa.iconsOk, 'manifest is installable ' + JSON.stringify(pwa));
  check(pwa.active, 'service worker active');
  // Notifications: the bell is there on a phone; a notification link (/ui/#project=<id>) opens that project.
  await phone.click('#notif-btn'); await phone.waitForSelector('#notif-panel:not([hidden])');
  check(await phone.isVisible('#notif-btn') && await phone.isVisible('#notif-panel #push-toggle'), 'phone header has the notifications bell; its panel holds the push switch for this device');
  await phone.keyboard.press('Escape'); check(await phone.isHidden('#notif-panel'), 'Escape closes the notification panel');
  check(await phone.isVisible('#nav-inbox-badge'), 'phone tab bar shows the inbox count');
  const backup = await phone.evaluate(async () => (await (await fetch('/ui/api/threads?q=Backup')).json()).threads[0]);
  await phone.goto((process.env.BASE || 'http://127.0.0.1:8765') + '/ui/#thread=' + backup.id);
  await phone.waitForSelector('#thread-heading:has-text("Backup")', { timeout: 8000 });
  check(await phone.isVisible('#thread-messages .msg') && !(await phone.evaluate(() => location.hash)), 'a #thread= link opens that conversation');
  const proj = await phone.evaluate(async () => (await (await fetch('/ui/api/projects')).json()).projects?.[0] || null);
  if (proj) {
    await phone.goto((process.env.BASE || 'http://127.0.0.1:8765') + '/ui/#project=' + proj.id);
    await phone.waitForSelector('#project-main h2:has-text("' + proj.title.replace(/"/g, '') + '")', { timeout: 8000 });
    check(!(await phone.isHidden('#projects')) && !(await phone.evaluate(() => location.hash)), 'notification link opens its project');
  } else check(false, 'a seeded project for the link check');
  const apiCached = await phone.evaluate(async () => (await Promise.all((await caches.keys()).map(async k => (await (await caches.open(k)).keys()).map(r => new URL(r.url).pathname)))).flat());
  check(apiCached.includes('/ui/') && !apiCached.some(p => p.startsWith('/ui/api/')), 'service worker keeps the shell but never API data ' + JSON.stringify(apiCached));
  await phone.context().setOffline(true); await phone.reload(); await phone.waitForSelector('#login-error:has-text("Offline")', { timeout: 5000 });
  check(true, 'app shell opens offline and says so'); await phone.context().setOffline(false);

  // HLA_VOICE_V1: the Chief's car mode on a phone, with the browser's speech recognition and speech stubbed.
  const carCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await carCtx.addInitScript(() => {
    window.__said = []; window.__heard = 'Hoe staat het met de projecten';
    class FakeRecognition {
      start() { this.t = setTimeout(() => { const alt = [{ transcript: window.__heard }]; alt.isFinal = true; this.onresult && this.onresult({ resultIndex: 0, results: [alt] }); }, 200); }
      stop() { clearTimeout(this.t); this.onend && this.onend(); }
      abort() { clearTimeout(this.t); }
    }
    window.SpeechRecognition = FakeRecognition; window.webkitSpeechRecognition = FakeRecognition;
    const synth = { speaking: false, getVoices: () => [{ name: 'Test Vlaams', lang: 'nl-BE' }, { name: 'Test English', lang: 'en-US' }],
      speak(u) { if (u.text.trim()) window.__said.push(u.text); setTimeout(() => u.onend && u.onend(), 30); }, cancel() {}, pause() {}, resume() {}, addEventListener() {} };
    Object.defineProperty(window, 'speechSynthesis', { value: synth, configurable: true });
    window.SpeechSynthesisUtterance = function (t) { this.text = t; };
  });
  const car = await carCtx.newPage(); const carErrors = []; car.on('pageerror', e => carErrors.push(e.message)); nativeGuard(car);
  const posted = []; car.on('request', r => { if (r.method() === 'POST' && /\/ui\/api\/threads\/[^/]+\/messages$/.test(r.url())) posted.push(r.postData()); });
  await car.goto((process.env.BASE || 'http://127.0.0.1:8765') + '/ui/#chief');
  await car.fill('#password', 'pw'); await car.click('#login-form button[type=submit]');
  await car.waitForSelector('#chief:not([hidden])', { timeout: 8000 });
  await car.waitForSelector('#car-status:has-text("Ready")', { timeout: 8000 });
  const carLayout = await car.evaluate(() => {
    const vis = id => { const r = document.getElementById(id).getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.left >= 0 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1; };
    return { overflow: document.documentElement.scrollWidth > innerWidth || document.getElementById('chief').scrollWidth > document.getElementById('chief').clientWidth,
      controls: ['car-talk', 'car-handsfree', 'car-briefing', 'car-exit', 'car-settings-toggle'].every(vis) && document.getElementById('car-stop').hidden,
      talk: Math.round(document.getElementById('car-talk').getBoundingClientRect().width), hash: location.hash, tabbar: getComputedStyle(document.querySelector('aside')).zIndex };
  });
  const joined = await car.evaluate(() => {
    const res = list => list.map(([t, f]) => Object.assign([{ transcript: t }], { isFinal: f }));
    return [carJoinResults(res([['kan', true], ['kan je', true], ['kan je het', true], ['kan je het monitoring project', true], ['kan je het monitoring project terug verder zetten', false]])),
            carJoinResults(res([['kan je het', true], [' monitoring project', true], ['terug zetten', false]])),
            carJoinResults(res([['ja', true], ['ja', true]]))];
  });
  check(joined[0] === 'kan je het monitoring project terug verder zetten' && joined[1] === 'kan je het monitoring project terug zetten' && joined[2] === 'ja',
        'speech results: Android-style repeated phrases are not duplicated, separate pieces are joined ' + JSON.stringify(joined));
  check(!carLayout.overflow && carLayout.controls && carLayout.talk >= 56 && !carLayout.hash, 'the Chief opens from /ui/#chief at 390px: controls in view (Stop hidden while idle), a clear talk button, no sideways scroll ' + JSON.stringify(carLayout));
  // HLA_UI_V5: Car mode hides the shell (sidebar / tab bar, header, tabs) and has its own clear exit.
  await car.click('#car-focus');
  const focusTalk = await car.evaluate(() => { const shown = s => { const e = document.querySelector(s); if (!e) return false; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).display !== 'none'; };
    return [Math.round(document.getElementById('car-talk').getBoundingClientRect().width), document.documentElement.scrollWidth <= innerWidth + 1,
      !shown('aside.sidebar') && !shown('main > header.page-head') && !shown('#subnav'), shown('#car-focus-exit'), Math.round(document.getElementById('chief-orb').getBoundingClientRect().width)]; });
  await car.click('#car-focus-exit');
  check(focusTalk[0] >= 96 && focusTalk[1] && focusTalk[2] && focusTalk[3] && focusTalk[4] >= 120 && await car.evaluate(() => Math.round(document.getElementById('car-talk').getBoundingClientRect().width) < 96)
        && await car.isVisible('aside.sidebar'),
        'Car mode hides the shell chrome, enlarges the orb and controls, and "Exit car mode" brings everything back ' + JSON.stringify(focusTalk));
  await car.click('#car-talk');
  await car.waitForSelector('#car-status:has-text("Listening")', { timeout: 3000 });
  await car.waitForSelector('.car-you .car-text:has-text("Hoe staat het met de projecten")', { timeout: 8000 });
  await car.waitForSelector('.car-chief .car-text:has-text("Open Needs you om het goed te keuren")', { timeout: 15000 });
  await car.waitForSelector('#car-status:has-text("Ready")', { timeout: 8000 });
  const said = await car.evaluate(() => window.__said.join(' | '));
  check(posted.length === 1 && JSON.parse(posted[0]).voice === true && JSON.parse(posted[0]).content === 'Hoe staat het met de projecten', 'what was heard is sent as a voice turn ' + posted[0]);
  check(said.includes('Alles draait.') && said.includes('Open Needs you') && said.split(' | ').length >= 3 && !said.includes('**'), 'the reply is spoken sentence by sentence ' + said);
  check(await car.isVisible('#car-needs'), 'a reply that needs the owner shows "Open Needs you"');
  // Briefing: a Dutch request (nl-BE is the default language), answered and spoken.
  await car.evaluate(() => { window.__said = []; });
  await car.click('#car-briefing');
  await car.waitForSelector('.car-you .car-text:has-text("Geef me mijn briefing")', { timeout: 5000 });
  await car.waitForFunction(() => window.__said.some(s => s.includes('Alles draait')), null, { timeout: 15000 });
  check(JSON.parse(posted[1]).content.startsWith('Geef me mijn briefing: wat is er veranderd sinds') && JSON.parse(posted[1]).voice === true, 'Briefing asks what changed since the last visit ' + JSON.parse(posted[1]).content);
  await car.waitForSelector('#car-status:has-text("Ready")', { timeout: 8000 });
  // Barge in: tapping while he speaks stops speech and listens.
  const barge = await car.evaluate(() => { carSpeak('Een lange zin om te onderbreken.'); carSpeak('Nog een zin.');
    const before = CAR.state; document.getElementById('car-talk').click(); return [before, CAR.state, CAR.speakQueue.length]; });
  check(barge[0] === 'speaking' && barge[1] === 'listening' && barge[2] === 0, 'tapping while he speaks interrupts and listens ' + JSON.stringify(barge));
  await car.click('#car-stop');
  check(await car.evaluate(() => CAR.state === 'idle' && !CAR.handsFree), '■ Stop ends listening');
  // Hands-free: on starts listening; Stop turns it off.
  await car.click('#car-handsfree');
  check(await car.getAttribute('#car-handsfree', 'aria-pressed') === 'true' && await car.evaluate(() => CAR.state === 'listening'), 'hands-free on: listening at once');
  await car.click('#car-stop');
  check(await car.getAttribute('#car-handsfree', 'aria-pressed') === 'false', 'Stop turns hands-free off');
  await car.waitForTimeout(400); posted.length = 2;
  // Settings: the model picker changes the Chief's model; language and voice are remembered.
  await car.click('#car-settings-toggle');
  check(await car.isVisible('#voice-drawer #car-settings') && await car.evaluate(() => !!document.querySelector('#voice-drawer:not([hidden])') && document.querySelector('main').inert) && await car.$$eval('#car-model option', o => o.length) >= 2 && await car.$$eval('#car-voice option', o => o.map(x => x.textContent).join()).then(t => t.includes('Test Vlaams') && !t.includes('Test English')),
        'settings: model choices and voices for the chosen language');
  await car.selectOption('#car-model', 'anthropic/claude-sonnet-5.5');
  await car.waitForFunction(async () => (await (await fetch('/ui/api/chief')).json()).model === 'anthropic/claude-sonnet-5.5', null, { timeout: 5000 });
  await car.selectOption('#car-lang', 'en-US');
  check(await car.$$eval('#car-voice option', o => o.map(x => x.textContent).join()).then(t => t.includes('Test English') && !t.includes('Test Vlaams'))
        && await car.evaluate(() => localStorage.getItem('hla.chief.lang')) === 'en-US', 'language switch lists its voices and is remembered; model saved');
  await car.click('#car-settings-close');
  check(await car.isHidden('#voice-drawer') && await car.evaluate(() => document.activeElement && document.activeElement.id === 'car-settings-toggle'), 'voice settings: Done closes the drawer and focus returns to the settings button');
  await car.click('#car-settings-toggle'); await car.waitForSelector('#voice-drawer:not([hidden])');
  await car.keyboard.press('Escape');
  check(await car.isHidden('#voice-drawer') && await car.getAttribute('#car-settings-toggle', 'aria-expanded') === 'false', 'voice settings: Escape closes the drawer');
  // HLA_CHIEF_LATENCY_V1: speech output without gaps.
  // Browser voices: every piece is handed to speechSynthesis at once (no wait for onend between utterances).
  const browserQueue = await car.evaluate(async () => {
    carStopSpeaking(); const calls = []; const orig = speechSynthesis.speak;
    speechSynthesis.speak = u => { calls.push([u.text, performance.now()]); setTimeout(() => u.onend && u.onend(), 200); };
    carSpeak('First part of the answer, with a clause. Second sentence here. Third one.');
    const queuedAt = calls.map(c => c[1]); const n = calls.length;
    await new Promise(r => setTimeout(r, 700)); speechSynthesis.speak = orig;
    return { n, spread: queuedAt.length ? queuedAt[queuedAt.length - 1] - queuedAt[0] : -1, idle: CAR.state };
  });
  check(browserQueue.n >= 2 && browserQueue.spread < 50, 'browser speech: all pieces are queued at once, no wait between utterances ' + JSON.stringify(browserQueue));
  // Server voices (Kokoro): a slow /voice/speak (800 ms per request), clips play 900 ms each.
  const speakLog = []; let inFlight = 0, maxInFlight = 0;
  await car.route('**/ui/api/voice/speak', async route => {
    const text = JSON.parse(route.request().postData()).text; inFlight++; maxInFlight = Math.max(maxInFlight, inFlight);
    speakLog.push(['start', text, Date.now()]);
    await new Promise(r => setTimeout(r, 800));
    inFlight--; speakLog.push(['end', text, Date.now()]);
    await route.fulfill({ status: 200, contentType: 'audio/mpeg', body: Buffer.from('ID3fake-' + text) }).catch(() => {});
  });
  const served = await car.evaluate(async () => {
    window.__clips = [];
    window.Audio = class { constructor() { this.src = ''; } load() {}
      play() { window.__clips.push(['play', this.src, performance.now()]); setTimeout(() => { window.__clips.push(['ended', this.src, performance.now()]); this.onended && this.onended(); }, 900); return Promise.resolve(); }
      pause() { this.paused = true; } };
    CAR.config.server_tts = true; store.set('chief.tts', 'server'); carStopSpeaking();
    const text = 'Okay. Here is the update. Three projects are running and one needs your approval on the dashboard. '
      + 'The website check found nothing down, and costs today are about two dollars. Want details?';
    // As carFollow hands them over: one finished sentence at a time while the reply streams in.
    const t0 = performance.now(); for (const sentence of text.match(/[^.?!]+[.?!]/g)) carSpeak(sentence.trim()); CAR.replyDone = true; carAfterReply();
    await new Promise(r => { const t = setInterval(() => { if (!CAR.speaking && !(CAR.clips || []).length) { clearInterval(t); r(); } }, 50); setTimeout(() => { clearInterval(t); r(); }, 12000); });
    const plays = __clips.filter(c => c[0] === 'play'), ends = __clips.filter(c => c[0] === 'ended');
    const gaps = plays.slice(1).map((p, i) => Math.round(p[2] - ends[i][2]));
    return { plays: plays.length, gaps, firstPlay: Math.round(plays[0][2] - t0) };
  });
  const starts = speakLog.filter(e => e[0] === 'start').map(e => e[1]);
  check(maxInFlight >= 2 && starts.length >= 3 && starts.length <= 5 && served.plays === starts.length,
        `server speech: requests run ahead of playback (max ${maxInFlight} at once, ${starts.length} requests for 6 sentences: short ones merged)`);
  check(served.gaps.every(g => g < 60), 'server speech: the next clip plays the moment the previous one ends ' + JSON.stringify(served));
  // Barge-in aborts what is in flight and clears the queue.
  const barged = await car.evaluate(async () => {
    carSpeak('A long sentence that will be interrupted right away, to see that nothing else plays. And another sentence behind it, also long enough.');
    await new Promise(r => setTimeout(r, 100)); const plays = __clips.length;
    document.getElementById('car-talk').click();
    await new Promise(r => setTimeout(r, 1500));
    return { queue: (CAR.clips || []).length, newPlays: __clips.filter(c => c[0] === 'play').length - __clips.slice(0, plays).filter(c => c[0] === 'play').length, state: CAR.state };
  });
  check(barged.queue === 0 && barged.newPlays === 0 && barged.state === 'listening', 'server speech: barge-in aborts the requests and clears the queue ' + JSON.stringify(barged));
  await car.click('#car-stop');
  await car.unroute('**/ui/api/voice/speak');
  await car.evaluate(() => { store.set('chief.tts', 'browser'); CAR.config.server_tts = false; });
  // The acknowledgement: spoken only when no reply text arrived within 1.2 s.
  const ack = await car.evaluate(async () => {
    window.__said = []; CAR.state = 'thinking'; const seq = ++CAR.turnSeq; carAckLater(seq);
    await new Promise(r => setTimeout(r, 1400)); const silent = window.__said.slice();
    window.__said = []; const seq2 = ++CAR.turnSeq; carAckLater(seq2); CAR.gotText = true;
    await new Promise(r => setTimeout(r, 1400)); carStopSpeaking(); CAR.state = 'idle';
    return { slow: silent, fast: window.__said };
  });
  check(ack.slow.length === 1 && /One moment|Momentje/.test(ack.slow[0]) && ack.fast.length === 0, 'a short acknowledgement only when the reply is slow ' + JSON.stringify(ack));
  const carNoOverflow = await car.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  await car.click('#car-exit');
  await car.waitForSelector('#overview:not([hidden])');
  check(carNoOverflow && await car.isHidden('#chief') && await car.evaluate(() => CAR.state === 'idle'), 'leaving car mode returns to Overview and stops listening');
  check(carErrors.length === 0, 'car mode: no page errors ' + JSON.stringify(carErrors));
  await carCtx.close();

  check(errors.length === 0, 'no page errors ' + JSON.stringify(errors));
  check(consoleErrors.length === 0, 'no console errors ' + JSON.stringify(consoleErrors.slice(0, 5)));
  console.log(failures ? `\n${failures} FAILED` : '\nALL PASSED');
  await browser.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
