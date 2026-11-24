// Copied from the caller's repository (scripts/playables/kits/board29/smoke-dress.js) by its sync script; edit there, then sync.
const { chromium } = require('playwright');
const until = async (p, fn, ms) => { for (let t = 0; t < ms; t += 200) { if (await p.evaluate(fn)) return true; await p.waitForTimeout(200); } return false; };
const fs = require('fs');
// node smoke-dress.js FILE [SHOTS_DIR] [views] — the dress-415 smoke for a kit with CFG.dialog: the chat intro plays
// (or a first touch skips it on «small»), the stickers go on, the outro plays, then the end card + CTA once.
const FILE = process.argv[2], SHOTS = process.argv[3];
const html = fs.readFileSync(FILE, 'utf8');
const MRAID = `(() => { let state='loading'; const L={}; window.__opened=[]; window.__viewable=true;
 window.mraid={getVersion:()=> '2.0',getState:()=>state,addEventListener:(n,f)=>{(L[n]=L[n]||[]).push(f)},removeEventListener(){},
 isViewable:()=>window.__viewable,open:(u)=>window.__opened.push(u),getPlacementType:()=> 'interstitial',useCustomClose(){}};
 window.__fire=(n,a)=>(L[n]||[]).forEach(f=>f(a)); setTimeout(()=>{state='default';window.__fire('ready')},400); })();`;
const CFG = JSON.parse(html.match(/<script type="application\/json" id="cfg">([\s\S]*?)<\/script>/)[1]);
const VIEWS = [{ n: 'portrait', w: 390, h: 844 }, { n: 'landscape', w: 844, h: 390 }, { n: 'small', w: 360, h: 640 }, { n: 'tablet', w: 768, h: 1024 }, { n: 'tall', w: 375, h: 812 }]
  .filter((v) => !process.argv[4] || process.argv[4].split(',').includes(v.n));
const chatBoxes = () => { const r = (e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; };
  return { b: [...document.querySelectorAll('#chat .b')].map(r), disc: r(document.getElementById('disc')), chatting: document.getElementById('app').classList.contains('chatting') }; };
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
    const off = (a) => a.l < -1 || a.t < -1 || a.r > v.w + 1 || a.b > v.h + 1;
    const hit = (a, b) => a.l < b.r - 1 && b.l < a.r - 1 && a.t < b.b - 1 && b.t < a.b - 1;
    const checkChat = async (when) => { const c = await p.evaluate(chatBoxes); c.b.forEach((b, i) => { if (off(b)) fails.push(`${v.n}: ${when} bubble ${i + 1} off screen`); if (hit(b, c.disc)) fails.push(`${v.n}: ${when} bubble ${i + 1} over the disclaimer`); }); return c; };
    if (CFG.dialog && CFG.dialog.intro) {
      await p.waitForTimeout(1500);
      if (v.n === 'small') {
        // a first touch skips the intro and never opens the store
        await p.touchscreen.tap(v.w / 2, v.h * 0.45); await p.waitForTimeout(400);
        if ((await p.evaluate(chatBoxes)).chatting) fails.push(`${v.n}: a touch did not skip the intro`);
        if (await p.evaluate(() => window.__opened.length)) fails.push(`${v.n}: skipping the intro opened the store`);
      } else {
        await p.waitForTimeout(1600);
        await checkChat('intro');
        if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-0-chat.png` });
        if (!(await until(p, () => !document.getElementById('app').classList.contains('chatting'), 12000))) fails.push(`${v.n}: the intro never ended`);
      }
      await p.waitForTimeout(1200);
    } else await p.waitForTimeout(1900);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-0-hint.png` });
    await p.waitForTimeout(800);
    // item 0 (top): real drag with mouse onto her chest
    const geo = await p.evaluate(() => {
      const it = [...document.querySelectorAll('.item')].map((e) => { const r = e.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
      const z = document.getElementById('photo').getBoundingClientRect();
      return { it, z: { x: z.left, y: z.top, w: z.width, h: z.height } };
    });
    const at = (fx, fy) => ({ x: geo.z.x + fx * geo.z.w, y: geo.z.y + fy * geo.z.h });
    await p.mouse.move(geo.it[0].x, geo.it[0].y); await p.mouse.down();
    const c = at(CFG.stages[0].items[0].at[0], CFG.stages[0].items[0].at[1]);
    for (let s = 1; s <= 10; s++) await p.mouse.move(geo.it[0].x + (c.x - geo.it[0].x) * s / 10, geo.it[0].y + (c.y - geo.it[0].y) * s / 10);
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-1-dragging.png` });
    await p.mouse.up();
    await p.waitForTimeout(260);
    if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-1b-wear.png` });
    await p.waitForTimeout(900);
    const opened0 = await p.evaluate(() => window.__opened.length);
    if (opened0) fails.push(`${v.n}: first drag opened the store`);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-2-top.png` });
    // drop the sandals OUTSIDE her → must spring back
    await p.mouse.move(geo.it[2].x, geo.it[2].y); await p.mouse.down();
    await p.mouse.move(geo.it[2].x + 4, geo.it[2].y - 30); await p.mouse.move(v.w - 5, 5); await p.mouse.up();
    await p.waitForTimeout(600);
    const back = await p.evaluate(() => { const e = document.querySelectorAll('.item')[2]; return !e.classList.contains('used') && e.style.transform === ''; });
    if (!back) fails.push(`${v.n}: sticker dropped outside did not go back`);
    // item 1 (trousers): a tap
    await p.touchscreen.tap(geo.it[1].x, geo.it[1].y);
    await p.waitForTimeout(1000);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-3-trousers.png` });
    // item 2 (sandals): drag to her feet
    await p.mouse.move(geo.it[2].x, geo.it[2].y); await p.mouse.down();
    const f = at(CFG.stages[0].items[2].at[0], CFG.stages[0].items[2].at[1] - 0.04);
    for (let s = 1; s <= 10; s++) await p.mouse.move(geo.it[2].x + (f.x - geo.it[2].x) * s / 10, geo.it[2].y + (f.y - geo.it[2].y) * s / 10);
    await p.mouse.up();
    await p.waitForTimeout(900);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-4-full.png` });
    const stages = await p.evaluate(() => JSON.parse(document.getElementById('cfg').textContent).stages.length);
    if (stages > 1) {
      await p.waitForTimeout(500);
      if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-4b-banner.png` });
      await p.waitForTimeout(1400);
      if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-4c-stage2.png` });
      const g2 = await p.evaluate(() => [...document.querySelectorAll('.item')].map((e) => { const r = e.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }));
      if (g2.length !== 3) fails.push(`${v.n}: stage 2 has ${g2.length} stickers`);
      const bagTo = at(0.3, 0.6);
      await p.mouse.move(g2[0].x, g2[0].y); await p.mouse.down();
      for (let s = 1; s <= 10; s++) await p.mouse.move(g2[0].x + (bagTo.x - g2[0].x) * s / 10, g2[0].y + (bagTo.y - g2[0].y) * s / 10);
      await p.mouse.up(); await p.waitForTimeout(900);
      if (SHOTS && v.n === 'portrait') await p.screenshot({ path: `${SHOTS}/${v.n}-4d-bag.png` });
      await p.touchscreen.tap(g2[1].x, g2[1].y); await p.waitForTimeout(900);
      await p.touchscreen.tap(g2[2].x, g2[2].y); await p.waitForTimeout(900);
      if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-4e-acc.png` });
    }
    if (CFG.dialog && CFG.dialog.outro) {
      // the payoff lines play before the end card
      await until(p, () => document.querySelectorAll('#chat:not(.gone) .b').length === JSON.parse(document.getElementById('cfg').textContent).dialog.outro.length, 12000);
      await p.waitForTimeout(500);
      await checkChat('outro');
      if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-4f-outro.png` });
      if (await p.evaluate(() => document.getElementById('app').classList.contains('end'))) fails.push(`${v.n}: end card before the outro finished`);
      if (!(await until(p, () => document.getElementById('app').classList.contains('end'), 14000))) fails.push(`${v.n}: no end after the outro`);
    }
    await p.waitForTimeout(1800);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-5-end.png` });
    const st = await p.evaluate(() => {
      const app = document.getElementById('app');
      const vis = (id) => { const r = document.getElementById(id).getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom }; };
      const t = document.getElementById("eTitle"); window.__tt = {sh: t.scrollHeight, ch: t.clientHeight, sw: t.scrollWidth, cw: t.clientWidth, fs: getComputedStyle(t).fontSize};
      return { end: app.classList.contains('end'), cta: vis('cta'), title: vis('eTitle'), clip: t.scrollWidth > t.clientWidth + 1,
        sub: vis('eSub'), subClip: document.getElementById('eSub').scrollWidth > document.getElementById('eSub').clientWidth + 1, before: vis('before'), disc: vis('disc'), front: [...document.querySelectorAll('#photo img')].map(i => i.style.opacity) };
    });
    if (!st.end) fails.push(`${v.n}: no end card`);
    for (const k of ['cta', 'title', 'sub', 'before', 'disc']) { const r = st[k]; if (r.l < -1 || r.t < -1 || r.r > v.w + 1 || r.b > v.h + 1) fails.push(`${v.n}: ${k} off screen ${JSON.stringify(r)}`); }
    if (st.clip) fails.push(`${v.n}: title clipped`); if (st.title.b > st.sub.t + 1) fails.push(`${v.n}: title runs into the line`);
    if (st.subClip) fails.push(`${v.n}: sub clipped`);
    // overlap check title vs cta vs disc
    if (st.cta.b > st.disc.t + 1) fails.push(`${v.n}: CTA overlaps disclaimer`);
    const ctaBox = st.cta;
    await p.mouse.click((ctaBox.l + ctaBox.r) / 2, (ctaBox.t + ctaBox.b) / 2);
    await p.waitForTimeout(200);
    const opened = await p.evaluate(() => window.__opened);
    if (opened.length !== 1 || opened[0] !== CFG.clickUrl) fails.push(`${v.n}: CTA opened ${JSON.stringify(opened)}`);
    if (errs.length) fails.push(`${v.n}: errors ${errs.join(' | ')}`);
    if (reqs.length) fails.push(`${v.n}: requests ${reqs.join(', ')}`);
    console.log(v.n, JSON.stringify(await p.evaluate(() => window.__tt)), JSON.stringify(st.title), JSON.stringify(st.sub));
    await ctx.close();
  }
  await b.close();
  console.log(fails.length ? 'FAIL\n' + fails.join('\n') : 'ALL OK');
})();
