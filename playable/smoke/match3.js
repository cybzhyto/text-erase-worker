// Copied from the caller's repository (scripts/playables/kits/board29/smoke-match3.js) by its sync script; edit there, then sync.
// node smoke-match3.js FILE [SHOTS_DIR] — plays the «match3» playable to the end on 5 screens. The test reads the board
// from the page (each tile's picture + position), finds a swap that makes a row of 3 the way a player would, and plays
// it: a drag onto the neighbour, then pick-then-neighbour, alternating. Checks: first touch never opens the store, pieces
// land on the board, end banner + CTA on screen, CTA opens the click URL once, no errors, no network.
const { chromium } = require('playwright');
const fs = require('fs');
const FILE = process.argv[2], SHOTS = process.argv[3];
const html = fs.readFileSync(FILE, 'utf8');
const CFG = JSON.parse(html.match(/<script type="application\/json" id="cfg">([\s\S]*?)<\/script>/)[1]);
const MRAID = `(() => { let state='loading'; const L={}; window.__opened=[]; window.__viewable=true;
 window.mraid={getVersion:()=> '2.0',getState:()=>state,addEventListener:(n,f)=>{(L[n]=L[n]||[]).push(f)},removeEventListener(){},
 isViewable:()=>window.__viewable,open:(u)=>window.__opened.push(u),getPlacementType:()=> 'interstitial',useCustomClose(){}};
 window.__fire=(n,a)=>(L[n]||[]).forEach(f=>f(a)); setTimeout(()=>{state='default';window.__fire('ready')},400); })();`;
const VIEWS = [{ n: 'portrait', w: 390, h: 844 }, { n: 'landscape', w: 844, h: 390 }, { n: 'small', w: 360, h: 640 }, { n: 'tablet', w: 768, h: 1024 }, { n: 'tall', w: 375, h: 812 }].filter((v) => !process.argv[4] || process.argv[4].split(',').includes(v.n));
const hit = (a, b) => a.l < b.r - 1 && b.l < a.r - 1 && a.t < b.b - 1 && b.t < a.b - 1;
const off = (a, v) => a.l < -1 || a.t < -1 || a.r > v.w + 1 || a.b > v.h + 1;

// the board as the player sees it: rows × cols of piece indexes, plus each cell's centre on screen
function readBoard(src2type, tiles, C, R) {
  const live = tiles.filter((t) => t.o > 0.5 && t.w > 4);
  const xs = [...new Set(live.map((t) => Math.round(t.x)))].sort((a, b) => a - b);
  const ys = [...new Set(live.map((t) => Math.round(t.y)))].sort((a, b) => a - b);
  const snap = (arr, v) => arr.reduce((best, a, i) => (Math.abs(a - v) < Math.abs(arr[best] - v) ? i : best), 0);
  const g = []; for (let r = 0; r < R; r++) g.push(new Array(C).fill(null));
  for (const t of live) { const c = snap(xs, t.x), r = snap(ys, t.y); if (r < R && c < C) g[r][c] = { t: src2type[t.src], x: t.x, y: t.y }; }
  return g;
}
function findMove(g, want) {
  const R = g.length, C = g[0].length, ty = (r, c) => (r >= 0 && r < R && c >= 0 && c < C && g[r][c] ? g[r][c].t : -1);
  const run = (r, c) => { const t = ty(r, c); if (t < 0) return 0; let h = 1, v = 1, i; for (i = c - 1; ty(r, i) === t; i--) h++; for (i = c + 1; ty(r, i) === t; i++) h++; for (i = r - 1; ty(i, c) === t; i--) v++; for (i = r + 1; ty(i, c) === t; i++) v++; return Math.max(h, v); };
  let any = null;
  for (let r = 0; r < R; r++) for (let c = 0; c < C; c++) for (const [dr, dc] of [[0, 1], [1, 0]]) {
    const r2 = r + dr, c2 = c + dc; if (!g[r] || !g[r][c] || r2 >= R || c2 >= C || !g[r2][c2]) continue;
    const a = g[r][c], b = g[r2][c2]; [g[r][c], g[r2][c2]] = [b, a];
    const ra = run(r, c), rb = run(r2, c2), ta = ty(r, c), tb = ty(r2, c2);
    [g[r][c], g[r2][c2]] = [a, b];
    if (ra >= 3 || rb >= 3) { const m = { a, b, t: ra >= 3 ? ta : tb }; if (want.has(m.t)) return m; any = any || m; }
  }
  return any;
}

(async () => {
  const b = await chromium.launch();
  const fails = [];
  const src2type = {}; CFG.items.forEach((it, k) => { src2type[CFG.images[it.img]] = k; });
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
      return { head: r(document.getElementById('head')), board: r(document.getElementById('board')), grid: r(document.getElementById('grid')),
        disc: r(document.getElementById('disc')), hint: r(document.getElementById('hint')), clip: t.scrollWidth > t.clientWidth + 1 };
    });
    if (geo.clip) fails.push(`${v.n}: title clipped`);
    for (const k of ['board', 'grid', 'head']) if (off(geo[k], v)) fails.push(`${v.n}: ${k} off screen ${JSON.stringify(geo[k])}`);
    if (hit(geo.board, geo.grid)) fails.push(`${v.n}: board and grid overlap`);
    if (hit(geo.head, geo.grid)) fails.push(`${v.n}: hook over the grid`);
    if (hit(geo.grid, geo.disc)) fails.push(`${v.n}: grid over disclaimer`);
    if ((geo.grid.r - geo.grid.l) / CFG.cols < 36) fails.push(`${v.n}: tiles smaller than 36 px`);
    let moves = 0, ended = false;
    for (let step = 0; step < 60 && !ended; step++) {
      const st = await p.evaluate(() => ({
        end: document.getElementById('app').classList.contains('end') || document.getElementById('app').classList.contains('done'),
        got: [...document.querySelectorAll('#dots i')].map((d) => d.classList.contains('on')),
        tiles: [...document.querySelectorAll('.tile')].map((e) => { const x = e.getBoundingClientRect(); return { src: e.firstChild.src, x: (x.left + x.right) / 2, y: (x.top + x.bottom) / 2, w: x.width, o: +getComputedStyle(e).opacity }; }),
      }));
      if (st.end) { ended = true; break; }
      // which piece is still missing → the dot order follows the order pieces were collected, so count them
      const want = new Set(CFG.items.map((_, k) => k));
      const g = readBoard(src2type, st.tiles, CFG.cols, CFG.rows);
      const m = findMove(g, want);
      if (!m) { await p.waitForTimeout(800); continue; }
      if (moves % 2 === 0) {
        await p.mouse.move(m.a.x, m.a.y); await p.mouse.down();
        for (let s = 1; s <= 6; s++) await p.mouse.move(m.a.x + (m.b.x - m.a.x) * s / 6, m.a.y + (m.b.y - m.a.y) * s / 6);
        await p.mouse.up();
      } else {
        await p.touchscreen.tap(m.a.x, m.a.y); await p.waitForTimeout(150); await p.touchscreen.tap(m.b.x, m.b.y);
      }
      moves++;
      if (moves === 1 && (await p.evaluate(() => window.__opened.length))) fails.push(`${v.n}: the first move opened the store`);
      if (moves === 1 && SHOTS && v.n === 'portrait') { await p.waitForTimeout(350); await p.screenshot({ path: `${SHOTS}/${v.n}-1-pop.png` }); }
      await p.waitForTimeout(1500);
      if (moves === 3 && SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-2-mid.png` });
    }
    await p.waitForTimeout(2600);
    if (SHOTS) await p.screenshot({ path: `${SHOTS}/${v.n}-4-end.png` });
    const st = await p.evaluate(() => {
      const r = (e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom }; };
      const cta = document.getElementById('cta');
      return { end: document.getElementById('app').classList.contains('end'), on: document.querySelectorAll('.slot.on').length,
        cta: r(cta), clip: cta.scrollWidth > cta.clientWidth + 1, board: r(document.getElementById('board')), disc: r(document.getElementById('disc')),
        head: r(document.getElementById('head')), ctaVis: +getComputedStyle(document.getElementById('ctaRow')).opacity };
    });
    if (!st.end || st.on !== CFG.items.length) fails.push(`${v.n}: end ${st.end}, pieces ${st.on}/${CFG.items.length} after ${moves} moves`);
    if (off(st.cta, v) || st.clip) fails.push(`${v.n}: CTA off screen or clipped`);
    if (st.ctaVis < 0.9) fails.push(`${v.n}: CTA not shown`);
    if (off(st.board, v)) fails.push(`${v.n}: end board off screen`);
    if (hit(st.board, st.cta)) fails.push(`${v.n}: CTA over the board`);
    if (hit(st.cta, st.disc)) fails.push(`${v.n}: CTA over disclaimer`);
    await p.mouse.click((st.cta.l + st.cta.r) / 2, (st.cta.t + st.cta.b) / 2);
    await p.waitForTimeout(200);
    const opened = await p.evaluate(() => window.__opened);
    if (opened.length !== 1 || opened[0] !== CFG.clickUrl) fails.push(`${v.n}: CTA opened ${JSON.stringify(opened)}`);
    if (errs.length) fails.push(`${v.n}: errors ${errs.join(' | ')}`);
    if (reqs.length) fails.push(`${v.n}: requests ${reqs.join(', ')}`);
    console.log(`${v.n}: ${moves} moves`);
    await ctx.close();
  }
  await b.close();
  console.log(fails.length ? 'FAIL\n' + fails.join('\n') : 'ALL OK');
})();
