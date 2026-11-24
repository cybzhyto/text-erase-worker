// Copied from the caller's repository (scripts/playables/kits/board29/smoke-style.js) by its sync script; edit there, then sync.
// node smoke-style.js FILE [SHOTS_DIR] [views] — plays the «style» (accessorize) playable on 5 screens: the first touch
// picks a card (no store), a touch outside the cards does nothing, each round lands its pick in the flat-lay, end, CTA once.
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
    const geo = () => p.evaluate(() => {
      const r = (e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; };
      const t = document.getElementById('hTitle');
      return { head: r(document.getElementById('head')), disc: r(document.getElementById('disc')), hint: r(document.getElementById('hint')),
        board: r(document.getElementById('board')), cards: [...document.querySelectorAll('.card')].map(r), cta: r(document.getElementById('cta')),
        banner: r(document.getElementById('banner')), clip: t.scrollWidth > t.clientWidth + 1 || t.scrollHeight > t.clientHeight + 2 };
    });
    const g = await geo();
    const NR = CFG.rounds.length;
    if (g.clip) fails.push(`${v.n}: title clipped`);
    if (hit(g.head, g.board)) fails.push(`${v.n}: hook over the flat-lay`);
    if (hit(g.hint, g.disc)) fails.push(`${v.n}: hint over the disclaimer`);
    if (hit(g.hint, g.board)) fails.push(`${v.n}: hint over the flat-lay`);
    if (off(g.board, v)) fails.push(`${v.n}: flat-lay off screen`);
    g.cards.forEach((c, i) => {
      if (off(c, v)) fails.push(`${v.n}: card ${i + 1} off screen`);
      if (Math.min(c.r - c.l, c.b - c.t) < 60) fails.push(`${v.n}: card ${i + 1} under 60 px`);
      if (hit(c, g.board)) fails.push(`${v.n}: card ${i + 1} over the flat-lay`);
      if (hit(c, g.disc)) fails.push(`${v.n}: card ${i + 1} over the disclaimer`);
      if (hit(c, g.hint)) fails.push(`${v.n}: card ${i + 1} under the hint`);
    });
    const got = () => p.evaluate(() => [...document.querySelectorAll('.got')].filter((e) => e.style.display === 'block').length);
    // a touch on empty background: nothing
    await p.touchscreen.tap(v.w - 6, g.head.b + 4); await p.waitForTimeout(300);
    if (await got()) fails.push(`${v.n}: a touch outside the cards placed something`);
    if (await p.evaluate(() => window.__opened.length)) fails.push(`${v.n}: the first touch opened the store`);
    for (let r = 0; r < NR; r++) {
      const c = (await geo()).cards[r % 2];
      await p.touchscreen.tap((c.l + c.r) / 2, (c.t + c.b) / 2);
      await p.waitForTimeout(1300);
      if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-1-round${r + 1}.png` });
      if ((await got()) !== r + 1) fails.push(`${v.n}: round ${r + 1} did not land (${await got()})`);
    }
    await p.waitForTimeout(1500);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-2-end.png` });
    const st = await geo();
    if (!(await p.evaluate(() => document.getElementById('app').classList.contains('end')))) fails.push(`${v.n}: no end after ${NR} rounds`);
    if (off(st.cta, v) || await p.evaluate(() => { const c = document.getElementById('cta'); return c.scrollWidth > c.clientWidth + 1; })) fails.push(`${v.n}: CTA off screen or clipped`);
    if (hit(st.cta, st.disc)) fails.push(`${v.n}: CTA over disclaimer`);
    if (hit(st.cta, st.board)) fails.push(`${v.n}: CTA over the flat-lay`);
    if (hit(st.banner, st.board)) fails.push(`${v.n}: banner over the flat-lay`);
    if (hit(st.banner, st.cta)) fails.push(`${v.n}: banner over the CTA`);
    if (off(st.banner, v)) fails.push(`${v.n}: banner off screen`);
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
