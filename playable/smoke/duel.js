// Copied from the caller's repository (scripts/playable/smoke.js) by its sync script; edit there, then sync.
'use strict';
// Browser smoke test for lib/playable-build: what the static check() cannot see. Run it after every change to a mechanic
// (lib/playable-build/<mechanic>.js or core.js) and on any built file you doubt. Needs Playwright + Chromium (cloud
// sessions have both; on a Mac: `npm i -g playwright && npx playwright install chromium`).
//
//   node scripts/playable/smoke.js                       builds the test fixture of every mechanic and tests it
//   node scripts/playable/smoke.js ad.html [more.html]   tests built files (the click URL is read from the file)
//   --shots DIR                                          also saves screenshots (play / result / rotated) per run
//
// Each file runs 6 times: portrait 390×844 and landscape 844×390, each with an MRAID stub (AppLovin's SDK: 'loading',
// then 'ready' after 400 ms) and without MRAID (the browser preview), plus 360×640 and 768×1024 with MRAID. It fails when:
//   • the page asks for anything outside the file (AppLovin: no network), or logs a JS error;
//   • the ad lays out before MRAID 'ready', or anything moves / opens on its own in the 3 s before the first touch;
//   • the first tap opens the store, a round does not advance on a tap, or there is no result screen;
//   • the question, the looks, the result or the CTA leave the screen or clip their text, in either orientation, also
//     after rotating mid-play and on the result screen; the CTA is wider than its column;
//   • the CTA does not call mraid.open(clickUrl) exactly once (preview: shows the toast, never navigates);
//   • viewableChange(false) does not pause the animations.
const fs = require('fs');
const path = require('path');

let chromium;
try { ({ chromium } = require('playwright')); } catch (e) {
  console.error('playwright is not installed: npm i -g playwright && npx playwright install chromium');
  process.exit(2);
}
let lib = null; try { lib = require('../../lib/playable-build'); } catch (e) { /* worker copy: no lib */ }

const AD_URL = 'https://playable.test/ad.html';
// the two main sizes run with and without MRAID; a small phone and a tablet run once more (layout only differs by size)
const VIEWS = [{ name: 'portrait', w: 390, h: 844, modes: [true, false] }, { name: 'landscape', w: 844, h: 390, modes: [true, false] },
  { name: 'small', w: 360, h: 640, modes: [true] }, { name: 'tablet', w: 768, h: 1024, modes: [true] }];

const MRAID_STUB = `(() => {
  let state = 'loading';
  const L = {};
  window.__opened = []; window.__early = []; window.__viewable = true;
  window.mraid = {
    getVersion: () => '2.0',
    getState: () => state,
    addEventListener: (n, f) => { (L[n] = L[n] || []).push(f); },
    removeEventListener: (n, f) => { L[n] = (L[n] || []).filter((g) => g !== f); },
    isViewable: () => { if (state === 'loading') window.__early.push('isViewable'); return window.__viewable; },
    open: (u) => { if (state === 'loading') window.__early.push('open'); window.__opened.push(u); },
    getPlacementType: () => 'interstitial',
    useCustomClose: () => {},
  };
  window.__fire = (n, a) => (L[n] || []).forEach((f) => f(a));
  setTimeout(() => { state = 'default'; window.__fire('ready'); }, 400);
})();`;
const PREVIEW_STUB = 'window.__opened = []; window.__early = [];';

function fixtures() {
  const out = [];
  for (const key of Object.keys(lib.MECHANICS)) {
    const dir = path.join(__dirname, '../../lib/playable-build/test');
    const pics = JSON.parse(fs.readFileSync(path.join(dir, 'pictures.json'), 'utf8'));
    const spec = {
      mechanic: key, lang: 'en', clickUrl: (process.env.SMOKE_CLICK || ''),
      styles: { romantic: { name: 'Soft Romantic' }, classic: { name: 'Modern Classic' }, minimal: { name: 'Clean Minimal' } },
      rounds: [
        { q: 'Date night: which one would you wear?', a: { img: 'r1', style: 'romantic' }, b: { img: 'm1', style: 'minimal' } },
        { q: 'Monday at the office?', a: { img: 'c1', style: 'classic' }, b: { img: 'r2', style: 'romantic' } },
        { q: 'Weekend brunch?', a: { img: 'm2', style: 'minimal' }, b: { img: 'c2', style: 'classic' } },
      ],
    };
    const r = lib.build(spec, pics);
    if (!r.ok) throw new Error(`fixture ${key} does not build: ${r.issues.join('; ')}`);
    out.push({ name: `fixture:${key}`, html: r.html });
    // the same game with the longest words it can get: each fixed word from the language where it is longest (the pt CTA
    // «Quero minha cápsula» ran off a tablet turned sideways, 2026-11-06, while the English fixture passed) and the model's
    // words near their limits
    const UI = require('../../lib/playable-build/recipe').UI;
    const text = {};
    for (const lang of Object.keys(UI)) for (const [k, v] of Object.entries(UI[lang])) if (!text[k] || v.length > text[k].length) text[k] = v;
    const long = Object.assign({}, spec, {
      text,
      styles: {
        romantic: { name: 'Effortless Modern Native', line: 'Brand construye tu armario cápsula alrededor de siluetas suaves y tonos empolvados.' },
        classic: { name: 'Klassisch und Selbstsicher', line: 'Brand baut deine Capsule-Garderobe um klare Blazer, Bundfaltenhosen und Kaschmir.' },
        minimal: { name: 'Minimalismo Sofisticato', line: 'Brand costruisce il tuo guardaroba capsula intorno a linee pulite e toni neutri.' },
      },
      rounds: spec.rounds.map((rd, n) => Object.assign({}, rd, { q: ['Freitagabend: welcher Look passt eher zu dir?',
        'Segunda no escritório: qual look você escolhe?', 'Brunch del fine settimana: quale look indossi?'][n] })),
    });
    long.styles.classic.name = long.styles.classic.name.slice(0, 24);
    const r2 = lib.build(long, pics);
    if (!r2.ok) throw new Error(`fixture ${key} (longest words) does not build: ${r2.issues.join('; ')}`);
    out.push({ name: `fixture:${key}·longest words`, html: r2.html });
  }
  return out;
}

function cfgOf(html) {
  const m = /<script type="application\/json" id="cfg">([\s\S]*?)<\/script>/.exec(html);
  return m ? JSON.parse(m[1]) : {};
}

// Boxes of the elements that must stay on screen, plus text that overflows its box.
const PROBE = `(sel) => sel.map((s) => {
  const e = document.querySelector(s);
  if (!e) return { s, missing: true };
  const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
  return { s, x: r.left, y: r.top, w: r.width, h: r.height, hidden: cs.display === 'none' || cs.visibility === 'hidden' || r.width < 2 || r.height < 2,
    clip: e.scrollHeight > e.clientHeight + 2 || e.scrollWidth > e.clientWidth + 2, text: (e.textContent || '').trim() };
})`;

// PB28: every text the ad shows reads on a phone: contrast ≥ 4.5:1 against what is behind it (3:1 for large text: ≥ 24 px, or
// ≥ 18.7 px bold, WCAG AA) and ≥ 10 px tall. Review of Lab #555 (2026-11-06): the «AI-generated images» line was 2.9:1 at 10 px.
// Behind = the element's own background and its parents' down to the first opaque one; an element still fading in is skipped.
const CONTRAST = `(sel) => sel.map((s) => {
  const e = document.querySelector(s);
  if (!e || !(e.textContent || '').trim()) return { s, skip: true };
  const rgba = (c) => { const m = /rgba?\\(([^)]+)\\)/.exec(String(c)); if (!m) return null;
    const v = m[1].split(/[\\s,\\/]+/).filter(Boolean).map(Number); return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1]; };
  const over = (t, b) => [0, 1, 2].map((i) => t[i] * t[3] + b[i] * (1 - t[3])).concat(1);
  const lum = (c) => { const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  let op = 1; const under = [];
  for (let p = e; p; p = p.parentElement) {
    const cs = getComputedStyle(p); op *= Number(cs.opacity);
    const b = rgba(cs.backgroundColor);
    if (b && b[3] > 0 && !(under.length && under[under.length - 1][3] >= 1)) under.push(b);
  }
  if (op < 0.95) return { s, skip: true };
  let bg = [255, 255, 255, 1];
  for (let i = under.length - 1; i >= 0; i--) bg = over(under[i], bg);
  const cs = getComputedStyle(e), fg = over(rgba(cs.color), bg), size = parseFloat(cs.fontSize);
  const L1 = lum(fg), L2 = lum(bg);
  return { s, ratio: (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05), size, large: size >= 24 || (size >= 18.66 && Number(cs.fontWeight) >= 700) };
})`;

async function readable(page, sels, label, fail) {
  for (const c of await page.evaluate(`(${CONTRAST})(${JSON.stringify(sels)})`)) {
    if (c.skip) continue;
    const need = c.large ? 3 : 4.5;
    if (c.ratio < need) fail(`${label}: ${c.s} text contrast ${c.ratio.toFixed(2)}:1, needs ${need}:1`);
    if (c.size < 10) fail(`${label}: ${c.s} text is ${c.size.toFixed(1)} px, needs 10 px`);
  }
}

async function onScreen(page, sels, W, H, label, fail, textSel) {
  const boxes = await page.evaluate(`(${PROBE})(${JSON.stringify(sels)})`);
  for (const b of boxes) {
    if (b.missing) { fail(`${label}: ${b.s} is missing`); continue; }
    if (b.hidden) { fail(`${label}: ${b.s} is not visible`); continue; }
    if (b.x < -1 || b.y < -1 || b.x + b.w > W + 1 || b.y + b.h > H + 1) {
      fail(`${label}: ${b.s} leaves the screen (${Math.round(b.x)},${Math.round(b.y)} ${Math.round(b.w)}×${Math.round(b.h)} in ${W}×${H})`);
    }
    if ((textSel || []).includes(b.s)) {
      if (!b.text) fail(`${label}: ${b.s} has no text`);
      if (b.clip) fail(`${label}: ${b.s} text does not fit its box`);
    }
  }
  return boxes;
}

const overlap = (a, b) => a && b && a.x < b.x + b.w - 1 && b.x < a.x + a.w - 1 && a.y < b.y + b.h - 1 && b.y < a.y + a.h - 1;

// The CTA stays inside its column (#rBottom): a CTA wider than the column can still be on screen by a pixel, as the pt one was
// on a tablet turned sideways (2026-11-06), so the screen edge alone does not catch it.
async function inColumn(page, label, fail) {
  const [cta, col] = await page.evaluate(`(${PROBE})(${JSON.stringify(['#cta', '#rBottom'])})`);
  if (cta && col && !cta.missing && !col.missing && (cta.x < col.x - 1 || cta.x + cta.w > col.x + col.w + 1)) {
    fail(`${label}: the CTA is wider than its column (${Math.round(cta.w)} px in ${Math.round(col.w)})`);
  }
}

// A finger on the element's centre. Not page.tap(): Playwright waits for a still element, and the CTA pulses forever.
// Also proves nothing covers the element there (the touch goes to whatever is on top).
async function tapAt(page, sel) {
  const b = await page.evaluate(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
  await page.touchscreen.tap(b[0], b[1]);
}

async function run(browser, file, view, withMraid, shots) {
  const fails = [];
  const fail = (m) => fails.push(m);
  const cfg = cfgOf(file.html);
  const rounds = (cfg.rounds || []).length;
  const ctx = await browser.newContext({ viewport: { width: view.w, height: view.h }, hasTouch: true, isMobile: true, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const outside = [], errors = [];
  await page.route('**/*', (route) => {
    const u = route.request().url();
    if (u === AD_URL) return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: file.html });
    outside.push(u);
    return route.abort();
  });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
  page.on('framenavigated', (f) => { if (f === page.mainFrame() && f.url() !== AD_URL) fail(`navigated by itself to ${f.url()}`); });
  await page.addInitScript(withMraid ? MRAID_STUB : PREVIEW_STUB);
  const tag = `${file.name} ${view.name} ${withMraid ? 'mraid' : 'preview'}`;
  const shot = async (what) => {
    if (!shots) return;
    fs.mkdirSync(shots, { recursive: true });
    const safe = `${file.name}-${view.name}-${withMraid ? 'mraid' : 'preview'}-${what}`.replace(/[^a-z0-9_.-]+/gi, '_');
    await page.screenshot({ path: path.join(shots, `${safe}.png`) });
  };

  try {
    await page.goto(AD_URL, { waitUntil: 'load' });
    if (withMraid) {
      // the stub turns 'ready' at 400 ms; core sets --u on #app at its first layout
      await page.waitForTimeout(150);
      const laidOut = await page.evaluate(`document.getElementById('app').style.getPropertyValue('--u')`);
      if (laidOut) fail('laid out before MRAID ready');
    }
    await page.waitForSelector('#app.ready', { timeout: 5000 });
    if (withMraid) {
      const early = await page.evaluate('window.__early');
      if (early.length) fail(`called MRAID before ready: ${early.join(', ')}`);
    }

    // nothing moves on its own before the first touch (the hint hand is the only motion)
    const before = await page.evaluate(`[document.getElementById('step').textContent, document.getElementById('app').className]`);
    await page.waitForTimeout(3000);
    const after = await page.evaluate(`[document.getElementById('step').textContent, document.getElementById('app').className]`);
    if (before[0] !== after[0]) fail(`advanced by itself: "${before[0]}" → "${after[0]}"`);
    if (/touched|result|analyzing/.test(after[1])) fail(`changed state by itself: ${after[1]}`);
    if ((await page.evaluate('window.__opened')).length) fail('opened the click URL before any touch');

    const W = view.w, H = view.h;
    const PLAY = ['#brand', '#step', '#q', '#dots', '#cardA', '#cardB'];
    const boxes = await onScreen(page, [...PLAY, '#hint'], W, H, 'play', fail, ['#q', '#step', '#hint']);
    const by = Object.fromEntries(boxes.map((b) => [b.s, b]));
    if (overlap(by['#q'], by['#cardA']) || overlap(by['#q'], by['#cardB'])) fail('play: the question overlaps a look');
    for (const s of ['#cardA', '#cardB']) if (by[s] && Math.min(by[s].w, by[s].h) < 100) fail(`play: ${s} is only ${Math.round(by[s].w)}×${Math.round(by[s].h)} px`);
    await readable(page, ['#step', '#q', '#hint', '#disc'], 'play', fail);
    await shot('play');

    // first tap plays, never opens
    await tapAt(page, '#cardA');
    await page.waitForTimeout(350);
    if ((await page.evaluate('window.__opened')).length) fail('the first tap opened the click URL');
    if (!(await page.$('#app.touched'))) fail('the first tap did not register as the first interaction');
    await onScreen(page, ['#mini'], W, H, 'play after touch', fail, ['#mini']);
    await page.waitForTimeout(150);
    await readable(page, ['#mini'], 'play after touch', fail);

    for (let n = 2; n <= rounds; n++) {
      const want = String(cfg.text.step).replace('{n}', n).replace('{total}', rounds);
      try {
        await page.waitForFunction((t) => document.getElementById('step').textContent === t && document.getElementById('arena').className === '', want, { timeout: 3000 });
      } catch (e) { fail(`round ${n} did not come up after a tap`); break; }
      if (n === 2) {
        // rotate mid-play: the round must re-fit
        await page.setViewportSize({ width: H, height: W });
        await page.waitForTimeout(400);
        await onScreen(page, PLAY, H, W, 'play after rotating', fail, ['#q']);
        await page.setViewportSize({ width: W, height: H });
        await page.waitForTimeout(400);
      }
      await tapAt(page, n % 2 ? '#cardA' : '#cardB');
    }
    try { await page.waitForSelector('#app.result', { timeout: 5000 }); } catch (e) { fail('no result screen after the last round'); }
    await page.waitForTimeout(700);
    const RES = ['#rKicker', '#rStyle', '#fan', '#rLine', '#cta'];
    const rb = Object.fromEntries((await onScreen(page, RES, W, H, 'result', fail, ['#rStyle', '#rLine', '#cta'])).map((b) => [b.s, b]));
    if (rb['#cta'] && rb['#cta'].h < 40) fail(`result: the CTA is only ${Math.round(rb['#cta'].h)} px tall`);
    await inColumn(page, 'result', fail);
    await readable(page, ['#rKicker', '#rStyle', '#rLine', '#chip', '#cta', '#disc'], 'result', fail);
    const fanImgs = await page.evaluate(`[...document.querySelectorAll('#fan img')].map((i) => { const r = i.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })`);
    if (!fanImgs.length) fail('result: no picks shown');
    for (const [l, t, r, b] of fanImgs) if (l < -1 || t < -1 || r > W + 1 || b > H + 1) fail('result: a pick leaves the screen');
    if (overlap(rb['#fan'], rb['#cta'])) fail('result: the picks overlap the CTA');
    await shot('result');

    await tapAt(page, '#cta');
    await page.waitForTimeout(300);
    const opened = await page.evaluate('window.__opened');
    if (withMraid) {
      if (opened.length !== 1 || opened[0] !== cfg.clickUrl) fail(`CTA: expected mraid.open("${cfg.clickUrl}") once, got ${JSON.stringify(opened)}`);
    } else if (!(await page.$('#app.toasting'))) fail('preview CTA did not show the toast');

    await page.setViewportSize({ width: H, height: W });
    await page.waitForTimeout(400);
    await onScreen(page, RES, H, W, 'result after rotating', fail, ['#rStyle', '#cta']);
    await inColumn(page, 'result after rotating', fail);
    await shot('result-rotated');

    if (withMraid) {
      await page.evaluate(`window.__viewable = false; window.__fire('viewableChange', false)`);
      if (!(await page.$('#app.paused'))) fail('viewableChange(false) did not pause');
      await page.evaluate(`window.__viewable = true; window.__fire('viewableChange', true)`);
      if (await page.$('#app.paused')) fail('viewableChange(true) did not resume');
    }
  } catch (e) {
    fail(`crashed: ${e.message.split('\n')[0]}`);
  }
  for (const u of outside) fail(`asked for something outside the file: ${u.slice(0, 120)}`);
  for (const e of errors) fail(e.slice(0, 200));
  await ctx.close();
  return { tag, fails };
}

(async () => {
  const args = process.argv.slice(2);
  let shots = '';
  const paths = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--shots') { shots = path.resolve(args[++i] || 'playable-shots'); continue; }
    paths.push(args[i]);
  }
  const files = paths.length
    ? paths.map((p) => ({ name: path.basename(p, '.html'), html: fs.readFileSync(p, 'utf8') }))
    : fixtures();
  const browser = await chromium.launch();
  let bad = 0;
  for (const file of files) {
    const st = lib ? lib.check(file.html, { clickUrl: cfgOf(file.html).clickUrl }) : [];
    if (st.length) { bad++; console.log(`✗ ${file.name} static: ${st.join(' | ')}`); }
    for (const view of VIEWS) {
      for (const withMraid of view.modes) {
        const r = await run(browser, file, view, withMraid, shots);
        if (r.fails.length) { bad++; console.log(`✗ ${r.tag}\n    ${r.fails.join('\n    ')}`); } else console.log(`✓ ${r.tag}`);
      }
    }
  }
  await browser.close();
  console.log(bad ? `${bad} run(s) failed` : 'all runs passed');
  process.exit(bad ? 1 : 0);
})();
