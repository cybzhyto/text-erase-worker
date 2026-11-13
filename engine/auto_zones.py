"""Build the erase cfg for build_math.py from the caller's own text analysis of THIS build, measured on the source.

A hand cfg fits only the render body it was measured on: two builds of one video can place things differently (a price
tag lifted off the legal line in one, kept as source pixels in the next). So the zones come from the run itself:

    python3 auto_zones.py AUTO.json RB.json SRC.mp4 CFG_OUT.json

AUTO.json (written by the caller next to the render body):
    {"overlays": [{role, start, end, box: {ymin, xmin, ymax, xmax} (per mille), text}],   source text blocks + times
     "groups":   [{t, role, text, box (per mille), lines, color, plate, surround, freeSpot?}],   exact reads on stills
     "cuts":     [{from, to}],                                                             shot changes
     "scenes":   [{start, end, type}],                                                     optional
     "covered":  [{start, end, how, band [y0, y1], cols [x0, x1] (per mille), …}]}         optional: what the build covers

What each role gets (roles the build keeps as source pixels get no zone):
  subtitle   -> caption_rects measured frame by frame inside the caption rows (a pure black box = "B", a taller box or
                bare light letters' bbox = "T"); a boxed source also gets caption_pages (one fixed box height, same words
                -> same box; old_boxes "all" only when no bare-letter page was seen)
  disclaimer -> one zone per shot over the read box; light / dark from the pixels, threshold measured per shot
  price      -> kept, except a tag the build lifted to a free spot ("tag lifted …" layer): full erase of its old box
  logo, ui, sticker -> kept, and other zones are clipped so they never cover them
  anything else (headline, cta, text …) -> one zone per shot over the union of its read boxes and their motion path
                (+ a slide margin on end cards), mode / kernel / threshold / brightness gate measured on the source;
                white letters partly over a light background get a second near-white zone
README › Automatic zones has the measures and why.
Every zone of a static text gets static_share 0.3 (plate.py). The result goes through build_math.py as any cfg."""
import json, subprocess, sys
import numpy as np, cv2

KEEP = {'price', 'logo', 'ui', 'sticker', 'app_ui', 'watermark'}
CAPTION = {'subtitle', 'caption'}
FPS_M = 4            # frames per second sampled for the zone measures
FPS_C = 10           # frames per second sampled for the caption measures
TOPHAT_C = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))   # caption letters vs their surround


def load_gray(src, fps, w, h, rows=None):
    """Decode the source at `fps` as gray frames (optionally only rows [r0, r1)): list of (t, frame)."""
    r0, r1 = rows or (0, h)
    vf = f'fps={fps},format=gray,crop={w}:{r1 - r0}:0:{r0}'      # format first: a yuv420 crop rounds an odd height down
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', src, '-vf', vf, '-f', 'rawvideo', '-pix_fmt', 'gray', '-'],
                         stdout=subprocess.PIPE)
    n = w * (r1 - r0); out = []; i = 0
    while True:
        b = p.stdout.read(n)
        if len(b) < n: break
        out.append((i / fps, np.frombuffer(b, np.uint8).reshape(r1 - r0, w))); i += 1
    p.wait()
    return out


def load_rgb(src, fps, w, h, rows):
    r0, r1 = rows
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', src, '-vf', f'fps={fps},format=rgb24,crop={w}:{r1 - r0}:0:{r0}', '-f', 'rawvideo',
                          '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)
    n = w * (r1 - r0) * 3; out = []; i = 0
    while True:
        b = p.stdout.read(n)
        if len(b) < n: break
        out.append((i / fps, np.frombuffer(b, np.uint8).reshape(r1 - r0, w, 3).astype(np.int16))); i += 1
    p.wait()
    return out


def runs1d(v, th, gap=3):
    """[(a, b)] index runs where v >= th, gaps <= gap bridged."""
    idx = np.nonzero(v >= th)[0]; out = []
    for i in idx:
        if out and i - out[-1][1] <= gap + 1: out[-1][1] = i
        else: out.append([i, i])
    return out
def find_box(f, W):
    """Black caption box in an RGB crop: [x0, y0, x1, y1] (crop coords) or None. A box = a rectangle of pure black
    (max channel <= 3) that stays black in most of its rows / columns (letters cover < half), with a straight outline."""
    B = (f.max(2) <= 3)
    H = B.shape[0]
    # columns: a box column is black in many rows of the band; start from the whole crop, then refine twice
    y0, y1 = 0, H - 1
    box = None
    for it in range(4):
        cf = B[y0:y1 + 1].mean(0)
        xr = [r for r in runs1d(cf, 0.3 if it == 0 else 0.5, gap=0) if r[1] - r[0] >= 120]   # no gap: the box's
        # antialiased edge column parts it from a dark coat beside it; inside, the margins keep every column >= 0.6
        if not xr: return None
        xr = max(xr, key=lambda r: r[1] - r[0] - abs((r[0] + r[1]) / 2 - W / 2) * 0.5)
        x0, x1 = xr
        rf = B[:, x0 + 8:x1 - 7].mean(1)
        yr = [r for r in runs1d(rf, 0.25) if r[1] - r[0] >= 40 and rf[r[0]:r[1] + 1].max() >= 0.9]   # letter rows
        # are a quarter black at least; a box run holds pure margin rows
        if not yr: return None
        y0n, y1n = max(yr, key=lambda r: r[1] - r[0])
        e = np.nonzero(rf[y0n:y1n + 1] >= 0.85)[0]               # same for the top and bottom edge rows
        y0n, y1n = y0n + e[0], y0n + e[-1]
        if (y0n, y1n, x0, x1) == box: break
        box = (y0n, y1n, x0, x1); y0, y1 = y0n, y1n
    y0, y1, x0, x1 = box
    if x1 - x0 >= 0.97 * W or not (40 <= y1 - y0 <= 320): return None
    if B[y0:y1 + 1, x0:x1 + 1].mean() < 0.6: return None
    return [int(x0), int(y0), int(x1), int(y1)]


def kernel_for(line_px):
    """Morph kernel wider than the stroke: small print 15, captions / headlines 31, end-card display type 61."""
    return 15 if line_px < 30 else 31 if line_px < 80 else 61


def lum(hexc):
    try:
        h = hexc.lstrip('#'); r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return 0.299 * r + 0.587 * g + 0.114 * b
    except Exception:
        return None


def measure(frames, t0, t1, rect, k, polarity=None, holes=()):
    """Mode, threshold and brightness gate of the letters inside rect [x0, y0, x1, y1] px during [t0, t1).
    holes: px rects of other zones inside it (a lifted price tag), left out of the stats. Outlined letters whose two
    hats differ a lot (white fill on cream: top-hat 25, outline 150+) come back as two zones, light + dark."""
    x0, y0, x1, y1 = rect
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    pt, pb, lo, hi, dk, q3 = [], [], [], [], [], []
    sel = [L for t, L in frames if t0 <= t < t1] or [L for t, L in frames if abs(t - t0) < 0.5]
    ox, oy = max(0, x0 - k), max(0, y0 - k)
    for L in sel[:: max(1, len(sel) // 12)]:
        c = L[oy:y1 + k, ox:x1 + k]
        if c.size == 0: continue
        th = cv2.morphologyEx(c, cv2.MORPH_TOPHAT, ker); bh = cv2.morphologyEx(c, cv2.MORPH_BLACKHAT, ker)
        keep = np.zeros(c.shape, bool); keep[y0 - oy:y1 - oy, x0 - ox:x1 - ox] = True
        for hx0, hy0, hx1, hy1 in holes: keep[max(0, hy0 - oy):max(0, hy1 - oy), max(0, hx0 - ox):max(0, hx1 - ox)] = False
        if keep.sum() < 50: continue
        th, bh, c = th[keep], bh[keep], c[keep]
        pt.append(np.percentile(th, 99.5)); pb.append(np.percentile(bh, 99.5))
        lo.append(np.percentile(c, 50)); hi.append(np.percentile(c, 99.5)); dk.append(np.percentile(c, 0.5)); q3.append(np.percentile(c, 75))
    if not pt: return None
    T, B, bg, top, low, p75 = (float(np.median(v)) for v in (pt, pb, lo, hi, dk, q3))
    if max(T, B) < 8: return None                                      # nothing that stands out: no text left there
    # polarity: the extreme that sits further from the background (median) is the letters. The hats alone mislead:
    # on cream with dark letters the cream gaps between letters are bright structures too (top-hat as high as black-hat)
    lmin = int(np.clip(round(bg + 0.35 * (top - bg)), 60, 245))
    if polarity == 'both':      # outlined letters (white fill + dark shadow): both hats
        tl, td = (int(np.clip(round(0.45 * v), 10, 70)) for v in (T, B))
        if min(tl, td) >= 0.6 * max(tl, td):
            return {'mode': 'both', 'k': k, 'thr': min(tl, td), 'grow': 3, 'lmin': lmin}
        return [{'mode': 'light', 'k': k, 'thr': tl, 'grow': 3, 'lmin': lmin},
                {'mode': 'dark', 'k': k, 'thr': td, 'grow': 3, 'lmin': 150}]
    if polarity == 'legal':     # the legal line: white letters with a dark outline / shadow (house style) rise above the
        # background (cream 233 -> fill 255: +22); grey or dark letters on a light page do not (+0 .. +7). Colour reads name
        # the outline colour for outlined letters, so the pixels decide
        polarity = 'light' if top - bg >= 14 else 'dark'
    dark = polarity == 'dark' if polarity else (bg - low) > 1.3 * (top - bg) and B > 0.8 * T
    if dark:
        return {'mode': 'dark', 'k': k, 'thr': int(np.clip(round(0.45 * B), 10, 70)), 'grow': 3 if k <= 31 else 8, 'lmin': 150}
    z = {'mode': 'light', 'k': k, 'thr': int(np.clip(round(0.45 * T), 10, 70)), 'grow': {15: 3, 31: 5}.get(k, 12), 'lmin': lmin}
    # white letters partly over a light background (cream wall: bg 225, fill 250) stand out by only ~25 there, under
    # the threshold the dark parts of the shot set: a second zone takes only the near-white fill, low step, high gate
    if top >= 235 and p75 >= 185 and z['thr'] > 20:
        return [z, {'mode': 'light', 'k': k, 'thr': 12, 'grow': 3, 'lmin': int(max(225, top - 17))}]
    return z


def motion_union(frames, t0, t1, rect, t_ref=None, reach=48):
    """A text that moves inside its window (a CTA pill that bounces 22 px) leaves the read box in half the frames: follow the
    box's content by template matching at the sampled frames (+- reach px) and return the union of the places it was."""
    x0, y0, x1, y1 = rect
    sel = [(t, L) for t, L in frames if t0 <= t < t1]
    if len(sel) < 3: return rect
    ref = min(sel, key=lambda q: abs(q[0] - t_ref)) if t_ref is not None and t0 <= t_ref < t1 else sel[len(sel) // 2]
    tpl = ref[1][y0:y1, x0:x1]
    if tpl.size == 0 or tpl.std() < 8: return rect
    u = list(rect)
    for t, L in sel:
        a0, b0 = max(0, x0 - reach), max(0, y0 - reach)
        win = L[b0:min(L.shape[0], y1 + reach), a0:min(L.shape[1], x1 + reach)]
        if win.shape[0] < tpl.shape[0] or win.shape[1] < tpl.shape[1]: continue
        _, mx, _, loc = cv2.minMaxLoc(cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED))
        if mx < 0.7: continue                                  # the text is gone or the background took over: no move
        dx, dy = a0 + loc[0] - x0, b0 + loc[1] - y0
        u = [min(u[0], x0 + dx), min(u[1], y0 + dy), max(u[2], x1 + dx), max(u[3], y1 + dy)]
    if u != list(rect): u = [max(0, u[0] - 4), max(0, u[1] - 4), u[2] + 4, u[3] + 4]   # between two sampled frames
    return u


def shots(t0, t1, cuts):
    """[t0, t1) split at the shot changes inside it."""
    cs = sorted({round(c, 3) for c in cuts if t0 + 0.05 < c < t1 - 0.05})
    edges = [t0] + cs + [t1]
    return list(zip(edges, edges[1:]))


def main():
    A, RB, SRC, OUT = sys.argv[1:5]
    a = json.load(open(A)); rb = json.load(open(RB))
    W, H = rb['width'], rb['height']; D = float(rb['duration'])
    def px(b, pad_x=0, pad_y=0):
        return [max(0, round(b['xmin'] * W / 1000) - pad_x), max(0, round(b['ymin'] * H / 1000) - pad_y),
                min(W, round(b['xmax'] * W / 1000) + pad_x), min(H, round(b['ymax'] * H / 1000) + pad_y)]
    cuts = sorted({float(c.get('to', c.get('from'))) for c in a.get('cuts', [])})
    scenes = a.get('scenes', [])
    def scene_type(t):
        for s in scenes:
            if s['start'] <= t < s['end']: return s.get('type')
        return None
    groups = a.get('groups', [])
    def reads(role, t0, t1, text=None):
        g = [x for x in groups if x.get('role') == role and t0 - 0.05 <= float(x['t']) <= t1 + 0.05]
        if text:
            key = text.strip().lower()[:12]
            m = [x for x in g if (x.get('text') or '').strip().lower()[:12] == key]
            if m: return m
        return g
    def union(boxes):
        return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
    frames = load_gray(SRC, FPS_M, W, H)
    cfg = {'_note': 'auto zones (engine/auto_zones.py) measured on this build and its source', 'static_share': 0.3,
           'zones_extra': [], 'auto': {'zones': []}}
    # boxes the zones must leave alone: what the build keeps as source pixels or never translates
    lifted = [e for e in rb['elements'] if (e.get('name') or '').startswith('tag lifted')]
    lifted_txt = {(e.get('text') or '').strip() for e in lifted}
    keep = []
    for g in groups:
        if g.get('role') in KEEP and not (g.get('role') == 'price' and (g.get('text') or '').strip() in lifted_txt):
            keep.append((float(g['t']), px(g['box'], 4, 4)))
    def clip_keep(t0, t1, r):
        """Shrink rect r (vertically) off every kept box that shows during [t0, t1)."""
        x0, y0, x1, y1 = r
        for t, (kx0, ky0, kx1, ky1) in keep:
            if not (t0 - 0.3 <= t <= t1 + 0.3): continue
            if kx1 <= x0 or kx0 >= x1 or ky1 <= y0 or ky0 >= y1: continue
            above, below = ky0 - y0, y1 - ky1              # room left above / below the kept box
            if above >= below: y1 = min(y1, ky0)
            else: y0 = max(y0, ky1)
        return [x0, y0, x1, y1] if y1 - y0 >= 8 else None
    def polarity(gs):
        """'light' / 'dark' from the reads' letter colour vs the colour around them (None when they do not say)."""
        v = [(lum(x.get('color') or ''), lum(x.get('plate') or x.get('surround') or '')) for x in gs]
        v = [c - s for c, s in v if c is not None and s is not None and abs(c - s) >= 40]
        if not v: return None
        return 'light' if sum(1 for d in v if d > 0) >= sum(1 for d in v if d < 0) else 'dark'
    fulls = []                                                   # full zones so far: (t0, t1, px rect)
    def add(t0, t1, r, role, line_px, full=False, pol=None):
        if t1 - t0 < 0.02 or r is None: return
        pct = [round(v * 100 / s, 2) for v, s in zip(r, (W, H, W, H))]
        if full:
            fulls.append((t0, t1, r)); ms = [None]
        else:
            holes = [h for a0, a1, h in fulls if a0 < t1 and a1 > t0]
            m = measure(frames, t0, t1, r, kernel_for(line_px), pol, holes)
            if m is None: cfg['auto']['zones'].append({'role': role, 't': [round(t0, 2), round(t1, 2)], 'skip': 'no text'}); return
            ms = m if isinstance(m, list) else [m]
        for m in ms:
            z = [round(t0, 3), round(t1 - t0, 3)] + pct + (['full', 0, 0, 3] if m is None else [m['mode'], m['k'], m['thr'], m['grow'], m['lmin']])
            cfg['zones_extra'].append(z); cfg['auto']['zones'].append({'role': role, 'z': z})

    # 1) captions: measured frame by frame inside the caption rows
    subs = [o for o in a.get('overlays', []) if o.get('role') in CAPTION]
    if subs:
        sb = [px(g['box'], 0, 0) for g in groups if g.get('role') in CAPTION] or [px(o['box']) for o in subs]
        cy0, cy1 = min(b[1] for b in sb), max(b[3] for b in sb)
        r0 = max(0, cy0 - 40); r1 = min(H, cy1 + 40)
        db = [px(g['box']) for g in groups if g.get('role') == 'disclaimer']
        if db:                                                   # never measure the legal line as a caption
            dy0 = sorted(b[1] for b in db)[len(db) // 2]
            if r0 < dy0 < r1: r1 = dy0 - 4
        win = [(o['start'], o['end']) for o in subs]
        cap = []; radii = []
        for t, f in load_rgb(SRC, FPS_C, W, H, (r0, r1)):
            if not any(s - 0.05 <= t < e + 0.05 for s, e in win): continue
            L = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
            rec = {'t': round(t, 2), 'box': None, 'text': None}
            b = find_box(f, W)
            if b:
                rec['box'] = [b[0], b[1] + r0, b[2], b[3] + r0]
                col = f[b[1]:b[3] + 1, b[0] + 1].max(1) <= 3          # corner radius: rows until the box's first
                d = int(np.argmax(col)) if col.any() else None            # column turns black (a slight under-
                if d is not None and d < 80: radii.append(d)              # estimate: the new box only gets squarer)
            if rec['box'] is None and scene_type(t) not in ('app_ui',):   # no box: white / yellow letters that stand
                q0, q1 = max(0, cy0 - 10 - r0), max(0, cy1 + 10 - r0)        # out of their surround (a cream wall
                f, L = f[q0:q1], L[q0:q1]                                    # is bright but flat: low top-hat)
                mx, mn = f.max(2), f.min(2)
                th = cv2.morphologyEx(L.astype(np.uint8), cv2.MORPH_TOPHAT, TOPHAT_C)
                T = (L > 225) & (th > 60) & (((mx - mn) < 45) | ((f[..., 0] > 200) & (f[..., 1] > 170) & (f[..., 2] < 150)))
                if T.sum() >= 250:
                    cols = np.nonzero(T.sum(0) >= 3)[0]; rws = np.nonzero(T.sum(1) >= 3)[0]
                    tb = [int(cols.min()), int(rws.min()) + r0 + q0, int(cols.max()), int(rws.max()) + r0 + q0]
                    # caption-like only: letters fill their bbox densely (words appear one by one, so the bbox
                    # need not be centred) and at most two lines tall; scattered highlights between two boxed
                    # pages (2 % of a 590 x 162 px bbox on one test video) are not a caption
                    fill = T.sum() / max(1, (tb[2] - tb[0] + 1) * (tb[3] - tb[1] + 1))
                    if fill >= 0.12 and tb[3] - tb[1] <= 170 and tb[0] > 2 and tb[2] < W - 3:   # not cut by the frame
                        rec['text'] = tb
            cap.append(rec)
        cfg['caption_rects'] = caption_rects(cap, W, H)
        # bare letters anywhere (captions without a box in some shots): only the measured boxes are old boxes the new box
        # covers; a bare-letter page is erased (old_boxes «all» would leave it under the new box's edges)
        bare = sum(1 for c in cap if c['text']) >= 3
        ones = [r for r in cfg['caption_rects'] if r[6] == 'B' and r[5] - r[3] < 7.3]
        tr = [e for e in rb['elements'] if e.get('type') == 'text' and e.get('transcript_source')]
        if len(ones) >= 3 and len(tr) == 1:
            # the new box's corners must cover the old box's: never rounder than the old box (measured) nor than
            # the Build's own caption plate
            radius = next((c['elements'][2].get('border_radius') for c in rb['elements'] if c.get('type') == 'composition'
                           and len(c.get('elements', [])) == 3 and str(c['elements'][2].get('fill_color', '')).startswith('rgba(0,0,0')), '1.4 vmin')
            rv = float(str(radius).split()[0]) if str(radius).endswith('vmin') else 1.4
            if radii: rv = min(rv, sorted(radii)[len(radii) // 2] / (min(W, H) / 100))
            y0s = sorted(r[3] for r in ones); y1s = sorted(r[5] for r in ones)
            cfg['caption_pages'] = {'track': 'transcript', 'box': [round(y0s[len(y0s) // 10] - 0.25, 2), round(y1s[-1 - len(y1s) // 10] + 0.25, 2)],
                                    'old_boxes': 'B' if bare else 'all', 'radius': f'{rv:.2f} vmin', 'fill': '#000000', 'pad_x_em': 0.6, 'max_width': 90,
                                    'font': f"{tr[0].get('font_family', 'Montserrat')}:{tr[0].get('font_weight', '700')}",
                                    'local': True, 'tall_old': 'two_lines'}

    # 2) every other translated block: one zone per shot over its read boxes
    for o in sorted(a.get('overlays', []), key=lambda o: o.get('role') != 'price'):   # full zones first: holes
        role = o.get('role')
        if role in CAPTION or role in KEEP and role != 'price': continue
        for t0, t1 in shots(float(o['start']), float(o['end']), cuts):
            if role == 'price':
                if (o.get('text') or '').strip() not in lifted_txt: continue
                g = [x for x in reads('price', t0, t1, o.get('text')) if x.get('freeSpot')] or reads('price', t0, t1, o.get('text'))
                if g: add(t0, t1, union([px(x['box'], 8, 6) for x in g]), 'price lifted', 0, full=True)
                continue
            g = reads(role, t0, t1, None if role == 'disclaimer' else o.get('text'))
            if role == 'disclaimer':
                dp = [c for c in a.get('covered', []) if c.get('type') == 'disclaimer' and c.get('band') and c['start'] < t1 and c['end'] > t0]
                gw = [x for c in dp for x in c.get('grown', []) if x['start'] < t1 and x['end'] > t0 and x.get('boxes')]
                if gw:
                    b0 = gw[0]['boxes'][0]; r = [round(b0[0] * W / 1000), round(b0[1] * H / 1000), round(b0[2] * W / 1000), round(b0[3] * H / 1000)]
                elif dp:
                    c = dp[0]; xs = c.get('cols') or [0, 1000]
                    r = px({'ymin': c['band'][0], 'xmin': xs[0], 'ymax': c['band'][1], 'xmax': xs[1]}, 12, 10)
                elif g:                                          # one odd read must not stretch the line: median box
                    bs = [px(x['box'], 12, 10) for x in g]; r = [sorted(v)[len(v) // 2] for v in zip(*bs)]
                else:
                    cfg['auto']['zones'].append({'role': role, 't': [t0, t1], 'skip': 'no read box'}); continue
                # the legal line is white letters with a dark shadow (house style; grow 3 takes the shadow); a shot whose pixels
                # show dark letters on a light page (an app screen, a cream intro) erases dark letters
                add(t0, t1, clip_keep(t0, t1, r), role, 20, pol='legal'); continue
            if g:
                r = union([px(x['box'], 12, 10) for x in g])
                line = max((px(x['box'])[3] - px(x['box'])[1]) / max(1, int(x.get('lines') or 1)) for x in g)
                r = motion_union(frames, t0, t1, r, g[0].get('t'))     # a bouncing pill: its whole path
            else:
                cv = [c for c in a.get('covered', []) if c.get('type', role) == role and c['start'] < t1 and c['end'] > t0 and c.get('band')]
                if not cv: cfg['auto']['zones'].append({'role': role, 't': [t0, t1], 'skip': 'no read box'}); continue
                c = cv[0]; xs = c.get('cols') or [0, 1000]
                r = px({'ymin': c['band'][0], 'xmin': xs[0], 'ymax': c['band'][1], 'xmax': xs[1]})
                line = (r[3] - r[1]) / 2
            if scene_type(t0) == 'packshot':                       # end-card text slides / fades in: cover its path
                r = [r[0], max(0, r[1] - 60), r[2], min(H, r[3] + 60)]
                t0 = min(t0, next((s['start'] for s in scenes if s['start'] <= t0 < s['end']), t0))
            add(t0, t1, clip_keep(t0, t1, r), role, line, pol=polarity(g))
    json.dump(cfg, open(OUT, 'w'), ensure_ascii=False, indent=1)
    print('auto zones:', len(cfg['zones_extra']), 'zones,', len(cfg.get('caption_rects', [])), 'caption rects,',
          'caption pages' if cfg.get('caption_pages') else 'no caption pages',
          '| skipped', sum(1 for z in cfg['auto']['zones'] if z.get('skip')))


def caption_rects(F, W, H, DT=1 / FPS_C):
    """Per-frame caption measures -> merged rects [t, d, x0, y0, x1, y1 (%), 'B' | 'T'] (as segs.py)."""
    def rect(f):
        if f['box'] and f['box'][3] - f['box'][1] < 140: x0, y0, x1, y1 = f['box']; return ('B', [x0 - 2, y0 - 1, x1 + 2, y1 + 1])
        if f['box']: x0, y0, x1, y1 = f['box']; return ('T', [x0 - 16, y0 - 12, x1 + 16, y1 + 12])
        if f['text']: x0, y0, x1, y1 = f['text']; return ('T', [x0 - 16, y0 - 12, x1 + 16, y1 + 12])
        return None
    caps = []; cur = None; prev_t = None
    for f in F:
        r = rect(f)
        gap = prev_t is not None and f['t'] - prev_t > DT * 1.5; prev_t = f['t']
        if r is None or gap:
            if cur: caps.append(cur); cur = None
            if r is None: continue
        k, b = r
        if cur and cur['k'] == k and all(abs(x - y) <= 14 for x, y in zip(b[1::2], cur['b'][1::2])) and \
                ((k == 'T' and abs(b[0] - cur['b0']) <= 14) or (k == 'B' and all(abs(x - y) <= 14 for x, y in zip(b, cur['b'])))):
            cur['b'] = [min(cur['b'][0], b[0]), min(cur['b'][1], b[1]), max(cur['b'][2], b[2]), max(cur['b'][3], b[3])]; cur['t1'] = f['t']
        else:
            if cur: caps.append(cur)
            cur = {'k': k, 'b': b, 'b0': b[0], 't0': f['t'], 't1': f['t']}
    if cur: caps.append(cur)
    sx, sy = W / 100, H / 100; out = []
    for i, c in enumerate(caps):
        t0 = max(0, c['t0'] - DT / 2 - 0.05); t1 = c['t1'] + DT / 2 + 0.05
        x0, y0, x1, y1 = c['b']
        if c['k'] == 'B' and i + 1 < len(caps) and caps[i + 1]['k'] == 'B' and caps[i + 1]['t0'] - c['t1'] <= 0.2:
            n = caps[i + 1]; g0 = t1; g1 = max(g0, n['t0'] - DT / 2 - 0.05)
            if g1 > g0:
                u = [min(x0, n['b'][0]), min(y0, n['b'][1]), max(x1, n['b'][2]), max(y1, n['b'][3])]
                out.append([round(g0, 2), round(g1 - g0, 2), round(u[0] / sx, 2), round(u[1] / sy, 2), round(u[2] / sx, 2), round(u[3] / sy, 2), 'B'])
        out.append([round(t0, 2), round(t1 - t0, 2), round(x0 / sx, 2), round(y0 / sy, 2), round(x1 / sx, 2), round(y1 / sy, 2), c['k']])
    return out


if __name__ == '__main__':
    main()
