// Copied from the caller's repository (scripts/playables/kits/swap-wife43/smoke.js) by its sync script; edit there, then sync.
// node smoke.js FILE [SHOTS_DIR] — plays the «swap» playable on 5 screens: drag look 1, tap 2 and 3, re-tap, end, CTA.
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
      return { head: r(document.getElementById('head')), disc: r(document.getElementById('disc')), zone: r(document.getElementById('zone')),
        cards: [...document.querySelectorAll('.card')].map(r), hint: r(document.getElementById('hint')), mini: r(document.getElementById('mini')),
        clip: (() => { const t = document.getElementById('hook'); return t.scrollWidth > t.clientWidth + 1; })() };
    });
    if (geo.clip) fails.push(`${v.n}: hook clipped`);
    geo.cards.forEach((c, i) => {
      if (off(c, v)) fails.push(`${v.n}: card ${i} off screen`);
      if (hit(c, geo.disc)) fails.push(`${v.n}: card ${i} over disclaimer`);
      if (hit(c, geo.head)) fails.push(`${v.n}: card ${i} under the hook`);
      if (hit(c, geo.hint)) fails.push(`${v.n}: card ${i} under the hint`);
      if (Math.min(c.r - c.l, c.b - c.t) < 60) fails.push(`${v.n}: card ${i} too small ${Math.round(c.r - c.l)}`);
      if (c.r > geo.zone.l + 4) fails.push(`${v.n}: card ${i} covers her (${Math.round(c.r)} > ${Math.round(geo.zone.l)})`);
    });
    if (hit(geo.head, geo.zone) && geo.head.b > geo.zone.t + (geo.zone.b - geo.zone.t) * 0.05) fails.push(`${v.n}: hook over her head`);
    const c = (r) => ({ x: (r.l + r.r) / 2, y: (r.t + r.b) / 2 });
    const dragTo = async (from, to) => { await p.mouse.move(from.x, from.y); await p.mouse.down(); for (let s = 1; s <= 10; s++) await p.mouse.move(from.x + (to.x - from.x) * s / 10, from.y + (to.y - from.y) * s / 10); await p.mouse.up(); };
    // card 0 dropped back near the rail → no change
    await dragTo(c(geo.cards[0]), { x: c(geo.cards[0]).x, y: c(geo.cards[0]).y + 30 });
    await p.waitForTimeout(600);
    const st0 = await p.evaluate(() => document.querySelectorAll('.card.on').length);
    if (st0 !== 0) fails.push(`${v.n}: a short drag on the rail dressed her`);
    await dragTo(c(geo.cards[0]), c(geo.zone));
    await p.waitForTimeout(900);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-1-look1.png` });
    const st1 = await p.evaluate(() => ({ on: [...document.querySelectorAll('.card')].map((e) => e.classList.contains('on')), opened: window.__opened.length }));
    if (!st1.on[0]) fails.push(`${v.n}: drag did not dress look 1`);
    if (st1.opened) fails.push(`${v.n}: a drag opened the store`);
    await p.touchscreen.tap(c(geo.cards[1]).x, c(geo.cards[1]).y); await p.waitForTimeout(800);
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-2-look2.png` });
    await p.touchscreen.tap(c(geo.cards[0]).x, c(geo.cards[0]).y); await p.waitForTimeout(800);   // back to look 1: not the end yet
    const mid = await p.evaluate(() => ({ end: document.getElementById('app').classList.contains('end'), done: document.getElementById('app').classList.contains('done') }));
    if (mid.end || mid.done) fails.push(`${v.n}: ended before every look was tried`);
    await p.touchscreen.tap(c(geo.cards[2]).x, c(geo.cards[2]).y); await p.waitForTimeout(800);
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-3-look3.png` });
    await p.waitForTimeout(2200);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-4-end.png` });
    const st = await p.evaluate(() => {
      const r = (e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; };
      const cta = document.getElementById('cta'), t = document.getElementById('eTitle');
      return { end: document.getElementById('app').classList.contains('end'), cta: r(cta), clip: cta.scrollWidth > cta.clientWidth + 1,
        title: r(t), tclip: t.scrollWidth > t.clientWidth + 1, sub: r(document.getElementById('eSub')), head: r(document.getElementById('head')),
        disc: r(document.getElementById('disc')), before: r(document.getElementById('before')), bshow: getComputedStyle(document.getElementById('before')).display !== 'none',
        dots: document.querySelectorAll('#dots i.on').length };
    });
    if (!st.end || st.dots !== 3) fails.push(`${v.n}: end ${st.end}, dots ${st.dots}/3`);
    if (off(st.cta, v) || st.clip) fails.push(`${v.n}: CTA off screen or clipped`);
    if (off(st.title, v) || st.tclip) fails.push(`${v.n}: end title off screen or clipped`);
    if (hit(st.title, st.head)) fails.push(`${v.n}: end title under the hook`);
    if (hit(st.cta, st.disc) || hit(st.sub, st.disc)) fails.push(`${v.n}: end text over disclaimer`);
    if (st.bshow && (hit(st.before, st.title) || hit(st.before, st.head) || off(st.before, v))) fails.push(`${v.n}: before card misplaced`);
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
