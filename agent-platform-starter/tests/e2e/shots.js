// Screenshot every dashboard view at desktop, laptop and phone widths.
const { chromium } = require('playwright');
const out = process.argv[2] || 'shots';
const base = process.env.BASE || 'http://127.0.0.1:8765';
const only = process.env.ONLY ? process.env.ONLY.split(',') : null;
const sizes = { desktop: [1440, 900], laptop: [1100, 760], phone: [390, 844] };
(async () => {
  const browser = await chromium.launch();
  for (const [name, [width, height]] of Object.entries(sizes)) {
    if (only && !only.includes(name)) continue;
    const page = await browser.newPage({ viewport: { width, height } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    await page.goto(base + '/ui/');
    await page.screenshot({ path: `${out}/${name}-login.png` });
    await page.fill('#password', 'pw'); await page.click('#login-form button[type=submit]');
    await page.waitForTimeout(700);
    for (const view of ['overview', 'tasks', 'threads', 'knowledge', 'queue', 'agents']) {
      await page.evaluate(v => document.querySelector(`button[data-view="${v}"]`).click(), view);
      await page.waitForTimeout(500);
      if (view === 'threads') {
        if (name === 'phone') await page.screenshot({ path: `${out}/${name}-threads-list.png`, fullPage: true });
        const item = await page.$('.thread-item:has-text("Fileserver")');
        if (item) { await item.click(); await page.waitForTimeout(800); }
      }
      await page.screenshot({ path: `${out}/${name}-${view}.png`, fullPage: true });
      if (view === 'tasks') {
        const row = await page.$('#task-rows tr');
        if (row) { await row.click(); await page.waitForTimeout(600); await page.screenshot({ path: `${out}/${name}-task-detail.png` });
          await page.keyboard.press('Escape'); await page.waitForTimeout(200); }
      }
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    console.log(name, 'horizontal overflow px:', overflow, 'errors:', JSON.stringify(errors));
    await page.close();
  }
  await browser.close();
})();
