// Copied from the caller's repository (scripts/playables/kits/assemble-2/smoke.js) by its sync script; edit there, then sync.
// node smoke.js FILE [SHOTS_DIR] — plays the «assemble» playable on 5 screens (drag, drop outside, tap, end, CTA).
const { chromium } = require('playwright');
const fs = require('fs');
const FILE = process.argv[2], SHOTS = process.argv[3];
const html = fs.readFileSync(FILE, 'utf8');
const MRAID = `(() => { let state='loading'; const L={}; window.__opened=[]; window.__viewable=true;
 window.mraid={getVersion:()=> '2.0',getState:()=>state,addEventListener:(n,f)=>{(L[n]=L[n]||[]).push(f)},removeEventListener(){},
 isViewable:()=>window.__viewable,open:(u)=>window.__opened.push(u),getPlacementType:()=> 'interstitial',useCustomClose(){}};
 window.__fire=(n,a)=>(L[n]||[]).forEach(f=>f(a)); setTimeout(()=>{state='default';window.__fire('ready')},400); })();`;
const VIEWS = [{ n: 'portrait', w: 390, h: 844 }, { n: 'landscape', w: 844, h: 390 }, { n: 'small', w: 360, h: 640 }, { n: 'tablet', w: 768, h: 1024 }, { n: 'tall', w: 375, h: 812 }];
const R = (r) => ({ l: r.left, t: r.top, r: r.right, b: r.bottom });
const hit = (a, b) => a.l < b.r - 1 && b.l < a.r - 1 && a.t < b.b - 1 && b.t < a.b - 1;
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
    await p.waitForTimeout(1900);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-0-hint.png` });
    await p.waitForTimeout(800);
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-0b-hint.png` });
    const g = await p.evaluate(() => {
      const r = (e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; };
      return { items: [...document.querySelectorAll('.item')].map(r), board: r(document.getElementById('board')),
        head: r(document.getElementById('head')), title: r(document.getElementById('hTitle')), disc: r(document.getElementById('disc')),
        hint: r(document.getElementById('hint')), clip: (() => { const t = document.getElementById('hTitle'); return t.scrollWidth > t.clientWidth + 1; })() };
    });
    const c = (r) => ({ x: (r.l + r.r) / 2, y: (r.t + r.b) / 2 });
    // layout checks
    for (let i = 0; i < g.items.length; i++) {
      const a = g.items[i];
      if (a.l < -1 || a.t < -1 || a.r > v.w + 1 || a.b > v.h + 1) fails.push(`${v.n}: sticker ${i} off screen`);
      for (let j = i + 1; j < g.items.length; j++) if (hit(a, g.items[j])) fails.push(`${v.n}: stickers ${i} and ${j} overlap`);
      if (hit(a, g.head)) fails.push(`${v.n}: sticker ${i} covers the hook`);
      const w = a.r - a.l, h = a.b - a.t; if (Math.max(w, h) < 56) fails.push(`${v.n}: sticker ${i} too small (${Math.round(w)}x${Math.round(h)})`);
    }
    if (g.clip) fails.push(`${v.n}: hook clipped`);
    if (hit(g.head, g.board)) fails.push(`${v.n}: hook runs into the board`);
    if (g.board.b > g.hint.t + 1) fails.push(`${v.n}: board runs into the hint`);
    // piece 0 (coat): real mouse drag onto the board
    const bc = c(g.board), s0 = c(g.items[0]);
    await p.mouse.move(s0.x, s0.y); await p.mouse.down();
    for (let s = 1; s <= 10; s++) await p.mouse.move(s0.x + (bc.x - s0.x) * s / 10, s0.y + (bc.y - s0.y) * s / 10);
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-1-dragging.png` });
    await p.mouse.up();
    await p.waitForTimeout(1000);
    if (await p.evaluate(() => window.__opened.length)) fails.push(`${v.n}: first drag opened the store`);
    // piece 4 dropped OUTSIDE the board → back home
    const s4 = c(g.items[4]);
    await p.mouse.move(s4.x, s4.y); await p.mouse.down();
    await p.mouse.move(s4.x + 4, s4.y - 30); await p.mouse.move(v.w - 5, 5); await p.mouse.up();
    await p.waitForTimeout(600);
    const back = await p.evaluate(() => { const e = document.querySelectorAll('.item')[4]; return !e.classList.contains('used') && e.style.transform === ''; });
    if (!back) fails.push(`${v.n}: piece dropped outside did not go back`);
    // piece 1 tap, 2 drag, 3 tap, 4 drag
    await p.touchscreen.tap(c(g.items[1]).x, c(g.items[1]).y); await p.waitForTimeout(900);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-2-two.png` });
    for (const i of [2, 4]) {
      const si = c(g.items[i]);
      if (i === 4) { await p.touchscreen.tap(c(g.items[3]).x, c(g.items[3]).y); await p.waitForTimeout(900); }
      await p.mouse.move(si.x, si.y); await p.mouse.down();
      const to = { x: g.board.l + (g.board.r - g.board.l) * 0.7, y: g.board.t + (g.board.b - g.board.t) * 0.6 };
      for (let s = 1; s <= 10; s++) await p.mouse.move(si.x + (to.x - si.x) * s / 10, si.y + (to.y - si.y) * s / 10);
      await p.mouse.up(); await p.waitForTimeout(900);
    }
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-3-banner.png` });
    await p.waitForTimeout(2400);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-4-end.png` });
    const st = await p.evaluate(() => {
      const r = (e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; };
      const cta = document.getElementById('cta');
      return { end: document.getElementById('app').classList.contains('end'), on: document.querySelectorAll('.slot.on').length,
        banner: r(document.getElementById('banner')), cta: r(cta), ctaClip: cta.scrollWidth > cta.clientWidth + 1, board: r(document.getElementById('board')), disc: r(document.getElementById('disc')) };
    });
    if (!st.end) fails.push(`${v.n}: no end`);
    if (st.on !== 5) fails.push(`${v.n}: ${st.on}/5 pieces on the board`);
    if (st.cta.l < -1 || st.cta.r > v.w + 1 || st.cta.b > v.h + 1) fails.push(`${v.n}: CTA off screen`);
    if (st.ctaClip) fails.push(`${v.n}: CTA text clipped`);
    if (hit(st.cta, st.board)) fails.push(`${v.n}: CTA covers the board`);
    if (hit(st.cta, st.disc)) fails.push(`${v.n}: CTA covers the disclaimer`);
    const bt = await p.evaluate(() => { const e = document.getElementById('banner'); const s = document.createElement('span'); s.textContent = e.textContent; e.textContent = ''; e.appendChild(s); const x = s.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; });
    if (hit(bt, st.board)) fails.push(`${v.n}: banner covers the board`);
    if (hit(bt, st.cta)) fails.push(`${v.n}: banner covers the CTA`);
    if (bt.l < 0 || bt.r > v.w) fails.push(`${v.n}: banner off screen`);
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
