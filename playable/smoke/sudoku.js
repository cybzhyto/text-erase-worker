// Copied from the caller's repository (scripts/playables/kits/board29/smoke-sudoku.js) by its sync script; edit there, then sync.
// node smoke-sudoku.js FILE [SHOTS_DIR] [views] — plays the «sudoku» playable on 5 screens: a bad line (two tops) is
// refused, a line through the first column, a repeat of the same outfit refused, picks one by one, a diagonal, end, CTA once.
const { chromium } = require('playwright');
const fs = require('fs');
const FILE = process.argv[2], SHOTS = process.argv[3];
const html = fs.readFileSync(FILE, 'utf8');
const CFG = JSON.parse(html.match(/<script type="application\/json" id="cfg">([\s\S]*?)<\/script>/)[1]);
const MRAID = `(() => { let state='loading'; const L={}; window.__opened=[]; window.__viewable=true;
 window.mraid={getVersion:()=> '2.0',getState:()=>state,addEventListener:(n,f)=>{(L[n]=L[n]||[]).push(f)},removeEventListener(){},
 isViewable:()=>window.__viewable,open:(u)=>window.__opened.push(u),getPlacementType:()=> 'interstitial',useCustomClose(){}};
 window.__fire=(n,a)=>(L[n]||[]).forEach(f=>f(a)); setTimeout(()=>{state='default';window.__fire('ready')},400); })();`;
const VIEWS = [{ n: 'portrait', w: 390, h: 844 }, { n: 'landscape', w: 844, h: 390 }, { n: 'small', w: 360, h: 640 }, { n: 'tablet', w: 768, h: 1024 }, { n: 'tall', w: 375, h: 812 }]
  .filter((v) => !process.argv[4] || process.argv[4].split(',').includes(v.n));
const hit = (a, b) => a.l < b.r - 1 && b.l < a.r - 1 && a.t < b.b - 1 && b.t < a.b - 1;
const off = (a, v) => a.l < -1 || a.t < -1 || a.r > v.w + 1 || a.b > v.h + 1;
(async () => {
  const b = await chromium.launch();
  const fails = [];
  for (const v of VIEWS) {
    const ctx = await b.newContext({ viewport: { width: v.w, height: v.h }, deviceScaleFactor: 1, hasTouch: true, isMobile: v.n !== 'landscape' });
    const p = await ctx.newPage();
    const errs = [], reqs = [];
    p.on('pageerror', (e) => errs.push(String(e)));
    p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
    await p.route('**/*', (r) => { const u = r.request().url(); if (u === 'https://playable.test/ad.html') return r.fulfill({ body: html, contentType: 'text/html' }); reqs.push(u); return r.abort(); });
    await p.addInitScript(MRAID);
    await p.goto('https://playable.test/ad.html');
    await p.waitForTimeout(2600);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-0-hint.png` });
    const geo = await p.evaluate(() => {
      const r = (e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; };
      const t = document.getElementById('hTitle');
      return { head: r(document.getElementById('head')), grid: r(document.getElementById('grid')), disc: r(document.getElementById('disc')),
        cells: [...document.querySelectorAll('.cell')].map(r), looks: [...document.querySelectorAll('.look')].map(r), hint: r(document.getElementById('hint')),
        clip: t.scrollWidth > t.clientWidth + 1 || t.scrollHeight > t.clientHeight + 2 };
    });
    if (geo.clip) fails.push(`${v.n}: title clipped`);
    if (hit(geo.head, geo.grid)) fails.push(`${v.n}: hook over the grid`);
    geo.cells.forEach((c, i) => { if (off(c, v)) fails.push(`${v.n}: cell ${i + 1} off screen`); if (Math.min(c.r - c.l, c.b - c.t) < 44) fails.push(`${v.n}: cell ${i + 1} under 44 px`); });
    geo.looks.forEach((l, i) => { if (off(l, v)) fails.push(`${v.n}: look ${i + 1} off screen`); if (hit(l, geo.grid)) fails.push(`${v.n}: look ${i + 1} over the grid`); if (hit(l, geo.disc)) fails.push(`${v.n}: look ${i + 1} over the disclaimer`); });
    if (hit(geo.hint, geo.disc)) fails.push(`${v.n}: hint over the disclaimer`);
    const c = (i) => ({ x: (geo.cells[i].l + geo.cells[i].r) / 2, y: (geo.cells[i].t + geo.cells[i].b) / 2 });
    const line = async (ids) => {
      const pts = ids.map(c);
      await p.mouse.move(pts[0].x, pts[0].y); await p.mouse.down();
      for (let k = 1; k < pts.length; k++) for (let s = 1; s <= 8; s++) await p.mouse.move(pts[k - 1].x + (pts[k].x - pts[k - 1].x) * s / 8, pts[k - 1].y + (pts[k].y - pts[k - 1].y) * s / 8);
      await p.mouse.up();
    };
    const full = () => p.evaluate(() => document.querySelectorAll('.look.full').length);
    // two tops in one line → refused
    await line([0, 1]); await p.waitForTimeout(300);
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-1-bad.png` });
    await p.waitForTimeout(900);
    if (await full()) fails.push(`${v.n}: a line of two tops made an outfit`);
    if (await p.evaluate(() => window.__opened.length)) fails.push(`${v.n}: a line opened the store`);
    // first column → outfit 1
    await line([0, 3, 6]); await p.waitForTimeout(1500);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-2-one.png` });
    if ((await full()) !== 1) fails.push(`${v.n}: column 1 did not make outfit 1`);
    // the same three again → refused
    await line([0, 3, 6]); await p.waitForTimeout(1300);
    if ((await full()) !== 1) fails.push(`${v.n}: the same outfit counted twice`);
    // one by one: 2, 5, 8
    for (const i of [1, 4, 7]) { await p.touchscreen.tap(c(i).x, c(i).y); await p.waitForTimeout(250); }
    await p.waitForTimeout(1400);
    if ((await full()) !== 2) fails.push(`${v.n}: picks one by one did not make outfit 2 (${await full()})`);
    // a diagonal: 3, 5, 7 → outfit 3 → end
    await line([2, 4, 6]); await p.waitForTimeout(1200);
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-3-three.png` });
    await p.waitForTimeout(3200);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-4-end.png` });
    const st = await p.evaluate(() => {
      const r = (e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; };
      const cta = document.getElementById('cta');
      return { end: document.getElementById('app').classList.contains('end'), cta: r(cta), clip: cta.scrollWidth > cta.clientWidth + 1,
        looks: [...document.querySelectorAll('.look')].map(r), disc: r(document.getElementById('disc')), head: r(document.getElementById('head')) };
    });
    if (!st.end) fails.push(`${v.n}: no end after ${CFG.need} outfits`);
    if (off(st.cta, v) || st.clip) fails.push(`${v.n}: CTA off screen or clipped`);
    if (hit(st.cta, st.disc)) fails.push(`${v.n}: CTA over disclaimer`);
    st.looks.forEach((l, i) => { if (off(l, v)) fails.push(`${v.n}: end look ${i + 1} off screen`); if (hit(l, st.cta)) fails.push(`${v.n}: CTA over end look ${i + 1}`); if (hit(l, st.head)) fails.push(`${v.n}: end look ${i + 1} under the hook`); });
    await p.mouse.click((st.cta.l + st.cta.r) / 2, (st.cta.t + st.cta.b) / 2);
    await p.waitForTimeout(200);
    const opened = await p.evaluate(() => window.__opened);
    if (opened.length !== 1 || opened[0] !== CFG.clickUrl) fails.push(`${v.n}: CTA opened ${JSON.stringify(opened)}`);
    if (errs.length) fails.push(`${v.n}: errors ${errs.join(' | ')}`);
    if (reqs.length) fails.push(`${v.n}: requests ${reqs.join(', ')}`);
    await ctx.close();
  }
  await b.close();
  console.log(fails.length ? 'FAIL\n' + fails.join('\n') : 'ALL OK');
})();
