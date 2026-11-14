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
  subtitle   -> a dark plate that is not pure black under the captions (the reads name one, a translucent plate): the
                plate itself is measured on every frame = caption_rects "P", one rect per page; such a source gets no
                caption_pages: build_math.py erases the old plate and keeps the build's
                Else black boxes ("B") and bare light letters ("T") measured frame by frame inside the caption rows; a
                boxed source also gets caption_pages (one fixed box height, same words -> same box; old_boxes "all" only
                when no bare-letter page was seen).
                Else, with next to no black box: outlined captions (a bright fill ringed by a dark outline, found in at
                least half of the caption frames; captions.py): one "cap" zone per caption block over the rows the lines
                sit in, + cfg "cap" = their capital height, outline reach and soft shadow measured on the source; the
                new caption layer takes that look (text_set).
                Every way, cfg auto.captions says what share of each caption block's time is under an erase.
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
import captions
from layers import orig_rect

KEEP = {'price', 'logo', 'ui', 'sticker', 'app_ui', 'watermark'}
CAPTION = {'subtitle', 'caption'}
FPS_M = 4            # frames per second sampled for the zone measures
FPS_C = 10           # frames per second sampled for the caption measures
TOPHAT_C = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))   # caption letters vs their surround
PLATE_Q = 0.98       # a line of pixels is still plate while this share of it is as dark as the plate


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


def plate_reads(caps, px):
    """What the caller's caption reads say when they name a dark plate that is not pure black, else None.
    Such a plate is translucent: its pixels follow what is behind it (one test video: 49 over a white app screen, 30-40
    over a floor, under 12 over dark trousers; read as "#222222"), so find_box (pure black, max channel <= 3) never sees
    it. Dark = luma under 128 (where the builder draws its dark plate), not pure black = over 12. More than half of the
    reads that name a plate must name such a one; "#000000" reads keep the black-box measure."""
    pv = [lum(g.get('plate') or '') for g in caps]
    dark = [g for g, v in zip(caps, pv) if v is not None and 12 < v < 128]
    if not dark or 2 * len(dark) <= sum(1 for v in pv if v is not None): return None
    lh = float(np.median([(px(g['box'])[3] - px(g['box'])[1]) / max(1, int(g.get('lines') or 1)) for g in dark]))   # one text line, px
    col = [v for v in (lum(g.get('color') or '') for g in dark) if v is not None]
    pb = [(float(g['t']), px(g['plateBox']), px(g['box'])) for g in dark if g.get('plateBox')]
    return {'lh': lh, 'lim': int(round(1.2 * lh)), 'lmin': 0.75 * float(np.median(col)) if col else 190.0,
            'thr': float(np.clip(2 * np.median([lum(g['plate']) for g in dark]) + 20, 30, 130)),      # if the stills cannot say
            'stills': [(t, p) for t, p, b in pb],
            # the plate's margins round the letters on the read stills: only for a side no page of the video shows
            'fall': [int(np.median(v)) for v in zip(*[[b[0] - p[0], b[1] - p[1], p[2] - b[2], p[3] - b[3]] for t, p, b in pb])] if pb
                    else [int(round(0.5 * lh))] * 4,
            'bare': len(dark) < len(caps)}     # some read shows a caption without such a plate: the old measures stay for those


def plate_dark(frames, stills):
    """How dark the plate is, from the read stills (the gray measure frame nearest to each): its pixels 10 px inside each
    edge against the background 10 px outside it. A plate that darkens what is behind it keeps one ratio (0.19 on one
    test video) -> the brightest a plate pixel can be = the ratio over white, + 12 for noise; None when the frames do
    not show it (a frame from another page gives no pair that is light outside and darker inside)."""
    q = []
    for t, (x0, y0, x1, y1) in stills if frames else []:
        L = min(frames, key=lambda v: abs(v[0] - t))[1].astype(np.float32); d = 10
        if y0 - d < 0 or y1 + d >= L.shape[0] or x0 - d < 0 or x1 + d >= L.shape[1]: continue
        xs = np.arange(x0 + 40, x1 - 40, 4); ys = np.arange(y0 + 40, y1 - 40, 4)      # clear of the round corners
        o = np.concatenate([L[y0 - d, xs], L[y1 + d, xs], L[ys, x0 - d], L[ys, x1 + d]])
        n = np.concatenate([L[y0 + d, xs], L[y1 - d, xs], L[ys, x0 + d], L[ys, x1 - d]])
        ok = (o >= 80) & (n < 0.75 * o)
        q += list(n[ok] / o[ok])
    return float(np.clip(255 * np.median(q) + 12, 30, 130)) if len(q) >= 50 else None


def find_plate(f, thr, lmin, lh, lim):
    """A dark translucent caption plate in an RGB crop, measured from its letters outward: (letters' bbox [x0, y0, x1, y1],
    how far the plate reaches past it [left, top, right, bottom] px (lim = that edge was not seen), letter count, the
    letters' mask) or None.
    Letters first, because they are the one sure thing: bright islands that the dark (<= thr) encloses on every side. A
    letter-free ground (a 31 px opening, a 51 px median) let app-screen text in or lost two-line pages. The letters of one
    plate fill a rectangle that holds nothing but plate and letters; while theirs does not, the island on one of its four
    sides that spoils it most is dropped (a light gap in dark clothes that touch the plate), so a short second line
    stays in. Each edge is then the first line past the letters that is not dark: exact against a light background (a
    white app screen, a floor); against something as dark as the plate (trousers, a coat, a plant behind or beside it)
    the line stays dark and the edge is not seen, which plate_pages settles from the page's other frames and the video's
    other pages."""
    L = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    H, W = L.shape
    D = L <= thr
    labD = cv2.connectedComponents(D.astype(np.uint8), connectivity=4)[1]
    n, lab, st, _ = cv2.connectedComponentsWithStats((~D).astype(np.uint8), connectivity=8)
    x, y, w, h, a = st.T
    ids = np.nonzero((x > 0) & (y > 0) & (x + w < W) & (y + h < H) & (a >= 8) & (h <= 1.5 * lh) & (np.arange(n) > 0))[0]
    if not len(ids): return None
    lit = np.bincount(lab[L >= lmin], minlength=n)[ids]
    bright = lit >= 0.3 * a[ids]; letter = bright & (h[ids] >= 0.35 * lh)     # the smaller bright ones: dots, commas, specks
    # the dark component round an island: the pixel above the first pixel of its top row (8-connected islands, 4-connected dark)
    comp = np.array([labD[y[i] - 1, x[i] + int(np.argmax(lab[y[i], x[i]:x[i] + w[i]] == i))] for i in ids])
    tot = np.bincount(comp[letter], weights=lit[letter]) if letter.any() else np.zeros(1)
    if tot.max() < 150: return None                           # the plate = the dark component that holds most letter pixels
    on = comp == int(np.argmax(tot))
    I = cv2.integral((D | np.isin(lab, ids[on])).astype(np.uint8))      # plate + all it encloses (a word in another colour too)
    ids, letter = ids[on & bright], letter[on & bright]
    bx = np.stack([x[ids], y[ids], x[ids] + w[ids] - 1, y[ids] + h[ids] - 1], 1); keep = np.ones(len(ids), bool)
    def box(k): return [int(bx[k, 0].min()), int(bx[k, 1].min()), int(bx[k, 2].max()), int(bx[k, 3].max())]
    def full(V): return (I[V[3] + 1, V[2] + 1] - I[V[1], V[2] + 1] - I[V[3] + 1, V[0]] + I[V[1], V[0]]) / ((V[2] - V[0] + 1) * (V[3] - V[1] + 1))
    while full(box(keep)) < 0.97 and keep.sum() > 1:
        k = np.nonzero(keep)[0]; best = None
        for j in {int(k[np.argmin(bx[k, 0])]), int(k[np.argmin(bx[k, 1])]), int(k[np.argmax(bx[k, 2])]), int(k[np.argmax(bx[k, 3])])}:
            keep[j] = False; v = full(box(keep)); keep[j] = True
            if best is None or v > best[0]: best = (v, j)
        keep[best[1]] = False
    if not letter[keep].any(): return None
    x0, y0, x1, y1 = box(keep); q = (x1 - x0) // 5      # top and bottom along the middle of the letters: the round corners stay out
    def reach(lines):                                   # dark flags of the lines past the letters, nearest first
        m = np.nonzero(lines.mean(1) < PLATE_Q)[0]
        return int(m[0]) if len(m) else lim
    g = [reach(D[y0:y1 + 1, max(0, x0 - lim):x0][:, ::-1].T), reach(D[max(0, y0 - lim):y0, x0 + q:x1 - q + 1][::-1]),
         reach(D[y0:y1 + 1, x1 + 1:x1 + 1 + lim].T), reach(D[y1 + 1:y1 + 1 + lim, x0 + q:x1 - q + 1])]
    if sum(v >= 4 for v in g) < 2: return None                # no dark margin round the letters: outlined letters, not a plate
    return [x0, y0, x1, y1], g, int(letter[keep].sum()), np.isin(lab, ids[keep])


def plate_pages(M, inw, lim, fall, lh):
    """Per-frame plate measures (find_plate's bbox, margins, letter count + «the letters of the last measured frame are
    still there»; None = no plate) -> the pages, each with its plate rect 'r' (crop coords; None = not a caption plate)
    and the frames 'i' it was measured in, and the margin the pages agree on [left / right, top / bottom].
    A page = frames whose letters stay (words may be added: one plate; a miss of up to three frames does not end it).
    Its plate does not move, so each edge is the innermost one any of its frames saw past all its letters: something dark
    beside the plate only ever pushes a measured edge outward, and it moves with the scene while the plate stands still.
    Opposite sides of a plate have the same padding, so the two sides of an axis settle together: both seen and alike
    (within a tenth / a fifth of a line) -> as measured; one not seen -> it takes the other's; they differ -> the
    smaller when it is in line with the video's median margin (the other side ran on into something dark), else the
    larger (a speck stopped the smaller one: too much is erased rather than a strip of the plate left). None seen, or
    the only one under half the median -> the median + 2 px. A page with one letter counts only when three of its sides
    are in line with the median; a page outside every caption window of the caller is not a caption."""
    pages = []; cur = None
    for i, m in enumerate(M):
        if m is None: continue
        if cur is None or not m[3]: cur = {'i': []}; pages.append(cur)
        cur['i'].append(i)
    for p in pages:
        b = np.array([M[i][0] for i in p['i']]); g = np.array([M[i][1] for i in p['i']])
        U = [int(b[:, 0].min()), int(b[:, 1].min()), int(b[:, 2].max()), int(b[:, 3].max())]
        e = np.stack([U[0] - b[:, 0] + g[:, 0], U[1] - b[:, 1] + g[:, 1], b[:, 2] + g[:, 2] - U[2], b[:, 3] + g[:, 3] - U[3]], 1)
        e = np.where((g < lim) & (e > 0), e, 10 ** 6).min(0)
        p.update(U=U, m=[int(v) if v < 10 ** 6 else None for v in e], n=int(np.median([M[i][2] for i in p['i']])))
    axes = ((0, 2), (1, 3)); ref = []
    for a, b in axes:
        v = [p['m'][k] for p in pages for k in (a, b) if p['m'][k] is not None]
        ref.append(int(np.median(v)) if v else max(fall[a], fall[b]))
    tol = [max(4, round(0.1 * lh)), max(6, round(0.2 * lh))]
    for p in pages:
        m = [None] * 4; line = [False] * 4
        for i, (a, b) in enumerate(axes):
            for k in (a, b): line[k] = p['m'][k] is not None and 0.5 * ref[i] <= p['m'][k] <= ref[i] + tol[i]
            v = sorted(x for x in (p['m'][a], p['m'][b]) if x is not None)
            if len(v) == 2 and v[1] - v[0] <= tol[i]: m[a], m[b] = p['m'][a], p['m'][b]
            elif len(v) == 2: m[a] = m[b] = v[0] if 0.5 * ref[i] <= v[0] <= ref[i] + tol[i] else v[1]
            elif v and v[0] >= 0.5 * ref[i]: m[a] = m[b] = v[0]
            else: m[a] = m[b] = ref[i] + 2
        p['seen'] = ''.join('LTRB'[k] if m[k] == p['m'][k] else '-' for k in range(4)); U = p['U']
        p['r'] = None if not any(inw[i] for i in p['i']) or (p['n'] < 2 and sum(line) < 3) else [U[0] - m[0], U[1] - m[1], U[2] + m[2], U[3] + m[3]]
    return pages, ref


def plate_rects(src, fps, w, h, rows, win, PL):
    """The plates of a source whose captions sit on a dark translucent plate: [t, d, x0, y0, x1, y1 (%), 'P'] per page
    (+ 2 px for the plate's antialiased edge) and a note for the cfg.
    Every frame of the source is measured, numbered as plate.py numbers them (frame / fps), so a rect starts on the
    page's first frame and ends after its last: the erase is a full rect, and a sampled measure (10 fps + a margin in
    time) would blur the picture for several frames before and after each page. The caption windows of the caller are
    rough, so the frames between them are measured too (a page that runs past its window must not keep its last
    frames); plate_pages drops a page that touches no window."""
    r0, r1 = rows; T0, T1 = min(s for s, e in win) - 0.05, max(e for s, e in win) + 0.05
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', src, '-vf', f'format=rgb24,crop={w}:{r1 - r0}:0:{r0}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                         stdout=subprocess.PIPE)
    n = w * (r1 - r0) * 3; j = -1; M = []; F = []; inw = []; last = None
    while True:
        b = p.stdout.read(n); j += 1
        if len(b) < n or j / fps >= T1: break
        if j / fps < T0: continue
        m = find_plate(np.frombuffer(b, np.uint8).reshape(r1 - r0, w, 3), PL['thr'], PL['lmin'], PL['lh'], PL['lim'])
        if m:
            same = last is not None and j - last[0] <= 4 and (last[1] & m[3]).sum() >= 0.85 * last[1].sum()
            last = (j, m[3]); m = (m[0], m[1], m[2], bool(same))
        M.append(m); F.append(j); inw.append(any(s - 0.05 <= j / fps < e + 0.05 for s, e in win))
    p.kill(); p.wait()
    pages, ref = plate_pages(M, inw, PL['lim'], PL['fall'], PL['lh'])
    # bare letters over a dark part of the picture are «enclosed by the dark» too, but with next to no margin (3-5 px on
    # three test videos without plates, 22-39 px on plates): when the pages do not agree on a plate's margins, the reads
    # were wrong about the plate and no rect is made (the caller falls back to the box / bare-letter measures)
    plates = min(ref) >= 0.12 * PL['lh']
    sx, sy = w / 100, h / 100; out = []
    for q in pages if plates else []:
        if not q['r']: continue
        x0, y0, x1, y1 = q['r']; a, c = F[q['i'][0]], F[q['i'][-1]] + 1
        if not x0 <= w / 2 <= x1: continue        # captions are centred: a dark button with a label beside them is not a plate
        out.append([round(a / fps, 3), round((c - a) / fps, 3), round(max(0, x0 - 2) / sx, 2), round(max(0, y0 + r0 - 2) / sy, 2),
                    round(min(w, x1 + 2) / sx, 2), round(min(h, y1 + r0 + 2) / sy, 2), 'P'])
    return out, {'thr': round(PL['thr'], 1), 'rows': [r0, r1], 'margins': ref, 'frames': sum(1 for m in M if m), 'pages': len(out),
                 'settled': sum(1 for q in pages if q['r'] and q['seen'] != 'LTRB'), 'dropped': sum(1 for q in pages if not q['r'])}


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
    legal = polarity == 'legal'
    if legal:                   # the legal line: white letters with a dark outline / shadow (house style) rise above the
        # background (cream 233 -> fill 255: +22); grey or dark letters on a light page do not (+0 .. +7). Colour reads name
        # the outline colour for outlined letters, so the pixels decide
        polarity = 'light' if top - bg >= 14 else 'dark'
    dark = polarity == 'dark' if polarity else (bg - low) > 1.3 * (top - bg) and B > 0.8 * T
    if dark:
        return {'mode': 'dark', 'k': k, 'thr': int(np.clip(round(0.45 * B), 10, 70)), 'grow': 3 if k <= 31 else 8, 'lmin': 150}
    z = {'mode': 'light', 'k': k, 'thr': int(np.clip(round(0.45 * T), 10, 70)), 'grow': {15: 3, 31: 5}.get(k, 12), 'lmin': lmin}
    if legal:   # small outlined letters: the dark outline / drop shadow sits up to 4 px past the fill, and a high brightness
        # gate (cream: 241) keeps only the core of each letter, so grow 3 left the shadow's bottom row as a dotted line
        # (2026-11-13). The hand cfgs' gate 150 + grow 4 takes the whole letter and its shadow; the
        # top-hat threshold alone keeps the plain background out
        z.update(lmin=150, grow=4)
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


def smooth_around(frames, t0, t1, rect, m=14, lim=3.0):
    """True when the ground just outside rect is smooth in every sampled frame: the fine detail (luma minus its 9 px
    blur) of a 14 px frame around it has a spread <= lim. A light sweep or a vignette is a slow change, not detail."""
    x0, y0, x1, y1 = rect; sel = [L for t, L in frames if t0 <= t < t1]
    if not sel: return False
    for L in sel:
        H, W = L.shape
        X0, Y0, X1, Y1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
        c = L[Y0:Y1, X0:X1].astype(np.float32); k = np.ones(c.shape, bool); k[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = False
        hp = c - cv2.GaussianBlur(c, (0, 0), 9)
        if k.sum() < 200 or float(hp[k].std()) > lim: return False
    return True


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
    # ... and the source pixels the build itself keeps on top («orig …» layers: a price tag kept as it is). The read of
    # such a block can be smaller than the layer the build made of it, and a zone that reached 20 px into the layer
    # stopped the job in build_math.py («the cfg does not fit this build»)
    origs = [(float(e.get('time') or 0), float(e.get('time') or 0) + float(e.get('duration') or D),
              [round(v * s_ / 100) for v, s_ in zip(orig_rect(e), (W, H, W, H))])
             for e in rb['elements'] if (e.get('name') or '').startswith('orig ') and e.get('type') == 'composition']
    def clip_keep(t0, t1, r):
        """Shrink rect r off every kept box that shows during [t0, t1): vertically off a kept read, and off a kept
        source layer of the build on the side that costs the zone least."""
        x0, y0, x1, y1 = r
        for t, (kx0, ky0, kx1, ky1) in keep:
            if not (t0 - 0.3 <= t <= t1 + 0.3): continue
            if kx1 <= x0 or kx0 >= x1 or ky1 <= y0 or ky0 >= y1: continue
            above, below = ky0 - y0, y1 - ky1              # room left above / below the kept box
            if above >= below: y1 = min(y1, ky0)
            else: y0 = max(y0, ky1)
        for a0, a1, (kx0, ky0, kx1, ky1) in origs:
            if a0 >= t1 - 0.05 or a1 <= t0 + 0.05: continue
            if kx1 <= x0 or kx0 >= x1 or ky1 <= y0 or ky0 >= y1: continue
            cuts_ = [[x0, y0, x1, ky0], [x0, ky1, x1, y1], [x0, y0, kx0, y1], [kx1, y0, x1, y1]]
            x0, y0, x1, y1 = max(cuts_, key=lambda c: max(0, c[2] - c[0]) * max(0, c[3] - c[1]))
        return [x0, y0, x1, y1] if y1 - y0 >= 8 and x1 - x0 >= 8 else None
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
        # one stray read (an end-card line read as a caption) must not stretch the caption rows over half the frame:
        # with rows that tall no bare-letter page passes the two-line limit and nothing is erased
        mid = sorted((b[1] + b[3]) / 2 for b in sb)[len(sb) // 2]; lh = sorted(b[3] - b[1] for b in sb)[len(sb) // 2]
        sb = [b for b in sb if abs((b[1] + b[3]) / 2 - mid) <= max(2.5 * lh, 120)] or sb
        cy0, cy1 = min(b[1] for b in sb), max(b[3] for b in sb)
        r0 = max(0, cy0 - 40); r1 = min(H, cy1 + 40)
        db = [px(g['box']) for g in groups if g.get('role') == 'disclaimer']
        if db:                                                   # never measure the legal line as a caption
            dy0 = sorted(b[1] for b in db)[len(db) // 2]
            if r0 < dy0 < r1: r1 = dy0 - 4
        win = [(o['start'], o['end']) for o in subs]
        cap = []; radii = []
        # a dark plate that is not pure black (translucent): the plate itself is measured (find_plate), in rows that hold
        # its edges and one more text line (a two-line page when the reads show one line), still clear of the legal line
        PL = plate_reads([g for g in groups if g.get('role') in CAPTION], px)
        if PL:
            R0 = max(0, round(cy0 - PL['lh'] - 16)); R1 = min(H, round(cy1 + PL['lh'] + 16))
            if db and R0 < dy0 < R1: R1 = dy0 - 4
            PL['thr'] = plate_dark(frames, PL['stills']) or PL['thr']
            PL['rects'], cfg['auto']['plates'] = plate_rects(SRC, float(rb.get('frame_rate', 30)), W, H, (R0, R1), win, PL)
            if not PL['rects']: PL = None       # the reads name a plate the pixels do not show: the measures below, as without it
        # the sampled measures (black box, bare letters): all of them without such a plate; with one, only where no plate
        # was measured and only when some read shows a caption without a plate
        for t, f in (load_rgb(SRC, FPS_C, W, H, (r0, r1)) if not PL or PL['bare'] else []):
            if not any(s - 0.05 <= t < e + 0.05 for s, e in win): continue
            if PL and any(r[0] - 0.05 <= t < r[0] + r[1] + 0.05 for r in PL['rects']): continue
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
        # outlined captions are tried only where the captions sit on no plate and (almost) no black box: letters on a
        # box are ringed by dark too, but there the box is the thing to erase
        nbox = sum(1 for c in cap if c['box'])
        outlined = not PL and nbox < 0.2 * max(1, len(cap)) and outlined_captions(SRC, W, H, D, cy0, cy1, win, rb, cfg)
        if outlined: cap = [dict(c, text=None) for c in cap]      # their letters are the "cap" zones'; a black box stays a box
        cfg['caption_rects'] = caption_rects(cap, W, H)
        if PL: cfg['caption_rects'] = sorted(cfg['caption_rects'] + PL['rects'], key=lambda r: r[0])
        if not outlined:
            sp = [(r[0], r[0] + r[1]) for r in cfg['caption_rects']]
            # covered = of the frames that show caption letters (letters ringed by dark: on a box, on a plate, outlined),
            # the share under a rect; the caller's block times are rough (a block may run on after its last page), so
            # the block's whole time counts only when no such letters are found in the video at all
            seen = caption_frames(SRC, W, H, cy0, cy1, win)
            if len(seen) >= 0.3 * sum(e - s_ for s_, e in win) * FPS_C:
                share = []
                for a_, b_ in win:
                    fr_ = [t for t in seen if a_ - 0.05 <= t < b_ + 0.05]
                    share.append(round(sum(1 for t in fr_ if any(x - 0.06 <= t < y + 0.06 for x, y in sp)) / len(fr_), 2) if fr_ else 1.0)
            else:
                share = [covered_share(a_, b_, sp) for a_, b_ in win]
            cfg['auto']['captions'] = {'style': 'plates' if PL else 'boxes / bare letters', 'blocks': len(win), 'share': share,
                                       'letters_seen': len(seen)}
        cfg['auto']['captions']['uncovered'] = sum(1 for v in cfg['auto']['captions']['share'] if v < 0.5)
        # bare letters anywhere (captions without a box in some shots): only the measured boxes are old boxes the new box
        # covers; a bare-letter page is erased (old_boxes «all» would leave it under the new box's edges)
        bare = sum(1 for c in cap if c['text']) >= 3
        ones = [r for r in cfg['caption_rects'] if r[6] == 'B' and r[5] - r[3] < 7.3]
        tr = [e for e in rb['elements'] if e.get('type') == 'text' and e.get('transcript_source')]
        if len(ones) >= 3 and len(tr) == 1:
            # the new box's corners must cover the old box's: never rounder than the old box (measured) nor than
            # the Build's own caption plate (black here: only a source with 'B' boxes gets this far; a translucent
            # plate, kind 'P', gets no caption_pages)
            radius = next((c['elements'][2].get('border_radius') for c in rb['elements'] if c.get('type') == 'composition'
                           and len(c.get('elements', [])) == 3 and str(c['elements'][2].get('fill_color', '')).startswith('rgba(0,0,0')), '1.4 vmin')
            rv = float(str(radius).split()[0]) if str(radius).endswith('vmin') else 1.4
            if radii: rv = min(rv, sorted(radii)[len(radii) // 2] / (min(W, H) / 100))
            y0s = sorted(r[3] for r in ones); y1s = sorted(r[5] for r in ones)
            cfg['caption_pages'] = {'track': 'transcript', 'box': [round(y0s[len(y0s) // 10] - 0.25, 2), round(y1s[-1 - len(y1s) // 10] + 0.25, 2)],
                                    'old_boxes': 'B' if bare else 'all', 'radius': f'{rv:.2f} vmin', 'fill': '#000000', 'pad_x_em': 0.6, 'max_width': 90,
                                    'font': f"{tr[0].get('font_family', 'Montserrat')}:{tr[0].get('font_weight', '700')}",
                                    'local': True, 'tall_old': 'two_lines'}

    # 1b) a legal line that stays the same through several shots is one "still" text: one letter mask for all of its
    # time (found on the shots where the letters stand out), its soft shadow divided out. On a white page the line is
    # only its grey shadow, so a zone measured on that shot alone found dark specks or nothing.
    still_ok = {}
    for o in a.get('overlays', []):
        if o.get('role') != 'disclaimer': continue
        o0, o1 = float(o['start']), float(o['end'])
        sh = shots(o0, o1, cuts); g = reads('disclaimer', o0, o1)
        if len(sh) < 3 or len(g) < 3: continue
        bs = [px(x['box'], 12, 10) for x in g]; r = [sorted(v)[len(v) // 2] for v in zip(*bs)]
        near = [b for b in bs if all(abs(p - q) <= 0.03 * H for p, q in zip(b[1::2], r[1::2]))]
        if len(near) < 0.6 * len(bs): continue                               # the reads do not agree: per-shot zones
        # not clipped off the kept blocks (page buttons under the line): only the line's own letters are filled, the
        # shadow is divided out, so what sits under it stays
        r = [min(b[0] for b in near), min(b[1] for b in near), max(b[2] for b in near), max(b[3] for b in near)]
        cfg.setdefault('still', []).append({'role': 'disclaimer', 'rect_pct': [round(v * 100 / s_, 2) for v, s_ in zip(r, (W, H, W, H))],
                                            'times': [[round(o0, 3), round(o1 - o0, 3)]], 'k': 15, 'thr': 40, 'lmin': 150, 'grow': 4})
        cfg['auto']['zones'].append({'role': 'disclaimer', 'still': [round(o0, 2), round(o1, 2)], 'rect': r})
        still_ok[id(o)] = True

    # 2) every other translated block: one zone per shot over its read boxes
    for o in sorted(a.get('overlays', []), key=lambda o: o.get('role') != 'price'):   # full zones first: holes
        role = o.get('role')
        if role in CAPTION or role in KEEP and role != 'price': continue
        # a block's times come from stills, so they stop a few frames short of where the text really starts and ends:
        # the zone runs to the shot change next to it (within 0.2 s) and to the last frame of the video (within 0.5 s);
        # the old text showing through the new one on the closing frames was the result otherwise
        o0, o1 = float(o['start']), float(o['end'])
        pc = max([c for c in cuts if c < o0 + 0.05] + [0.0]); nc = min([c for c in cuts if c > o1 - 0.05] + [D])
        if 0 < o0 - pc <= 0.2: o0 = pc
        if 0 < nc - o1 <= (0.5 if nc == D else 0.2): o1 = nc
        for t0, t1 in shots(o0, o1, cuts):
            if role == 'price':
                if (o.get('text') or '').strip() not in lifted_txt: continue
                g = [x for x in reads('price', t0, t1, o.get('text')) if x.get('freeSpot')] or reads('price', t0, t1, o.get('text'))
                if g: add(t0, t1, union([px(x['box'], 8, 6) for x in g]), 'price lifted', 0, full=True)
                continue
            g = reads(role, t0, t1, None if role == 'disclaimer' else o.get('text'))
            if role == 'disclaimer' and still_ok.get(id(o)): continue          # erased as one "still" text below
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
            pack = scene_type(t0) == 'packshot'
            if pack:                                               # end-card text slides / fades in: cover its path
                r = [r[0], max(0, r[1] - 60), r[2], min(H, r[3] + 60)]
                t0 = min(t0, next((s['start'] for s in scenes if s['start'] <= t0 < s['end']), t0))
            add(t0, t1, clip_keep(t0, t1, r), role, line, pol=polarity(g))
            # while an end-card line comes in (blurred, dim, growing) its letters are below every letter threshold and
            # showed under the new line for the first frames: over a smooth background the whole rect is filled for
            # that half second (a fill of smooth ground leaves no patch). After the letter zone: a full zone is a hole
            # in the measures of the zones that follow it
            e0, e1 = max(t0, float(o['start']) - 0.15), min(t1, float(o['start']) + 0.45)
            if pack and e1 - e0 > 0.1:
                # smooth = just before the line comes and once it has settled (while it comes its own blur is in the way)
                calm = [(t, L) for t, L in frames if e0 - 0.6 <= t < e0 - 0.05 or e1 + 0.25 <= t < e1 + 0.9]
                if smooth_around(calm, 0, D + 1, r): add(e0, e1, clip_keep(e0, e1, r), role + ' entry', 0, full=True)
    json.dump(cfg, open(OUT, 'w'), ensure_ascii=False, indent=1)
    print('auto zones:', len(cfg['zones_extra']), 'zones,', len(cfg.get('caption_rects', [])), 'caption rects,',
          'caption pages' if cfg.get('caption_pages') else 'no caption pages',
          '| skipped', sum(1 for z in cfg['auto']['zones'] if z.get('skip')), *(['| plates', cfg['auto']['plates']] if cfg['auto'].get('plates') else []))


def covered_share(a, b, spans):
    """Share of [a, b) that the time spans cover."""
    if b <= a: return 1.0
    ev = sorted((max(a, x), min(b, y)) for x, y in spans if y > a and x < b); tot = 0.0; end = a
    for x, y in ev:
        if y > end: tot += y - max(x, end); end = y
    return round(tot / (b - a), 2)


# A house caption look seen on many sources: heavy caps, a thin black outline, a soft shadow just under the letters.
# When the measured shadow is that one, the new captions are drawn with the same numbers (in vmin, as the renderer takes).
HOUSE = {'font_family': 'Poppins', 'font_weight': '800', 'stroke_color': '#000000', 'stroke_width': '0.35 vmin',
         'shadow_color': 'rgba(0,0,0,0.7)', 'shadow_blur': '2 vmin', 'shadow_y': '0.3 vmin'}


def caption_frames(SRC, W, H, cy0, cy1, win):
    """Times (at FPS_C) inside the caption blocks where a line of letters ringed by dark is found in the caption rows."""
    q0, q1 = max(0, cy0 - 80), min(H, cy1 + 80); out = []
    for t, f in load_rgb(SRC, FPS_C, W, H, (q0, q1)):
        if any(s - 0.05 <= t < e + 0.05 for s, e in win) and captions.find(np.ascontiguousarray(f[..., ::-1]).astype(np.uint8), 0)['lines']:
            out.append(t)
    return out


def outlined_captions(SRC, W, H, D, cy0, cy1, win, rb, cfg):
    """Outlined captions (captions.py). Found in at least half of the caption frames -> "cap" zones, cfg "cap",
    the new caption layer's look, auto.captions; returns True. Else nothing is written and the caller measures boxes."""
    q0, q1 = max(0, cy0 - 80), min(H, cy1 + 80)
    fr = [(t, np.ascontiguousarray(f[..., ::-1]).astype(np.uint8)) for t, f in load_rgb(SRC, FPS_C, W, H, (q0, q1))
          if any(s - 0.05 <= t < e + 0.05 for s, e in win)]
    if len(fr) < 6: return False
    hs = [r['cap_h'] for r in (captions.find(f, 0) for _, f in fr[::2]) if r['lines']]
    if len(hs) < max(5, 0.5 * len(fr[::2])): return False
    ch = sorted(hs)[len(hs) // 2]
    found = [(t, captions.find(f, ch)) for t, f in fr]
    lines = [(t, l) for t, r in found for l in r['lines']]
    if len(lines) < 5: return False
    fit = captions.fit_shadow([f for (t, f), (_, r) in zip(fr, found) if r['lines']], ch)
    reach = max(fit['reach'], 12) + 8                       # rows the zone needs past the lines: the shadow + fill context
    y0 = max(0, q0 + min(l[1] for _, l in lines) + int(0.45 * ch) - reach - int(fit['core']))
    y1 = min(H, q0 + max(l[3] for _, l in lines) - int(0.45 * ch) + reach + int(fit['core']) + 6)
    blocks = []                                             # caption blocks that follow each other = one zone
    for a, b in sorted(win):
        if blocks and a - blocks[-1][1] <= 1.0: blocks[-1][1] = max(blocks[-1][1], b)
        else: blocks.append([a, b])
    for a, b in blocks:
        a, b = max(0.0, a - 0.3), min(D, b + 0.3)
        z = [round(a, 3), round(b - a, 3), 0, round(y0 * 100 / H, 2), 100, round(y1 * 100 / H, 2), 'cap', int(ch), 0, 0]
        cfg['zones_extra'].append(z); cfg['auto']['zones'].append({'role': 'subtitle', 'z': z})
    grow = 0 if fit['shadow'] else max(0, fit['reach'] - int(fit['core']))
    cfg['cap'] = {'cap_h': int(ch), 'core': fit['core'], 'shadow': fit['shadow'], 'grow': grow, 'colors': []}
    on = [(t, t + 1 / FPS_C) for t, r in found if r['lines']]
    cfg['auto']['captions'] = {'style': 'outlined', 'blocks': len(win), 'share': [covered_share(a, b, on) for a, b in win],
                               'cap_h': int(ch), 'fit': {k: fit[k] for k in ('core', 'shadow', 'reach', 'err', 'n')}}
    # the new caption layer takes the measured look: size from the capital height (0.70 em in heavy sans caps), the
    # house numbers when the measured shadow is the house one, else the measured outline and shadow
    tr = [e for e in rb['elements'] if e.get('type') == 'text' and e.get('transcript_source')]
    if len(tr) == 1:
        vmin = min(W, H) / 100; sh = fit['shadow']
        # size and row measured on a render of the house look next to its source: the fill of a capital is 0.6845 em
        # tall, and a centred one-line text element puts the capitals' middle 0.073 em under its own y
        fs = ch / 0.6845; mid = q0 + float(np.median([(l[1] + l[3]) / 2 for _, l in lines]))
        look = {'font_size': f'{fs / vmin:.2f} vmin', 'font_size_minimum': None, 'font_size_maximum': None, 'width': '92%'}
        if str(tr[0].get('y_alignment', '50%')) == '50%' and len({round(l[1] / (0.5 * ch)) for _, l in lines}) <= 2:
            look['y'] = f'{(mid - 0.073 * fs) * 100 / H:.2f}%'
        if sh and 0.6 <= sh[1] <= 0.85 and 9 <= sh[2] <= 12.5 and 1.5 <= sh[3] <= 4.5 and fit['core'] <= 8:
            look.update(HOUSE)
        else:
            look.update({'stroke_color': '#000000', 'stroke_width': f'{max(1.0, fit["core"] - 3) / vmin:.2f} vmin'})
            if sh: look.update({'shadow_color': f'rgba(0,0,0,{sh[1]:.2f})', 'shadow_blur': f'{2 * sh[2] / vmin:.2f} vmin', 'shadow_y': f'{sh[3] / vmin:.2f} vmin'})
            else: look.update({'shadow_color': None, 'shadow_blur': None, 'shadow_y': None})
        cfg.setdefault('text_set', {})['transcript'] = look
    return True


def caption_rects(F, W, H, DT=1 / FPS_C):
    """Per-frame caption measures -> merged rects [t, d, x0, y0, x1, y1 (%), 'B' | 'T'] (as segs.py). The third kind,
    'P' (a dark translucent plate), is not sampled: plate_rects makes those rects, one per page, frame-exact."""
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
