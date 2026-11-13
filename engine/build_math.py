"""Math-route split of an auto-loc render body: erase zones for the local plate + overlay-only bodies.
usage: python3 build_math.py RB.json SRC_URL OUT_PREFIX CFG.json
 -> OUT_PREFIX.black.json / .white.json (overlay over solid black / white) + OUT_PREFIX.zones.json"""
import json, re, sys
rb = json.load(open(sys.argv[1])); SRC = sys.argv[2]; OUT = sys.argv[3]
els = rb['elements']; D = rb['duration']
cfg = json.load(open(sys.argv[4])) if len(sys.argv) > 4 else {}


def tracks_of(sel, key):
    """A cfg layer reference -> the top-level track numbers it means in THIS body.
    Track numbers differ between two builds of the same video (any added or dropped band shifts every layer after it),
    so a cfg written for one build should name its layers instead:
      int                                  the track number itself (only safe for the body the cfg was measured on)
      "transcript"                         the text layer that carries a transcript (the captions)
      {"name": "…"}                        layers with exactly this name
      {"text": "…", "time": t, "named": b} text layers whose text starts with "…"; optional: starts at t (±0.05 s),
                                           has a name (true) / has none (false)"""
    if isinstance(sel, bool): raise SystemExit(f'cfg {key}: {sel!r} is not a layer reference')
    if isinstance(sel, int): return [sel]
    if isinstance(sel, str) and sel.isdigit(): return [int(sel)]
    if sel == 'transcript': hit = [e for e in els if e.get('type') == 'text' and e.get('transcript_source')]
    elif isinstance(sel, dict) and 'name' in sel: hit = [e for e in els if e.get('name') == sel['name']]
    elif isinstance(sel, dict) and 'text' in sel:
        hit = [e for e in els if e.get('type') == 'text' and str(e.get('text') or '').strip().startswith(sel['text'])
               and ('time' not in sel or abs(float(e.get('time') or 0) - float(sel['time'])) <= 0.05)
               and ('named' not in sel or bool(e.get('name')) == bool(sel['named']))]
    else: raise SystemExit(f'cfg {key}: unknown layer reference {sel!r}')
    out = sorted({e.get('track') for e in hit})
    if not out: raise SystemExit(f'cfg {key}: no layer matches {sel!r} in this render body')
    return out


def one_track(sel, key):
    t = tracks_of(sel, key)
    if len(t) != 1: raise SystemExit(f'cfg {key}: {sel!r} matches {len(t)} tracks {t}, expected one')
    return t[0]


def many_tracks(sels, key):
    return sorted({t for s in (sels if isinstance(sels, list) else [sels]) for t in tracks_of(s, key)})


# every layer reference in the cfg becomes this body's track numbers before anything reads it
for k in ('drop_text_tracks', 'drop_band_tracks'):
    if cfg.get(k): cfg[k] = many_tracks(cfg[k], k)
if isinstance(cfg.get('dark_text'), (dict, list)):
    for w in ([cfg['dark_text']] if isinstance(cfg['dark_text'], dict) else cfg['dark_text']):
        w['tracks'] = many_tracks(w['tracks'], 'dark_text.tracks')
if cfg.get('caption_text_track') is not None: cfg['caption_text_track'] = one_track(cfg['caption_text_track'], 'caption_text_track')
if cfg.get('caption_pages'): cfg['caption_pages']['track'] = one_track(cfg['caption_pages']['track'], 'caption_pages.track')
for k in ('text_set', 'text_y_keys'):          # keyed by track: a key may be a number or a JSON layer reference
    if cfg.get(k):
        cfg[k] = {str(one_track(json.loads(s) if s[:1] in '{"' else s, k)): v for s, v in cfg[k].items()}
print('cfg layers:', {k: cfg[k] for k in ('drop_text_tracks', 'drop_band_tracks', 'caption_text_track') if cfg.get(k) is not None},
      'caption_pages.track', (cfg.get('caption_pages') or {}).get('track'))


def r2(x): return round(float(x) * 100) / 100
def r1(x): return round(float(x) * 10) / 10
def bbox(path):
    n = [float(v) for v in re.findall(r'-?\d+\.?\d*', path)]
    xs, ys = n[0::2], n[1::2]
    return min(xs), min(ys), max(xs), max(ys)
def patch(t, d, x0, y0, x1, y1, feather='1.2 vmin', radius='2 vmin'):
    return {"type": "shape", "time": r2(t), "duration": r2(d),
            "x": f"{r1((x0+x1)/2)}%", "y": f"{r1((y0+y1)/2)}%", "width": f"{r1(x1-x0)}%", "height": f"{r1(y1-y0)}%",
            "path": "M 0 0 L 100 0 L 100 100 L 0 100 Z", "border_radius": radius, "fill_color": "#fff", "blur": feather}
def layer(track, blur, overlay, shapes):
    # a mask comp with no active child is skipped by Creatomate and the layer shows UNMASKED (whole frame blurred,
    # seen on render ad6da2d2): keep one invisible 1 % corner square alive for the whole duration
    keeper = {"type": "shape", "track": 1, "time": 0, "duration": D, "x": "0.5%", "y": "0.5%", "width": "1%", "height": "1%",
              "path": "M 0 0 L 100 0 L 100 100 L 0 100 Z", "fill_color": "rgba(255,255,255,0.01)"}
    shapes = [keeper] + [dict({"type": "shape", "track": i + 2}, **{k: v for k, v in sh.items() if k != 'type'}) for i, sh in enumerate(shapes)]
    return {"type": "composition", "track": track, "time": 0, "duration": D, "elements": [
        {"type": "video", "track": 1, "time": 0, "source": SRC, "duration": D, "volume": "0%", "fit": "cover", "blur": blur, "color_overlay": overlay},
        {"type": "composition", "track": 2, "time": 0, "duration": D, "mask_mode": "alpha", "elements": shapes}]}
def clean_text(e):
    return {k: v for k, v in e.items() if v is not None}
dark, light, boxes, texts, app, keep = [], [], [], [], [], []
plates = []
for e in els:
    tr, ty = e.get('track'), e.get('type')
    if ty == 'audio': keep.append(e); continue
    if e.get('name') == 'clean' or (e.get('name') or '').startswith('surround'): continue
    if (e.get('name') or '').startswith('pad'):
        if cfg.get('caption_rects'): continue                     # blur: measured old-caption rects instead
        sh = e['elements'][1]['elements'][0]; x0, y0, x1, y1 = bbox(sh['path'])
        x0, y0, x1, y1 = x0 - 1.0, y0 - 0.6, x1 + 1.0, y1 + 0.6   # wider than the remover pad: the 1.2 vmin feather eats the rim
        if cfg.get('pad_clip'):                                     # remover pads (R1 regions) are far taller than the caption:
            cx0, cy0, cx1, cy1 = cfg['pad_clip']                    # a blur slab that big shows
            x0, y0, x1, y1 = max(x0, cx0), max(y0, cy0), min(x1, cx1), min(y1, cy1)
        dark.append(patch(e['time'], e['duration'], x0, y0, x1, y1)); continue
    if (e.get('name') or '').startswith('patch sticker'): continue          # graphic card: keep the original
    if (e.get('name') or '').startswith('app page'):
        if any(r[6:] == ['B'] for r in cfg.get('caption_rects', [])): continue   # white app covers: the black box stays instead
        app.append(e); continue
    if ty == 'composition' and len(e.get('elements', [])) == 3 and str(e['elements'][2].get('fill_color', '')).startswith('rgba(0,0,0'):
        b = e['elements'][2]                                                   # caption plate: blurred source + dark box
        plates.append((e['time'], e['duration'], b))
        boxes.append({"type": "shape", "time": e['time'], "duration": e['duration'], "x": b['x'], "y": b['y'],
                      "width": b['width'], "height": b['height'], "path": "M 0 0 L 100 0 L 100 100 L 0 100 Z",
                      "border_radius": b.get('border_radius', '1.92 vmin'), "fill_color": b['fill_color']})
        continue
    if ty == 'composition' and e['elements'] and e['elements'][0].get('type') == 'video' and tr != 24:
        v = e['elements'][0]
        if v['source'] == SRC and tr != 24: keep.append(e); continue
        # remover / clean-copy band (app screen edges, packshot, frozen plate): blurred live source instead
        v = dict(v, source=SRC, trim_start=e['time'], blur=v.get('blur', '3 vmin')); v.pop('speed', None)
        v['trim_duration'] = e['duration']; v['duration'] = e['duration']
        if cfg.get('drop_band_tracks') and tr in cfg['drop_band_tracks']: continue
        app.append(dict(e, elements=[v] + e['elements'][1:])); continue
    # (the v6 builder dropped the grey LOC12 pill by its old track number 24; here every frosted band is dropped below)
    dt = [w for w in ([cfg['dark_text']] if isinstance(cfg.get('dark_text'), dict) else cfg.get('dark_text', [])) if tr in w['tracks']]
    if ty == 'text' and dt:
        a, b = dt[0]['from'], dt[0]['to']   # disclaimer over a white app screen / cream scene: grey letters, no shadow
        cfg_dt = dt[0]
        t0, t1 = e['time'], e['time'] + e['duration']
        if t0 < b and t1 > a:
            parts = []
            if t0 < a: parts.append(dict(clean_text(e), duration=round(a - t0, 3)))
            parts.append(dict(clean_text(e), time=round(max(t0, a), 3), duration=round(min(t1, b) - max(t0, a), 3),
                              fill_color=cfg_dt['color'], shadow_color='rgba(0,0,0,0)'))
            if t1 > b: parts.append(dict(clean_text(e), time=round(b, 3), duration=round(t1 - b, 3)))
            texts.extend(parts); continue
    if ty == 'text':
        if tr in cfg.get('drop_text_tracks', []): continue
        texts.append(dict(clean_text(e), **cfg.get('text_set', {}).get(str(tr), {}))); continue
    keep.append(e)
# ---- math route: the old old text is erased in a local "plate" (maskfill.py), so this body keeps only
# what goes ON TOP of the picture (new text, boxes, kept stickers, app bands). It is rendered twice, over solid black
# and over solid white, and difference matting gives its alpha: a = 1 - (W - B) / 255; final = plate * (1 - a) + B.
W_, H_ = rb['width'], rb['height']
def px(x0, y0, x1, y1): return [round(x0 * W_ / 100), round(y0 * H_ / 100), round(x1 * W_ / 100), round(y1 * H_ / 100)]
Z = []      # erase zones: [t0, t1, x0, y0, x1, y1 (px), mode, k, thr, grow]
PW = sorted((q['time'], q['time'] + q['duration']) for q in boxes)
def split(t0, t1):
    cuts = sorted({t0, t1} | {x for a, c in PW for x in (a, c) if t0 < x < t1})
    for a, c in zip(cuts, cuts[1:]):
        m = (a + c) / 2; yield a, c, any(p0 <= m < p1 for p0, p1 in PW)
fills = []
def pct(q): return [float(str(q[k]).rstrip('%')) for k in ('x', 'y', 'width', 'height')]
if cfg.get('caption_union'):
    # the source's captions are opaque black boxes: each new page becomes ONE opaque black box = union of its own
    # box and the old box(es) on screen (cut where the old box changes), and the new line is centred in it; so no
    # old box edge shows and nothing needs erasing there. Old boxes with no new page on screen are erased.
    OLD = [(t, t + d, x0, y0, x1, y1) for t, d, x0, y0, x1, y1, *_ in cfg['caption_rects']]
    R = cfg.get('union_radius', '1.4 vmin'); pieces = []
    for q in boxes:
        q0, q1 = q['time'], q['time'] + q['duration']; x, y, w, h = pct(q)
        cuts = sorted({q0, q1} | {v for a, c, *_ in OLD for v in (a, c) if q0 < v < q1})
        for a, c in zip(cuts, cuts[1:]):
            if c - a < 0.02: continue
            m = (a + c) / 2; X0, Y0, X1, Y1 = x - w / 2, y - h / 2, x + w / 2, y + h / 2
            for o0, o1, ox0, oy0, ox1, oy1 in OLD:
                if o0 <= m < o1: X0, Y0, X1, Y1 = min(X0, ox0 - 0.3), min(Y0, oy0 - 0.2), max(X1, ox1 + 0.3), max(Y1, oy1 + 0.2)
            pieces.append(dict(q, time=round(a, 3), duration=round(c - a, 3), x=f'{(X0 + X1) / 2:.2f}%', y=f'{(Y0 + Y1) / 2:.2f}%',
                               width=f'{X1 - X0:.2f}%', height=f'{Y1 - Y0:.2f}%', fill_color='#000000', border_radius=R))
    boxes[:] = pieces
    PW = sorted((q['time'], q['time'] + q['duration']) for q in boxes)
    for t0, t1, x0, y0, x1, y1 in OLD:
        for a, c, on in split(t0, t1):
            if not on and c - a >= 0.02: Z.append([a, c] + px(x0 - 0.6, y0 - 0.3, x1 + 0.6, y1 + 0.3) + ['full', 0, 0, 3])
    TT = cfg.get('caption_text_track')
    for e in texts:
        if TT is None or e.get('track') != TT: continue
        T = e['time']; ky = []
        for q in sorted(boxes, key=lambda q: q['time']):
            a = max(0, round(q['time'] - T, 3)); c = round(q['time'] + q['duration'] - T - 0.001, 3)
            ky += [{'time': a, 'value': q['y']}, {'time': c, 'value': q['y']}]
        e['y'] = ky
CP = cfg.get('caption_pages')
if CP:
    # «same words -> same box»: the recipe's
    # transcript layer pages itself and its per-page boxes were unioned with the old boxes, so a box changed whenever
    # the old page under it changed. Here every new page is its own text layer + ONE box: the box height and y
    # are fixed for the whole video (cfg box [y0, y1] %, set to cover the old one-line boxes), its width = the page's
    # text (measured with the real font) + padding, or the widest old box on screen during the page if that is wider.
    # An old box taller than the fixed box (two-line old pages) is erased whole in the plate (soft blur shows
    # where it stuck out); an old box with no new page on screen is erased too.
    from PIL import ImageFont
    def font_path(spec):
        """'Montserrat:800' -> a cached TTF from Google Fonts (the family Creatomate renders with); a path is used as is."""
        import os, urllib.request
        if os.path.exists(spec): return spec
        fam, wt = spec.split(':'); out = os.path.expanduser(f'~/.cache/loc-math/{fam}-{wt}.ttf')
        if not os.path.exists(out):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            css = urllib.request.urlopen(urllib.request.Request(f'https://fonts.googleapis.com/css2?family={fam}:wght@{wt}',
                                         headers={'User-Agent': 'Mozilla/4.0'})).read().decode()     # old UA -> TTF urls
            urllib.request.urlretrieve(re.search(r'url\((https://[^)]+\.ttf)\)', css).group(1), out)
        return out
    CP['font_file'] = font_path(CP.get('font_file') or CP['font'])
    src_t = [e for e in texts if e.get('track') == CP['track']]
    assert len(src_t) == 1, 'caption_pages: no single transcript layer on track %s' % CP['track']
    e = src_t[0]; texts.remove(e)
    ws = e['transcript_source']; ws = json.loads(ws) if isinstance(ws, str) else ws
    T0, TD = float(e['time']), float(e['duration']); ML = int(e.get('transcript_maximum_length', 14))
    pages, cur = [], []
    for w in ws:
        val = w['value'].strip()
        if not val: continue
        if cur and (sum(len(x['value'].strip()) for x in cur) + len(val) > ML or cur[-1]['value'].strip()[-1] in '.,?!;:'):
            pages.append(cur); cur = []
        cur.append(w)
    if cur: pages.append(cur)
    # no page ends on a short function word when the next page can take it 
    DANGLE = set(CP.get('dangle', 'a al de del el la las los lo le y e o u en mi mis tu tus su sus un una que con por para me te se'.split()))
    nch = lambda pg: sum(len(x['value'].strip()) for x in pg)
    for i in [j for _ in range(3) for j in range(len(pages) - 1)]:
        while len(pages[i]) > 1 and pages[i][-1]['value'].strip().lower() in DANGLE and nch(pages[i + 1]) + len(pages[i][-1]['value'].strip()) <= ML:
            pages[i + 1].insert(0, pages[i].pop())
    font = ImageFont.truetype(CP['font_file'], 100)
    txt = [' '.join(x['value'].strip() for x in pg).upper() for pg in pages]
    vmin = min(W_, H_) / 100
    fs = float(str(e.get('font_size_maximum', '5 vmin')).split()[0]) * vmin
    padx = CP.get('pad_x_em', 0.6)
    widest = max(font.getlength(t) for t in txt) / 100
    fs = min(fs, (CP.get('max_width', 88) / 100 * W_) / (widest + 2 * padx))
    Y0, Y1 = CP['box']; YC = (Y0 + Y1) / 2
    OLD = [r for r in cfg.get('caption_rects', []) if CP.get('old_boxes', 'B') == 'all' or r[6:] == ['B']]
    P = [{'a': T0 + float(pg[0]['time']), 'c': T0 + float(pages[i + 1][0]['time']) if i + 1 < len(pages) else T0 + TD,
          'txt': txt[i], 'y0': Y0, 'y1': Y1} for i, pg in enumerate(pages)]
    # «two_lines»: where the old box had two lines (taller than
    # the fixed box), erasing its lower half left a blur strip under the new box. Instead the new pages on screen
    # during that old box become ONE two-line page whose box covers the old one, as the original creative did. The
    # shape still changes only with the words; no pixel under it is erased.
    covered = []
    if CP.get('tall_old') == 'two_lines':
        TW = sorted([t, t + d, y0, y1] for t, d, x0, y0, x1, y1, *_ in OLD if y0 < Y0 - 0.05 or y1 > Y1 + 0.05)
        TM = []
        for w in TW:
            if TM and w[0] <= TM[-1][1] + 0.1: TM[-1] = [TM[-1][0], max(TM[-1][1], w[1]), min(TM[-1][2], w[2]), max(TM[-1][3], w[3])]
            else: TM.append(list(w))
        for ta, tb, wy0, wy1 in TM:
            ix = [i for i, q in enumerate(P) if min(q['c'], tb) - max(q['a'], ta) >= min(0.35, 0.5 * (q['c'] - q['a'])) - 1e-6]
            if not ix: continue
            i0, i1 = ix[0], ix[-1]; grp = P[i0:i1 + 1]
            words = ' '.join(q['txt'] for q in grp).split()
            if len(grp) == 2: l1, l2 = grp[0]['txt'], grp[1]['txt']
            else:
                cut = min(range(1, len(words)), key=lambda j: max(font.getlength(' '.join(words[:j])), font.getlength(' '.join(words[j:])))) if len(words) > 1 else 1
                l1, l2 = ' '.join(words[:cut]), ' '.join(words[cut:])
            m = {'a': min(grp[0]['a'], ta), 'c': min(max(grp[-1]['c'], tb), T0 + TD), 'txt': (l1 + '\n' + l2).strip(),   # never past the transcript
                 'y0': min(Y0, wy0 - 0.15), 'y1': max(Y1, wy1 + 0.15)}
            if i0 > 0: P[i0 - 1]['c'] = m['a']
            if i1 + 1 < len(P) and P[i1 + 1]['a'] < m['c']: P[i1 + 1]['a'] = m['c']
            P[i0:i1 + 1] = [m]; covered.append((ta, tb))
            print('  two_lines %.2f-%.2f -> %r %.2f-%.2f' % (ta, tb, m['txt'], m['a'], m['c']))
        P = [q for q in P if q['c'] - q['a'] > 0.02]
    # an old box that shows up to 0.25 s before the next new page, with no page on,
    # was erased bare on those frames = a one-frame blur blob on the floor: start that page with the old box instead
    for t, d, *_ in OLD:
        nx = [q for q in P if t < q['a'] <= t + 0.25]
        if nx and not any(q['a'] <= t < q['c'] for q in P):
            q = min(nx, key=lambda q: q['a']); print('  snap %.2f -> %.2f %r' % (q['a'], t, q['txt'])); q['a'] = t
    newb, PG = [], []
    for q in P:
        a, c = q['a'], q['c']
        tw = max(font.getlength(l) for l in q['txt'].split('\n')) / 100 * fs
        half = (tw / 2 + padx * fs) / W_ * 100
        for t, d, x0, y0, x1, y1, *_ in OLD:
            if min(c, t + d) - max(a, t) > 0.02: half = max(half, 50 - x0 + 0.3, x1 - 50 + 0.3)
        half = min(half, 49.5); q['half'] = half
        PG.append((a, c))
        newb.append({"type": "shape", "time": round(a, 3), "duration": round(c - a, 3), "x": "50%", "y": f"{(q['y0'] + q['y1']) / 2:.2f}%",
                     "width": f"{2 * half:.2f}%", "height": f"{q['y1'] - q['y0']:.2f}%", "path": "M 0 0 L 100 0 L 100 100 L 0 100 Z",
                     "border_radius": CP.get('radius', '0 vmin'), "fill_color": CP.get('fill', '#000000')})
        t_el = {k: v for k, v in e.items() if not k.startswith('transcript') and k not in ('font_size_minimum', 'font_size_maximum', 'y', 'width', 'height', 'time', 'duration', 'track')}
        texts.append(dict(t_el, track=CP['track'], text=q['txt'], time=round(a, 3), duration=round(c - a, 3), x='50%',
                          y=f"{(q['y0'] + q['y1']) / 2:.2f}%", width='98%', height=f"{q['y1'] - q['y0']:.2f}%", font_size=f"{fs / vmin:.2f} vmin"))
    boxes[:] = newb; txt = [q['txt'] for q in P]
    rad = float(str(CP.get('radius', '0 vmin')).split()[0]) * vmin
    CAPS = [{'t0': round(q['a'], 3), 't1': round(q['c'], 3), 'text': q['txt'], 'font': CP['font_file'], 'size': round(fs, 2),
             'box': [round(W_ / 2 - q['half'] / 100 * W_, 1), round(q['y0'] * H_ / 100, 1), round(W_ / 2 + q['half'] / 100 * W_, 1), round(q['y1'] * H_ / 100, 1)],
             'radius': round(rad, 1), 'fill': [0, 0, 0], 'color': [255, 255, 255]} for q in P]
    json.dump({'pages': CAPS, 'clear': []}, open(f'{OUT}.captions.json', 'w'), ensure_ascii=False, indent=0)
    if CP.get('local'):      # composite.py draws them (exact size, real font): keep them out of the Creatomate bodies
        boxes[:] = []; texts[:] = [q for q in texts if not (q.get('track') == CP['track'] and q.get('text') in txt)]
    PW = sorted(PG)
    for t, d, x0, y0, x1, y1, *k in OLD:
        tall = (y0 < Y0 - 0.05 or y1 > Y1 + 0.05) and not any(ta - 0.01 <= t and t + d <= tb + 0.01 for ta, tb in covered)
        for a, c, on in split(t, t + d):
            if c - a >= 0.02 and (tall or not on): Z.append([a, c] + px(x0 - 0.6, y0 - 0.3, x1 + 0.6, y1 + 0.3) + ['full', 0, 0, 3])
    print('caption_pages:', len(P), 'pages, font %.2f vmin' % (fs / vmin), '| widest page %.1f %%' % (max(float(b['width'][:-1]) for b in newb)))
    for i, b in enumerate(newb[:200]): print('  %6.2f-%6.2f %-34s w %s h %s' % (b['time'], b['time'] + b['duration'], txt[i].replace(chr(10), ' / '), b['width'], b['height']))
for tr_, keys in cfg.get('text_y_keys', {}).items():      # a text that rides a moving element
    for q in texts:
        if str(q.get('track')) == tr_:
            q['y'] = [{'time': round(t - float(q['time']), 3), 'value': f'{y:.2f}%'} for t, y in keys if float(q['time']) <= t <= float(q['time']) + float(q['duration'])]
Z.extend(cfg.get('zones_px', []))      # explicit zones in px (e.g. a per-frame track of a moving button)
for t, d, x0, y0, x1, y1, mode, k, thr, grow, *lmin in cfg.get('zones_extra', []):   # explicit zones, % of frame (+ optional lmin, static)
    Z.append([t, t + d] + px(x0, y0, x1, y1) + [mode, k, thr, grow] + lmin[:2])
for t, d, x0, y0, x1, y1, *k in ([] if cfg.get('caption_union') else cfg.get('caption_rects', [])):
    if CP and (CP.get('old_boxes', 'B') == 'all' or k == ['B']): continue          # caption_pages handled it
    if k == ['B']:
        for a, c, on in split(t, t + d):
            if c - a < 0.02: continue
            if on: fills.append(dict(patch(a, c - a, x0, y0, x1, y1, feather='0 vmin', radius='0 vmin'), fill_color='#000000'))
            else: Z.append([a, c] + px(x0 - 0.6, y0 - 0.3, x1 + 0.6, y1 + 0.3) + ['full', 0, 0, 3])   # old black box + letters
        continue
    # a tall T rect is an old translucent plate with its text (segs.py): erase the plate too, not only the letters
    Z.append([t, t + d] + px(x0, y0, x1, y1) + (['full', 0, 0, 3] if (y1 - y0) > 7 else ['light', 25, 70, 4]))
for p in cfg.get('dark_patches', []):
    t, d, x0, y0, x1, y1 = p[:6]
    big = (y1 - y0) > 8                      # an end-card zone (TAP THE LINK + glow): thick strokes, soft halo
    Z.append([t, t + d] + px(x0, y0, x1, y1) + (['light', 61, 60, 16, 60] if big else ['light', 31, 70, 5]))   # end card: dark plain bg, so lmin 60 also catches the letters while they fade in
for p in cfg.get('light_patches', []):
    t, d, x0, y0, x1, y1 = p[:6]
    Z.append([t, t + d] + px(x0, y0, x1, y1) + ['dark', 31, 50, 3])
DB = cfg.get('disc_box', [8.5, 79.8, 91.5, 83.7])
for t, d, *_ in cfg.get('disc_segments', []):
    Z.append([t, t + d] + px(*DB) + ['light', 15, 70, 3])
for t, d, x0, y0, x1, y1, col, fe in cfg.get('solid_patches', []):
    fills.append(dict(patch(t, d, x0, y0, x1, y1, feather=fe, radius='0.6 vmin'), fill_color=col))
BW = [(r[0], r[0] + r[1]) for r in cfg.get('caption_rects', []) if r[6:] == ['B']]
BS = cfg.get('box_style', {})
for q in boxes:
    t0, t1 = q['time'], q['time'] + q['duration']
    ov = sum(max(0, min(t1, b1) - max(t0, b0)) for b0, b1 in BW)
    if BS and not CP and ov >= 0.5 * (t1 - t0): q.update(fill_color='#000000', border_radius='0 vmin', y=BS['y'], height=BS['height'])
# the plate already erased the old text: drop every frosted / remover band (app bands, LOC12 disclaimer band, white app
# haze) so no blurred original or regenerated pixel lands on top of it; keep audio, original stickers and plain shapes
dropped = [(e.get('track'), e.get('time'), e.get('duration')) for e in app] + \
          [(e.get('track'), e.get('time'), e.get('duration')) for e in keep if e.get('type') in ('composition', 'video')
           and not (e.get('name') or '').startswith('orig ')]
keep = [e for e in keep if e.get('type') not in ('composition', 'video') or (e.get('name') or '').startswith('orig ')]
app = []
print('dropped bands (track, time, dur):', dropped)
def body(bg):
    out_els = [e for e in keep if e.get('type') == 'audio']
    out_els.append({"type": "shape", "track": 2, "time": 0, "duration": D, "x": "50%", "y": "50%", "width": "100%",
                    "height": "100%", "path": "M 0 0 L 100 0 L 100 100 L 0 100 Z", "fill_color": bg})
    tr = 3
    for e in app: out_els.append(dict(e, track=tr)); tr += 1
    if fills + boxes: out_els.append({"type": "composition", "track": tr, "time": 0, "duration": D, "elements": fills + boxes}); tr += 1
    for e in [k for k in keep if k.get('type') != 'audio'] + texts:
        out_els.append(dict(e, track=tr)); tr += 1
    b = {k: v for k, v in rb.items() if k != 'elements'}; b['elements'] = out_els
    return b
for bg, tag in (('#000000', 'black'), ('#ffffff', 'white')):
    json.dump(body(bg), open(f'{OUT}.{tag}.json', 'w'), ensure_ascii=False, separators=(',', ':'))
# static text: white letters over a light shot have too small a
# top-hat in some frames and survived. Text is static per zone window, so plate.py erases the pixels that are letters in
# >= static_share of the window's frames in every frame of it (zone field 12; zones_extra may set its own).
SS = cfg.get('static_share', 0)
for z in Z if SS else []:
    if z[6] != 'full' and z[1] - z[0] >= 0.5 and len(z) <= 11:
        if len(z) == 10: z.append(150)
        z.append(SS)
json.dump({'src': SRC, 'w': W_, 'h': H_, 'fps': rb.get('frame_rate', 30), 'duration': D, 'zones': Z},
          open(f'{OUT}.zones.json', 'w'), indent=0)
print(OUT, 'zones', len(Z), 'app', len(app), 'fills', len(fills), 'boxes', len(boxes), 'texts', len(texts), 'keep', len(keep))
