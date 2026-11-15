// UI audit, part 1: every view at four widths. Screenshots + layout/a11y metrics + console/network errors.
// Usage: BASE=http://127.0.0.1:8771 node tests/e2e/audit/layout.js [outdir]
const { chromium } = require('playwright');
const path = require('path');
const out = process.argv[2] || path.join(__dirname, 'shots');
const base = process.env.BASE || 'http://127.0.0.1:8771';
const sizes = { w1440: [1440, 900], w1100: [1100, 760], w768: [768, 1024], w390: [390, 844] };
const views = ['overview', 'tasks', 'threads', 'knowledge', 'queue', 'agents', 'policy', 'infrastructure'];

function probe() {
  const vw = document.documentElement.clientWidth;
  const visible = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const desc = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).join('.') : '');
  const res = { pageOverflow: document.documentElement.scrollWidth - vw, offscreen: [], clipped: [], noName: [], noLabel: [], tinyTargets: [], narrowCells: [] };
  for (const e of document.querySelectorAll('main *, aside *')) {
    if (!visible(e)) continue;
    const r = e.getBoundingClientRect();
    // Skip things inside horizontal scrollers (they are allowed to be wider).
    let p = e.parentElement, inScroller = false;
    while (p && p !== document.body) { const s = getComputedStyle(p); if (/(auto|scroll)/.test(s.overflowX) && p.scrollWidth > p.clientWidth) { inScroller = true; break; } p = p.parentElement; }
    if (!inScroller && (r.right > vw + 1 || r.left < -1) && getComputedStyle(e).position !== 'fixed') res.offscreen.push(desc(e) + ` [${Math.round(r.left)}..${Math.round(r.right)}]`);
    const s = getComputedStyle(e);
    if (['hidden', 'clip'].includes(s.overflowX) && e.scrollWidth > e.clientWidth + 2 && s.textOverflow !== 'ellipsis' && e.children.length === 0) res.clipped.push(desc(e) + ' "' + e.textContent.trim().slice(0, 40) + '"');
    if (e.matches('button, a[href], [role=button], select, input, textarea')) {
      const name = (e.getAttribute('aria-label') || e.getAttribute('title') || (e.labels && [...e.labels].map(l => l.textContent).join(' ')) || e.textContent || e.placeholder || '').trim();
      if (e.matches('input, select, textarea') && !(e.labels && e.labels.length) && !e.getAttribute('aria-label') && e.type !== 'hidden') res.noLabel.push(desc(e) + (e.placeholder ? ' (placeholder only: ' + e.placeholder + ')' : ''));
      else if (!name) res.noName.push(desc(e));
      if (vw <= 768 && (r.height < 32 || r.width < 32)) res.tinyTargets.push(desc(e) + ` ${Math.round(r.width)}x${Math.round(r.height)} "${(e.textContent || '').trim().slice(0, 20)}"`);
    }
    if (e.matches('td, th') && r.width < 60 && e.textContent.trim().length > 8) res.narrowCells.push(desc(e) + ` w=${Math.round(r.width)} "${e.textContent.trim().slice(0, 30)}"`);
  }
  for (const k of Object.keys(res)) if (Array.isArray(res[k])) res[k] = [...new Set(res[k])].slice(0, 25);
  return res;
}

(async () => {
  const browser = await chromium.launch();
  const report = {};
  for (const [name, [width, height]] of Object.entries(sizes)) {
    const page = await browser.newPage({ viewport: { width, height } });
    const errors = [];
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));
    page.on('console', m => { if (['error', 'warning'].includes(m.type())) errors.push('console.' + m.type() + ': ' + m.text()); });
    page.on('requestfailed', r => { if (!/events$/.test(r.url())) errors.push('requestfailed: ' + r.url() + ' ' + (r.failure() || {}).errorText); });
    page.on('response', r => { if (r.status() >= 400) errors.push('HTTP ' + r.status() + ' ' + r.request().method() + ' ' + r.url()); });
    page.on('dialog', d => d.dismiss());
    await page.goto(base + '/ui/');
    await page.screenshot({ path: `${out}/${name}-login.png` });
    report[name] = { login: await page.evaluate(probe) };
    await page.fill('#password', 'pw'); await page.click('#login-form button[type=submit]');
    await page.waitForSelector('#workspace:not([hidden])'); await page.waitForTimeout(800);
    for (const view of views) {
      await page.click(`button[data-view="${view}"]`);
      await page.waitForTimeout(1200);
      if (view === 'threads' && width <= 900) {
        await page.screenshot({ path: `${out}/${name}-threads-list.png`, fullPage: true });
        report[name]['threads-list'] = await page.evaluate(probe);
        const item = await page.$('.thread-item:has-text("Fileserver")'); if (item) { await item.click(); await page.waitForTimeout(1000); }
      }
      await page.screenshot({ path: `${out}/${name}-${view}.png`, fullPage: true });
      await page.screenshot({ path: `${out}/${name}-${view}-fold.png` });
      report[name][view] = await page.evaluate(probe);
      if (view === 'tasks') {
        await page.click('#task-rows tr'); await page.waitForTimeout(800);
        await page.screenshot({ path: `${out}/${name}-task-drawer.png` });
        report[name]['task-drawer'] = await page.evaluate(probe);
        await page.keyboard.press('Escape'); await page.waitForTimeout(200);
      }
    }
    report[name].errors = [...new Set(errors)];
    await page.close();
  }
  await browser.close();
  console.log(JSON.stringify(report, null, 1));
})();
