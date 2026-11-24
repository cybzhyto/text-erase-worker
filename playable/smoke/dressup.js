// Copied from the caller's repository (scripts/playables/kits/board29/smoke-dressup.js) by its sync script; edit there, then sync.
// node smoke-dressup.js FILE [SHOTS_DIR] [views] — plays the «dressup» playable on 5 screens: the first touch on a woman
// dresses her (no store), a second touch on her does nothing, all dressed → pick phase, the pick → end, CTA opens once.
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
        ws: [...document.querySelectorAll('.w')].map(r), cta: r(document.getElementById('cta')), end: r(document.getElementById('endSub')),
        clip: t.scrollWidth > t.clientWidth + 1 };
    });
    const g = await geo();
    const N = CFG.women.length;
    if (g.clip) fails.push(`${v.n}: title clipped`);
    if (hit(g.hint, g.disc)) fails.push(`${v.n}: hint over the disclaimer`);
    g.ws.forEach((w, i) => {
      if (off(w, v)) fails.push(`${v.n}: woman ${i + 1} off screen`);
      if (w.r - w.l < 30) fails.push(`${v.n}: woman ${i + 1} under 30 px wide`);
      if (hit(w, g.head)) fails.push(`${v.n}: woman ${i + 1} under the hook`);
      if (hit(w, g.hint)) fails.push(`${v.n}: woman ${i + 1} under the hint`);
      for (let k = i + 1; k < N; k++) if (hit(w, g.ws[k])) fails.push(`${v.n}: women ${i + 1} and ${k + 1} overlap`);
    });
    const tapW = async (i) => { const w = (await geo()).ws[i]; await p.touchscreen.tap((w.l + w.r) / 2, w.t + (w.b - w.t) * 0.55); };
    const on = () => p.evaluate(() => document.querySelectorAll('.w.on').length);
    await tapW(0); await p.waitForTimeout(500);
    if ((await on()) !== 1) fails.push(`${v.n}: the first touch did not dress woman 1`);
    if (await p.evaluate(() => window.__opened.length)) fails.push(`${v.n}: the first touch opened the store`);
    await tapW(0); await p.waitForTimeout(300);
    if ((await on()) !== 1) fails.push(`${v.n}: a second touch on a dressed woman changed the count`);
    for (let i = 1; i < N; i++) { await tapW(i); await p.waitForTimeout(260); }
    await p.waitForTimeout(1200);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-1-pick.png` });
    if ((await on()) !== N) fails.push(`${v.n}: ${await on()} of ${N} dressed`);
    if (!(await p.evaluate(() => document.getElementById('app').classList.contains('pick')))) fails.push(`${v.n}: no pick phase`);
    await tapW(2); await p.waitForTimeout(500);
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-2-picked.png` });
    if (!(await p.evaluate(() => { const w = document.querySelectorAll('.w')[2]; return w.classList.contains('chosen'); }))) fails.push(`${v.n}: the pick did not mark woman 3`);
    await p.waitForTimeout(2300);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-3-end.png` });
    const st = await geo();
    if (!(await p.evaluate(() => document.getElementById('app').classList.contains('end')))) fails.push(`${v.n}: no end after the pick`);
    if (off(st.cta, v) || await p.evaluate(() => { const c = document.getElementById('cta'); return c.scrollWidth > c.clientWidth + 1; })) fails.push(`${v.n}: CTA off screen or clipped`);
    if (hit(st.cta, st.disc)) fails.push(`${v.n}: CTA over disclaimer`);
    if (hit(st.cta, st.end)) fails.push(`${v.n}: CTA over the end text`);
    if (off(st.end, v)) fails.push(`${v.n}: end text off screen`);
    st.ws.forEach((w, i) => { if (off(w, v)) fails.push(`${v.n}: end woman ${i + 1} off screen`); if (hit(w, st.end)) fails.push(`${v.n}: end text over woman ${i + 1}`); if (hit(w, st.head)) fails.push(`${v.n}: end woman ${i + 1} under the hook`); });
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
