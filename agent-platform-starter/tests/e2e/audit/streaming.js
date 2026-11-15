// UI audit, part 3: conversations. New conversation, live/streamed reply timeline, scroll-up during streaming,
// queued follow-up, Stop, command approval and read-only approval cards. Mutates state: restart the server to re-run.
// Usage: BASE=http://127.0.0.1:8771 W=1440 H=900 node tests/e2e/audit/streaming.js
const { chromium } = require('playwright');
const path = require('path');
const out = path.join(__dirname, 'shots');
const base = process.env.BASE || 'http://127.0.0.1:8771';
const W = Number(process.env.W || 1440), H = Number(process.env.H || 900), tag = 's' + W;
const obs = (k, v) => console.log('OBS ' + k + ': ' + (typeof v === 'string' ? v : JSON.stringify(v)));
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: W, height: H } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  page.on('response', r => { if (r.status() >= 400) errors.push('HTTP ' + r.status() + ' ' + r.request().method() + ' ' + r.url()); });
  page.on('dialog', d => d.accept());
  await page.goto(base + '/ui/');
  await page.fill('#password', 'pw'); await page.click('#login-form button[type=submit]');
  await page.waitForSelector('#workspace:not([hidden])'); await sleep(500);
  await page.click('button[data-view="threads"]'); await sleep(1000);
  obs('composer state with no conversation selected (phone)', await page.evaluate(() => ({ send: document.getElementById('thread-send').disabled })));

  // New conversation
  await page.click('#thread-new-toggle'); await sleep(200);
  await page.screenshot({ path: `${out}/${tag}-new-thread-form.png` });
  await page.click('#thread-create button[type=submit]'); await sleep(200);
  obs('new thread empty title validation', await page.evaluate(() => document.querySelector('#thread-title').validationMessage));
  await page.fill('#thread-title', 'Audit run');
  obs('new thread agent options (home)', await page.$$eval('#thread-agent option', o => o.map(x => x.textContent)));
  await page.selectOption('#thread-agent', { label: 'Infra Agent' });
  await page.click('#thread-create button[type=submit]'); await sleep(900);
  obs('after create: heading / focus', [await page.textContent('#thread-heading'), await page.evaluate(() => document.activeElement.id)]);
  await page.screenshot({ path: `${out}/${tag}-new-thread-empty.png` });

  // Live timeline
  const snap = async () => page.evaluate(() => {
    const box = document.getElementById('thread-messages'), live = box.querySelector('.msg.live');
    const msgs = [...box.querySelectorAll('.msg')].map(m => (m.classList.contains('live') ? 'LIVE' : m.classList.contains('msg-user') ? 'U' : 'A'));
    return {
      msgs: msgs.join(','), label: live && live.querySelector('.live-label')?.textContent, typing: !!(live && live.querySelector('.typing')),
      steps: live && live.querySelector('.steps summary')?.textContent, stepItems: live ? [...live.querySelectorAll('.steps li')].map(l => l.textContent) : null,
      textLen: live ? (live.querySelector('.msg-body')?.textContent.length || 0) : null,
      distFromBottom: Math.round(box.scrollHeight - box.scrollTop - box.clientHeight), send: document.getElementById('thread-send').textContent + (document.getElementById('thread-send').disabled ? '(disabled)' : ''),
      stop: !document.getElementById('thread-stop').hidden, state: document.getElementById('thread-state').textContent, input: document.getElementById('thread-input').value,
      listBadge: document.querySelector('.thread-item.active .badge')?.textContent || null,
    };
  });
  await page.fill('#thread-input', 'What user does the file manager on fs-01 run as?');
  const t0 = Date.now(); await page.press('#thread-input', 'Enter');
  const timeline = [];
  for (let i = 0; i < 28; i++) {
    const s = await snap(); timeline.push(((Date.now() - t0) / 1000).toFixed(1) + 's ' + JSON.stringify(s));
    if ([1, 4, 8, 12, 16, 20].includes(i)) await page.screenshot({ path: `${out}/${tag}-live-${String(i).padStart(2, '0')}.png` });
    await sleep(250);
  }
  timeline.forEach(l => console.log('TL ' + l));
  await page.screenshot({ path: `${out}/${tag}-live-done.png` });

  // Scroll up while streaming
  await page.fill('#thread-input', 'And which group owns /srv/files/shared?');
  await page.press('#thread-input', 'Enter');
  await page.waitForSelector('.msg.live'); await sleep(300);
  await page.evaluate(() => { document.getElementById('thread-messages').scrollTop = 0; });
  const scrollTrail = [];
  for (let i = 0; i < 24; i++) { await sleep(250); scrollTrail.push(await page.evaluate(() => { const b = document.getElementById('thread-messages'); return Math.round(b.scrollTop) + (b.querySelector('.msg.live') ? 'L' : ''); })); }
  obs('scrollTop while user scrolled to top during streaming (L=live bubble present)', scrollTrail.join(' '));
  obs('after finish while scrolled up: visible "jump to latest" control?', await page.evaluate(() => { const j = document.querySelector('#thread-jump, .jump-latest'); return j ? { exists: true, hidden: j.hidden, visible: !!(j.offsetWidth || j.offsetHeight) } : { exists: false }; }));
  await page.screenshot({ path: `${out}/${tag}-scrolled-up-after-stream.png` });

  // Queued follow-up and Stop
  await page.evaluate(() => { const b = document.getElementById('thread-messages'); b.scrollTop = b.scrollHeight; });
  await page.fill('#thread-input', 'Check the disk usage on dev too.');
  await page.press('#thread-input', 'Enter');
  await page.waitForSelector('.msg.live'); await sleep(400);
  await page.fill('#thread-input', 'Also list the ACLs.');
  await page.press('#thread-input', 'Enter'); await sleep(800);
  obs('queued: state', await snap());
  await page.screenshot({ path: `${out}/${tag}-queued.png` });
  await page.fill('#thread-input', 'A third message while one is queued');
  obs('third message while queued: send disabled? input kept?', await page.evaluate(() => [document.getElementById('thread-send').disabled, document.getElementById('thread-send').title]));
  await page.press('#thread-input', 'Enter'); await sleep(300);
  obs('third message after Enter: input value kept / notice', [await page.inputValue('#thread-input'), await page.textContent('#notice')]);
  await page.fill('#thread-input', '');
  await page.click('#thread-stop'); const ts = Date.now();
  const stopTrail = [];
  for (let i = 0; i < 16; i++) { await sleep(400); const s = await snap(); stopTrail.push(((Date.now() - ts) / 1000).toFixed(1) + 's ' + s.msgs + ' label=' + s.label + ' stop=' + s.stop + ' send=' + s.send + ' state=' + s.state); }
  obs('stop timeline', stopTrail);
  obs('notice after stop', await page.textContent('#notice'));
  await page.screenshot({ path: `${out}/${tag}-after-stop.png` });
  await sleep(3000);
  obs('last messages after stop', await page.$$eval('#thread-messages .msg', m => m.slice(-4).map(x => x.className + ' :: ' + x.textContent.slice(0, 90))));

  // Command approval card
  await page.click('.thread-item:has-text("Shared folder ACL")'); await page.waitForSelector('.command-card'); await sleep(300);
  await page.screenshot({ path: `${out}/${tag}-command-card.png` });
  const tc = Date.now(); await page.click('.command-card button:has-text("Approve & run once")');
  const ctrail = [];
  for (let i = 0; i < 20; i++) { await sleep(400); const s = await snap(); ctrail.push(((Date.now() - tc) / 1000).toFixed(1) + 's ' + s.msgs + ' label=' + s.label + ' card=' + (await page.textContent('.command-card .pill')) + ' stop=' + s.stop); }
  obs('command approve timeline', ctrail);
  await page.screenshot({ path: `${out}/${tag}-command-done.png` });

  // Read-only approval card
  await page.click('.thread-item:has-text("Fileserver")'); await page.waitForSelector('.approval-card'); await sleep(400);
  obs('opening conversation with pending approval: card fully in view?', await page.evaluate(() => { const c = document.querySelector('.approval-human_approval_required'), b = document.getElementById('thread-messages'); const r = c.getBoundingClientRect(), br = b.getBoundingClientRect(); return { cardTop: Math.round(r.top), cardBottom: Math.round(r.bottom), boxTop: Math.round(br.top), boxBottom: Math.round(br.bottom) }; }));
  const ta = Date.now(); await page.click('.approval-card button:has-text("Approve read-only")');
  const atrail = [];
  for (let i = 0; i < 20; i++) { await sleep(400); const s = await snap(); atrail.push(((Date.now() - ta) / 1000).toFixed(1) + 's ' + s.msgs + ' label=' + s.label + ' steps=' + s.steps + ' textLen=' + s.textLen + ' card=' + (await page.textContent('.approval-card .pill'))); }
  obs('read-only approve timeline', atrail);
  await page.screenshot({ path: `${out}/${tag}-approval-done.png` });

  // Composer while a conversation is switched mid-run: start a run then switch
  await page.click('.thread-item:has-text("Backup")'); await sleep(600);
  await page.fill('#thread-input', 'status?'); await page.press('#thread-input', 'Enter'); await sleep(500);
  await page.click('.thread-item:has-text("Deploy monitoring")'); await sleep(600);
  obs('switched away mid-run: composer state in other thread', await snap());
  await page.click('.thread-item:has-text("Backup")'); await sleep(600);
  obs('switched back mid-run', await snap());
  if (W <= 900) { await page.click('#thread-back'); await sleep(300); obs('phone back: list shown', await page.isVisible('#thread-list')); }
  await sleep(4000);
  console.log('ERRORS ' + JSON.stringify([...new Set(errors)], null, 1));
  await browser.close();
})();
