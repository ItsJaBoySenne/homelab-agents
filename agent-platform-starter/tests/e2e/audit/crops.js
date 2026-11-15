// UI audit, part 4: close-up screenshots of specific regions (policy at phone width, deploy history, legend).
const { chromium } = require('playwright');
const out = require('path').join(__dirname, 'shots');
const base = process.env.BASE || 'http://127.0.0.1:8771';
(async () => {
  const b = await chromium.launch();
  for (const [w, h] of [[390, 844], [768, 1024]]) {
    const p = await b.newPage({ viewport: { width: w, height: h } });
    await p.goto(base + '/ui/'); await p.fill('#password', 'pw'); await p.click('#login-form button[type=submit]');
    await p.waitForSelector('#workspace:not([hidden])'); await p.click('button[data-view="policy"]'); await p.waitForTimeout(1500);
    await (await p.$('.deploy-panel')).screenshot({ path: `${out}/c${w}-deploy-panel.png` });
    await (await p.$('#level-legend')).screenshot({ path: `${out}/c${w}-legend.png` });
    const sw = await p.$eval('.deploy-table', t => [t.scrollWidth, t.parentElement.clientWidth]);
    console.log(w, 'deploy table scrollWidth vs wrap', sw);
    await p.close();
  }
  await b.close();
})();
