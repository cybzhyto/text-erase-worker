// Copied from the caller's repository (scripts/playables/loc/smoke-endcard.js) by its sync script; edit there, then sync.
// node smoke-ec.js FILE SHOTS — video end card: loads with MRAID, plays (currentTime moves), no network, a tap opens the click URL.
const { chromium } = require('playwright'); const fs = require('fs');
const FILE = process.argv[2], SHOTS = process.argv[3]; const html = fs.readFileSync(FILE, 'utf8');
const cfg = JSON.parse(/<script type="application\/json" id="cfg">([\s\S]*?)<\/script>/.exec(html)[1]);
const MRAID = `(() => { let state='loading'; const L={}; window.__opened=[];
 window.mraid={getVersion:()=> '2.0',getState:()=>state,addEventListener:(n,f)=>{(L[n]=L[n]||[]).push(f)},removeEventListener(){},
 isViewable:()=>true,open:(u)=>window.__opened.push(u),getPlacementType:()=> 'interstitial',useCustomClose(){}};
 setTimeout(()=>{state='default';(L.ready||[]).forEach(f=>f())},400); })();`;
const VIEWS = [{ n: 'portrait', w: 390, h: 844 }, { n: 'landscape', w: 844, h: 390 }, { n: 'small', w: 360, h: 640 }, { n: 'tablet', w: 768, h: 1024 }, { n: 'tall', w: 375, h: 812 }];
(async () => {
  const b = await chromium.launch(); const fails = [];
  for (const v of VIEWS) {
    const ctx = await b.newContext({ viewport: { width: v.w, height: v.h }, hasTouch: true, isMobile: v.n !== 'landscape' });
    const p = await ctx.newPage(); const errs = [], reqs = [];
    p.on('pageerror', e => errs.push(String(e)));
    await p.route('**/*', r => { const u = r.request().url(); if (u === 'https://playable.test/ad.html') return r.fulfill({ body: html, contentType: 'text/html' }); if (!u.startsWith('data:')) reqs.push(u); return r.abort(); });
    await p.addInitScript(MRAID); await p.goto('https://playable.test/ad.html'); await p.waitForTimeout(2500);
    const st = await p.evaluate(() => { const x = document.getElementById('v'); return { t: x.currentTime, paused: x.paused, w: x.videoWidth, err: x.error && x.error.code }; });
    await p.screenshot({ path: `${SHOTS}/${v.n}.png` });
    await p.touchscreen.tap(v.w / 2, v.h / 2); await p.waitForTimeout(200);
    const opened = await p.evaluate(() => window.__opened);
    if (errs.length) fails.push(`${v.n}: page errors ${errs.join(' | ')}`);
    if (reqs.length) fails.push(`${v.n}: network ${reqs.slice(0, 2).join(', ')}`);
    if (!(st.w > 0) || st.err) fails.push(`${v.n}: video did not load (${JSON.stringify(st)})`);
    if (st.paused || !(st.t > 0.5)) fails.push(`${v.n}: video not playing (${JSON.stringify(st)})`);
    if (opened.length !== 1 || opened[0] !== cfg.clickUrl) fails.push(`${v.n}: tap opened ${JSON.stringify(opened)}`);
    console.log(v.n, JSON.stringify(st), 'opened', opened.length);
    await ctx.close();
  }
  await b.close(); console.log(fails.length ? 'FAIL\n' + fails.join('\n') : 'ALL OK'); process.exit(fails.length ? 1 : 0);
})();
