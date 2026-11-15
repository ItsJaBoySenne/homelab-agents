// Dashboard rendering cost with the scaled dataset, in headless Chromium.
//   BASE=http://127.0.0.1:8772 node tests/perf/perf_ui.js [--trace trace.json]
// Measures (wall time from click to DOM ready, plus long tasks > 50 ms recorded by PerformanceObserver):
//   login -> overview, Tasks view, Conversations view (opens the newest conversation), the largest conversation,
//   Knowledge view (notes + facts), a re-render of the largest conversation (what every finished run triggers),
//   and what keeps running in the background after leaving the Conversations view during a run.
const { chromium } = require('playwright');
const base = process.env.BASE || 'http://127.0.0.1:8772';
const traceIdx = process.argv.indexOf('--trace');

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const requests = [];
  page.on('request', r => { if (r.url().includes('/ui/api/')) requests.push({ t: Date.now(), url: r.url().replace(base, '') }); });
  await page.addInitScript(() => {
    window.__long = [];
    new PerformanceObserver(l => { for (const e of l.getEntries()) window.__long.push({ start: e.startTime, dur: e.duration }); })
      .observe({ type: 'longtask', buffered: true });
  });
  const results = [];
  async function measure(name, action, ready) {
    await page.evaluate(() => { window.__long = []; });
    const t0 = Date.now();
    await action();
    await page.waitForFunction(ready, null, { timeout: 120000, polling: 20 });
    // let pending renders finish so their long tasks are counted
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => setTimeout(r, 50))));
    const wall = Date.now() - t0;
    const long = await page.evaluate(() => window.__long);
    const nodes = await page.evaluate(() => document.getElementsByTagName('*').length);
    const r = { step: name, wall_ms: wall, long_tasks: long.length, longest_ms: Math.round(Math.max(0, ...long.map(x => x.dur))),
                long_total_ms: Math.round(long.reduce((a, x) => a + x.dur, 0)), dom_nodes: nodes };
    results.push(r); console.log(JSON.stringify(r));
  }
  await page.goto(base + '/ui/');
  await page.fill('#password', 'pw');
  await measure('login -> overview stats', () => page.click('#login-form button[type=submit]'),
    () => document.getElementById('m-tasks').textContent.replace(/\D/g, '') > 1000);
  await page.waitForTimeout(1500);
  await measure('Tasks view (25 rows)', () => page.click('button[data-view="tasks"]'),
    () => document.querySelectorAll('#task-rows tr').length >= 25);
  await measure('Tasks next page', () => page.click('#task-next'),
    () => (document.getElementById('task-page').textContent || '').startsWith('26'));
  await measure('Tasks search q=nginx', () => page.fill('#task-search', 'nginx'),
    () => document.getElementById('task-page').textContent.startsWith('1') && document.getElementById('task-search').value === 'nginx');
  await measure('Conversations view (list + newest thread)', () => page.click('button[data-view="threads"]'),
    () => document.querySelectorAll('.thread-item').length >= 50 && document.querySelectorAll('#thread-messages .msg').length > 0);
  const largestTitle = await page.evaluate(() => {
    // the seeded largest conversation is "Conversation 0: ..."
    const b = [...document.querySelectorAll('.thread-item')].find(x => x.textContent.includes('Conversation 0:'));
    return b ? b.querySelector('.thread-item-title').textContent : null;
  });
  if (largestTitle) {
    await page.fill('#thread-filter', 'Conversation 0:');
    // Since the perf fixes a conversation opens with its newest 100 messages ("Show earlier" loads more).
    await measure('open largest conversation (500 msgs, newest 100 shown)', () => page.click('.thread-item'),
      () => document.querySelectorAll('#thread-messages .msg').length >= 100 && document.querySelector('.show-earlier'));
    await measure('re-render largest conversation (refresh button)', () => page.click('#refresh'),
      () => document.getElementById('notice').textContent === 'Refreshed');
    // Background work after leaving the view: send a message, switch to Knowledge, watch requests for 15 s.
    const tSend = Date.now();
    await measure('Knowledge view (notes + facts)', async () => {
      await page.fill('#thread-input', 'Check nginx status on the monitoring servers.');
      await page.click('#thread-send');
      await page.waitForTimeout(300);
      await page.click('button[data-view="knowledge"]');
    }, () => document.querySelectorAll('#kb-list .kb-entry').length >= 100 && document.querySelectorAll('#fact-list .fact').length >= 25);
    const t = Date.now(); await page.evaluate(() => { window.__long = []; });
    await page.waitForTimeout(15000);
    const bg = requests.filter(r => r.t >= tSend).map(r => r.url.split('?')[0].replace(/[0-9a-f-]{36}/g, ':id'));
    const counts = {}; for (const u of bg) counts[u] = (counts[u] || 0) + 1;
    const long = await page.evaluate(() => window.__long);
    const r = { step: 'requests from Send until 15 s after switching to Knowledge (Conversations view hidden)', requests: counts,
                long_tasks: long.length, long_total_ms: Math.round(long.reduce((a, x) => a + x.dur, 0)),
                thread_dom_nodes: await page.evaluate(() => document.querySelectorAll('#thread-messages *').length) };
    results.push(r); console.log(JSON.stringify(r));
  }
  if (traceIdx > 0) {
    await browser.startTracing(page, { path: process.argv[traceIdx + 1], categories: ['devtools.timeline'] });
    await page.click('button[data-view="threads"]');
    await page.waitForTimeout(4000);
    await browser.stopTracing();
  }
  await browser.close();
})();
