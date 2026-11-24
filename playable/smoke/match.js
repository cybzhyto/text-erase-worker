// Copied from the caller's repository (scripts/playables/kits/board29/smoke-match.js) by its sync script; edit there, then sync.
// node smoke-match.js FILE [SHOTS_DIR] — plays any «match» playable (2–5 women) on 5 screens: a wrong drop, a right drag,
// taps for the rest, end, CTA once. Targets come from the file's own config (cards[k].woman), so one test fits every kit.
const { chromium } = require('playwright');
const fs = require('fs');
const FILE = process.argv[2], SHOTS = process.argv[3];
const html = fs.readFileSync(FILE, 'utf8');
const CFG = JSON.parse(html.match(/<script type="application\/json" id="cfg">([\s\S]*?)<\/script>/)[1]);
const NW = CFG.women.length, T0 = CFG.cards[0].woman, WRONG = T0 === 0 ? 1 : 0;
const CLICK = CFG.clickUrl;
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
      return { head: r(document.getElementById('head')), sub: r(document.getElementById('hSub')), disc: r(document.getElementById('disc')),
        women: [...document.querySelectorAll('.wom')].map(r), figs: [...document.querySelectorAll('.wom .fig')].map(r), names: [...document.querySelectorAll('.wom .name')].map(r),
        cards: [...document.querySelectorAll('.card')].map(r), hint: r(document.getElementById('hint')),
        clip: (() => { const t = document.getElementById('hTitle'); return t.scrollWidth > t.clientWidth + 1; })() };
    });
    if (geo.clip) fails.push(`${v.n}: title clipped`);
    geo.women.forEach((w, i) => { if (off(w, v)) fails.push(`${v.n}: woman ${i} off screen`); if (hit(w, geo.sub)) fails.push(`${v.n}: woman ${i} under the hook`); geo.cards.forEach((c, j) => { if (hit(geo.figs[i], c)) fails.push(`${v.n}: card ${j} covers woman ${i}`); }); });
    for (let i = 0; i < geo.names.length; i++) for (let j = i + 1; j < geo.names.length; j++) if (hit(geo.names[i], geo.names[j])) fails.push(`${v.n}: names ${i} and ${j} overlap`);
    geo.cards.forEach((c, i) => { if (off(c, v)) fails.push(`${v.n}: card ${i} off screen`); if (hit(c, geo.disc)) fails.push(`${v.n}: card ${i} over disclaimer`); if (Math.min(c.r - c.l, c.b - c.t) < 44) fails.push(`${v.n}: card ${i} too small`); });
    const c = (r) => ({ x: (r.l + r.r) / 2, y: (r.t + r.b) / 2 });
    const dragTo = async (from, to) => { await p.mouse.move(from.x, from.y); await p.mouse.down(); for (let s = 1; s <= 10; s++) await p.mouse.move(from.x + (to.x - from.x) * s / 10, from.y + (to.y - from.y) * s / 10); await p.mouse.up(); };
    // card 0 dropped on a woman it does not fit → miss, goes back
    await dragTo(c(geo.cards[0]), c(geo.figs[WRONG]));
    await p.waitForTimeout(200);
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-1-miss.png` });
    await p.waitForTimeout(700);
    const st1 = await p.evaluate(() => ({ on: document.querySelectorAll('.wom.on').length, pick: [...document.querySelectorAll('.wom')].map((e) => e.classList.contains('pick')), back: document.querySelectorAll('.card')[0].style.transform === '' }));
    if (st1.on !== 0 || !st1.back) fails.push(`${v.n}: wrong drop was not refused`);
    if (!st1.pick[T0]) fails.push(`${v.n}: the right woman does not glow after a miss`);
    if (await p.evaluate(() => window.__opened.length)) fails.push(`${v.n}: a drag opened the store`);
    // card 0 → its woman by drag, the rest by taps
    await dragTo(c(geo.cards[0]), c(geo.figs[T0]));
    await p.waitForTimeout(900);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-2-drag.png` });
    for (let k = 1; k < geo.cards.length; k++) { await p.touchscreen.tap(c(geo.cards[k]).x, c(geo.cards[k]).y); await p.waitForTimeout(900); }
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-3-all.png` });
    await p.waitForTimeout(2200);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-4-end.png` });
    const st = await p.evaluate(() => {
      const r = (e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; };
      const cta = document.getElementById('cta');
      return { end: document.getElementById('app').classList.contains('end'), on: document.querySelectorAll('.wom.on').length,
        cta: r(cta), clip: cta.scrollWidth > cta.clientWidth + 1, box: r(document.getElementById('endBox')), women: [...document.querySelectorAll('.wom')].map(r),
        head: r(document.getElementById('head')), disc: r(document.getElementById('disc')), chips: [...document.querySelectorAll('#chips span')].map(r) };
    });
    if (!st.end || st.on !== NW) fails.push(`${v.n}: end ${st.end}, dressed ${st.on}/${NW}`);
    if (off(st.cta, v) || st.clip) fails.push(`${v.n}: CTA off screen or clipped`);
    if (off(st.box, v)) fails.push(`${v.n}: end block off screen ${JSON.stringify(st.box)}`);
    if (hit(st.box, st.cta)) fails.push(`${v.n}: end block runs into the CTA`);
    if (hit(st.box, st.head)) fails.push(`${v.n}: end block under the hook`);
    st.women.forEach((w, i) => { if (hit(w, st.box)) fails.push(`${v.n}: end block covers woman ${i}`); if (hit(w, st.cta)) fails.push(`${v.n}: CTA covers woman ${i}`); });
    if (hit(st.cta, st.disc)) fails.push(`${v.n}: CTA over disclaimer`);
    await p.mouse.click((st.cta.l + st.cta.r) / 2, (st.cta.t + st.cta.b) / 2);
    await p.waitForTimeout(200);
    const opened = await p.evaluate(() => window.__opened);
    if (opened.length !== 1 || opened[0] !== CLICK) fails.push(`${v.n}: CTA opened ${JSON.stringify(opened)}`);
    if (errs.length) fails.push(`${v.n}: errors ${errs.join(' | ')}`);
    if (reqs.length) fails.push(`${v.n}: requests ${reqs.join(', ')}`);
    await ctx.close();
  }
  await b.close();
  console.log(fails.length ? 'FAIL\n' + fails.join('\n') : 'ALL OK');
})();
