// Copied from the caller's repository (scripts/playables/kits/capsule-40/smoke.js) by its sync script; edit there, then sync.
// node smoke.js FILE [SHOTS_DIR] — plays the «outfits» playable on 5 screens: 3 outfits by taps, end, CTA.
const { chromium } = require('playwright');
const fs = require('fs');
const FILE = process.argv[2], SHOTS = process.argv[3];
const html = fs.readFileSync(FILE, 'utf8');
const MRAID = `(() => { let state='loading'; const L={}; window.__opened=[]; window.__viewable=true;
 window.mraid={getVersion:()=> '2.0',getState:()=>state,addEventListener:(n,f)=>{(L[n]=L[n]||[]).push(f)},removeEventListener(){},
 isViewable:()=>window.__viewable,open:(u)=>window.__opened.push(u),getPlacementType:()=> 'interstitial',useCustomClose(){}};
 window.__fire=(n,a)=>(L[n]||[]).forEach(f=>f(a)); setTimeout(()=>{state='default';window.__fire('ready')},400); })();`;
const VIEWS = [{ n: 'portrait', w: 390, h: 844 }, { n: 'landscape', w: 844, h: 390 }, { n: 'small', w: 360, h: 640 }, { n: 'tablet', w: 768, h: 1024 }, { n: 'tall', w: 375, h: 812 }];
const hit = (a, b) => a.l < b.r - 1 && b.l < a.r - 1 && a.t < b.b - 1 && b.t < a.b - 1;
const off = (a, v) => a.l < -1 || a.t < -1 || a.r > v.w + 1 || a.b > v.h + 1;
const PLAN = [[0, 6, 9], [1, 5, 8], [2, 7, 8]];
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
    await p.waitForTimeout(2200);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-0-hint.png` });
    const R = (id) => p.evaluate((id) => { const x = document.getElementById(id).getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; }, id);
    const g = { head: await R('hTitle'), sub: await R('hSub'), board: await R('board'), caps: await R('caps'), shelf: await R('shelf'), disc: await R('disc') };
    g.cells = await p.evaluate(() => [...document.querySelectorAll('.cell')].map((e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; }));
    g.saved = await p.evaluate(() => [...document.querySelectorAll('.saved')].map((e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; }));
    for (const k of ['head', 'sub', 'board', 'caps', 'disc']) if (off(g[k], v)) fails.push(`${v.n}: ${k} off screen ${JSON.stringify(g[k])}`);
    for (const [a, c] of [['sub', 'board'], ['board', 'caps'], ['caps', 'disc'], ['sub', 'caps']]) if (hit(g[a], g[c])) fails.push(`${v.n}: ${a} overlaps ${c}`);
    g.saved.forEach((s, i) => { if (hit(s, g.board) || hit(s, g.caps) || off(s, v)) fails.push(`${v.n}: shelf card ${i} misplaced`); });
    const minCell = Math.min(...g.cells.map((c) => Math.min(c.r - c.l, c.b - c.t)));
    if (minCell < 44) fails.push(`${v.n}: capsule cells too small (${Math.round(minCell)}px)`);
    const clip = await p.evaluate(() => { const t = document.getElementById('hTitle'); return t.scrollWidth > t.clientWidth + 1; });
    if (clip) fails.push(`${v.n}: title clipped`);
    const c = (r) => ({ x: (r.l + r.r) / 2, y: (r.t + r.b) / 2 });
    let shot = 1;
    for (let o = 0; o < 3; o++) {
      for (const k of PLAN[o]) {
        const q = c(g.cells[k]);
        if (o === 0 && k === 0) await p.mouse.click(q.x, q.y); else await p.touchscreen.tap(q.x, q.y);
        await p.waitForTimeout(560);
        if (o === 0 && k === 0 && await p.evaluate(() => window.__opened.length)) fails.push(`${v.n}: first tap opened the store`);
      }
      if (o === 0) {  // swap: another top replaces the green one before the outfit completes? no — outfit already complete here
      }
      await p.waitForTimeout(250);
      if (SHOTS && (v.n === 'portrait' || o === 0)) await p.screenshot({ path: `${SHOTS}/${v.n}-${shot++}-outfit${o + 1}.png` });
      await p.waitForTimeout(1300);
    }
    await p.waitForTimeout(1800);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-9-end.png` });
    const st = await p.evaluate(() => {
      const r = (e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; };
      const cta = document.getElementById('cta');
      return { end: document.getElementById('app').classList.contains('end'), full: document.querySelectorAll('.saved.full').length,
        cta: r(cta), ctaClip: cta.scrollWidth > cta.clientWidth + 1, stats: r(document.getElementById('stats')),
        lines: [...document.querySelectorAll('#stats div')].map(r), saved: [...document.querySelectorAll('.saved')].map(r), disc: r(document.getElementById('disc')), head: r(document.getElementById('head')) };
    });
    if (!st.end) fails.push(`${v.n}: no end`);
    if (st.full !== 3) fails.push(`${v.n}: ${st.full}/3 outfits saved`);
    if (off(st.cta, v) || st.ctaClip) fails.push(`${v.n}: CTA off screen or clipped`);
    for (const l of st.lines) { if (hit(l, st.head)) fails.push(`${v.n}: stats under the hook`); for (const s of st.saved) if (hit(l, s)) fails.push(`${v.n}: stats over an outfit`); if (hit(l, st.cta)) fails.push(`${v.n}: stats over CTA`); }
    for (const s of st.saved) { if (off(s, v)) fails.push(`${v.n}: end outfit off screen`); if (hit(s, st.cta)) fails.push(`${v.n}: end outfit under CTA`); if (hit(s, st.head)) fails.push(`${v.n}: end outfit under the hook`); }
    if (hit(st.cta, st.disc)) fails.push(`${v.n}: CTA over disclaimer`);
    await p.mouse.click((st.cta.l + st.cta.r) / 2, (st.cta.t + st.cta.b) / 2);
    await p.waitForTimeout(200);
    const opened = await p.evaluate(() => window.__opened);
    if (opened.length !== 1 || opened[0] !== (process.env.SMOKE_CLICK || '')) fails.push(`${v.n}: CTA opened ${JSON.stringify(opened)}`);
    if (errs.length) fails.push(`${v.n}: errors ${errs.join(' | ')}`);
    if (reqs.length) fails.push(`${v.n}: requests ${reqs.join(', ')}`);
    await ctx.close();
  }
  await b.close();
  console.log(fails.length ? 'FAIL\n' + fails.join('\n') : 'ALL OK');
})();
