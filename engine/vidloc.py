"""Localize a looping end-card video whose words stand still (AI-made interactive end cards, HTML end cards): erase the old
words from the real pixels, draw the new words in their place, wrap the video in a playable HTML page.

usage: python3 vidloc.py PLAN.json SRC.mp4 WORKDIR        -> WORKDIR/out.mp4, out.html, sheet.jpg, report.json

PLAN (written by the caller from its read of the video):
  {"blocks": [{"key": "head", "rect": [x0, y0, x1, y1] (px, generous: the letters must be inside),
               "old": ["line 1", …] (the words on screen now, one string per line),
               "new": ["línea 1", …] (the words to draw, one string per line),
               "font": "sans" | "serif" | "script", "ink": "dark" | "light", "role": "headline" | "cta" | …}],
   "clickUrl": "https://…", "title": "…", "lang": "es", "check": 40}

How (one end card, every frame):
1. Letters, per block: black-hat (dark letters) or top-hat (light letters: the side of the rect's border colour most far pixels sit on) over
   the block's rect, kernel ~ a third of a line; a pixel is a letter when it is one in >= STATIC of the frames (the words stand
   still; leaves and clothes that move are not letters). Components that touch the rect's edge or are far taller than a line
   are dropped (a mirror frame, a sleeve). One mask per block, grown GROW px for the antialiased edge.
2. Fill = push-pull (normalized convolution) of the real pixels around the mask, frame by frame: the button glow and the moving
   picture keep their own pixels; no generated pixels (same fill as plate.py).
3. Check: the old letters' pixels that still stand out of the filled frame (detector 3x more sensitive), against the same rate
   just around them (a photo's texture answers inside and outside alike); a block with letters standing out in RESIDUE or more
   frames = the job stops (old words would show under the new ones).
4. New words: the font class from the plan (sans, serif, script: a few Google Fonts each, FONTS; the read may name the family), size
   and weight measured on the line's coverage (luma between background and letter colour): the old line drawn at 100 px gives
   the size that matches its height, and the weight whose ink share (mass / box) matches the measured one; letter spacing when the old words are spread wider than the font draws them. Colour
   = the median of the old letters' core pixels. Each line centred where the old line was (same line count) or the block centred
   on the old block; a line wider than the room around it (the run of background colour on its row: a button, the frame) is
   drawn smaller, down to SHRINK of the size, else the job stops; a line wider than the old block + 10 % is first eased down to
   EASE of the size (Spanish runs ~20 % longer: the headline keeps its shape).
   A block set at a slant (a hand-written note) is measured and drawn in a frame turned level about its centre (the turn whose
   row profile is sharpest), then the new words are turned back by the same angle.
5. Page: the mp4 inline (data URI), muted / playsinline / loop, waits for MRAID ready and viewable, any tap -> mraid.open(clickUrl);
   the video fits inside the screen, the page background = the video's edge colour."""
import base64, json, os, subprocess, sys, urllib.request, re
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont

STATIC = 0.5      # share of frames a pixel must be a letter in
GROW = 3          # px around the letters (antialiased edge, a soft shadow)
THR = 32          # luma step of a letter over its surround
RESIDUE = 6       # frames with old letters left that stop a block (of ~145)
SHRINK = 0.72     # smallest size a long line may be drawn at, as a share of the measured size
FAMILY_SLACK = 1.5  # a block takes its class's lead family when that scores within this factor of its own best
EASE = 0.92       # a longer translation is first drawn smaller down to this share to stay within the old block's width + 10 %
FONTS = {   # Google Fonts per class, with the static weights used; the closest family + weight to the old words is picked
    'sans': {'Inter': [400, 500, 600, 700], 'DM Sans': [400, 500, 600, 700], 'Montserrat': [400, 500, 600, 700]},
    'serif': {'Playfair Display': [400, 500, 600, 700], 'Instrument Serif': [400], 'DM Serif Display': [400],
              'Cormorant Garamond': [400, 500, 600, 700], 'Libre Bodoni': [400, 500, 600, 700]},
    'script': {'Dancing Script': [400, 700], 'Caveat': [400, 700], 'Great Vibes': [400]}}


def font_file(fam, wt):
    out = os.path.expanduser(f'~/.cache/loc-math/{fam.replace(" ", "")}-{wt}.ttf')
    if not os.path.exists(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        css = urllib.request.urlopen(urllib.request.Request(
            f'https://fonts.googleapis.com/css2?family={fam.replace(" ", "+")}:wght@{wt}', headers={'User-Agent': 'Mozilla/4.0'})).read().decode()
        urllib.request.urlretrieve(re.search(r'url\((https://[^)]+\.ttf)\)', css).group(1), out)
    return out


def probe(path):
    o = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,r_frame_rate',
                                   '-show_entries', 'format=duration', '-of', 'json', path], capture_output=True, text=True, check=True).stdout)
    s = o['streams'][0]; n, d = s['r_frame_rate'].split('/')
    return s['width'], s['height'], float(n) / float(d), float(o['format']['duration'])


def read_frames(path, W, H):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)


def ker(k): return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def detect(gray, mode, k, thr):
    if mode == 'dark': return cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, ker(k)) > thr
    return (cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, ker(k)) > thr) & (gray > 120)


def pushpull(I, Wt, need=0.35):
    """plate.py's fill: normalized convolution over a Gaussian pyramid (a hole becomes the blur of the pixels around it)."""
    h, w = Wt.shape; levels = 0
    while min(h, w) >> (levels + 1) >= 4 and levels < 7: levels += 1
    nums, dens = [I * Wt[..., None]], [Wt]
    for _ in range(levels): nums.append(cv2.pyrDown(nums[-1])); dens.append(cv2.pyrDown(dens[-1]))
    F = nums[-1] / np.maximum(dens[-1], 1e-6)[..., None]
    hole = dens[-1] < 1e-4
    if hole.any() and not hole.all(): F[hole] = nums[-1].sum((0, 1)) / float(dens[-1].sum())
    for l in range(levels - 1, -1, -1):
        up = cv2.pyrUp(F, dstsize=(nums[l].shape[1], nums[l].shape[0]))
        a = np.clip(dens[l] / need, 0, 1)[..., None]
        F = a * (nums[l] / np.maximum(dens[l], 1e-6)[..., None]) + (1 - a) * up
    return F


def fill(I, M, box, m=48):
    x0, y0, x1, y1 = box; H, W = M.shape
    X0, Y0, X1, Y1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
    Ic = I[Y0:Y1, X0:X1].astype(np.float32); Mc = M[Y0:Y1, X0:X1].astype(np.float32) / 255
    F = pushpull(Ic, 1 - Mc)
    Mf = np.clip(cv2.GaussianBlur(Mc, (0, 0), 1.5) * 1.6, 0, 1)[..., None]
    O = I.copy(); O[Y0:Y1, X0:X1] = np.clip(Ic * (1 - Mf) + F * Mf, 0, 255).astype(np.uint8)
    return O


def letters(frames, med, rect, nlines, ink=None):
    """The block's static letter mask (full frame size, before grow), its mode, kernel and the per-frame counts.
    ink = 'dark' | 'light' from the read (it sees the letters); without it, the side of the rect's border colour wins."""
    H, W = med.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in rect]; x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    lh = max(8, (y1 - y0) / max(1, nlines))
    k = int(np.clip(round(lh * 0.33) | 1, 7, 41))
    if ink in ('dark', 'light'): mode = ink
    else:
        # the background = the median of the rect's border ring; the letters = the pixels far from it, and their side wins (a
        # hat filter's response does not tell: the red gaps between white letters answer a black-hat too)
        g = cv2.cvtColor(med[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        ring = np.concatenate([g[:2].ravel(), g[-2:].ravel(), g[:, :2].ravel(), g[:, -2:].ravel()]).astype(int)
        diff = g.astype(int) - int(np.median(ring))
        mode = 'dark' if int((diff < -THR).sum()) >= int((diff > THR).sum()) else 'light'
    # a read's box is often tight (an ascender on its edge would be dropped as «runs on past it»): look a third of a line wider
    rx0, ry0, rx1, ry1 = x0, y0, x1, y1
    pad = int(max(6, 0.33 * lh)); x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
    cnt = np.zeros((y1 - y0, x1 - x0), np.int32)
    for I in frames:
        cnt += detect(cv2.cvtColor(I[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), mode, k, THR)
    sel = (cnt >= STATIC * len(frames)).astype(np.uint8)
    # drop what is not a letter: components touching the wider box's edge (something that runs on past it), centred outside the
    # read's box (a mirror frame, a button's rim next to the words), far taller than a line, or a long thin rule
    n, lab, st, _ = cv2.connectedComponentsWithStats(sel, connectivity=8)
    keep = np.zeros_like(sel)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < 3: continue
        if x == 0 or y == 0 or x + w >= sel.shape[1] or y + h >= sel.shape[0]: continue
        cx, cy = x0 + x + w / 2, y0 + y + h / 2
        if not (rx0 <= cx <= rx1 and ry0 <= cy <= ry1): continue
        if h > 1.6 * lh: continue
        if w > 3 * lh and h < 0.15 * lh: continue
        keep[lab == i] = 1
    M = np.zeros((H, W), np.uint8); M[y0:y1, x0:x1] = keep * 255
    # the measure mask: only components as bright (light letters) or as dark as the words themselves. A rug's or a leaf's static
    # highlight is erased with the words (harmless: it is filled from its own surroundings) but must not widen a line or move
    # its size (a card over a photo: rug bits at luma 120-170 next to letters at 235)
    g = cv2.cvtColor(med[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    n, lab, st, _ = cv2.connectedComponentsWithStats(keep, connectivity=8)
    lev, area = np.zeros(n), st[:, 4].astype(float)
    for i in range(1, n):
        x, y, w, h, a = st[i]; px = g[y:y + h, x:x + w][lab[y:y + h, x:x + w] == i]
        lev[i] = np.percentile(px, 90 if mode == 'light' else 10)
    Mm = M.copy()
    if n > 2:
        big = [i for i in range(1, n) if area[i] >= np.percentile(area[1:], 50)]
        word = float(np.median(np.repeat(lev[big], np.maximum(1, (area[big] / 10).astype(int)))))
        ringv = np.concatenate([g[:2].ravel(), g[-2:].ravel(), g[:, :2].ravel(), g[:, -2:].ravel()])
        gap = abs(word - float(np.median(ringv)))
        drop = [i for i in range(1, n) if abs(lev[i] - word) > 0.35 * max(gap, 3 * THR)]
        if drop:
            sub = Mm[y0:y1, x0:x1]; sub[np.isin(lab, drop)] = 0
    # and no stray speck: a small component further than a fifth of a line from every letter-sized one (a flower bit, a rug fleck) would
    # stretch its line's box; a dot or an accent sits right next to its letter and stays
    sub = (Mm[y0:y1, x0:x1] > 0).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(sub, connectivity=8)
    if n > 2:
        area = st[1:, 4]; big = np.isin(lab, 1 + np.flatnonzero(area >= 0.5 * np.median(area)))
        D = cv2.distanceTransform((~big).astype(np.uint8), cv2.DIST_L2, 3)
        far = [i for i in range(1, n) if st[i, 4] < 0.5 * np.median(area) and D[lab == i].min() > 0.2 * lh]
        if far: Mm[y0:y1, x0:x1][np.isin(lab, far)] = 0
    return M, Mm, mode, k, (x0, y0, x1, y1)


def rows_of(M, n=None):
    """Line boxes of a letter mask: runs of rows with ink, split at empty gaps (an accent's gap is closed first)."""
    ys = np.flatnonzero(M.any(1))
    if not len(ys): return []
    runs, a, b = [], ys[0], ys[0]
    for y in ys[1:]:
        if y - b > 2: runs.append((a, b)); a = y
        b = y
    runs.append((a, b))
    # an accent or a dot is a thin run right above a line: merge runs thinner than a third of the tallest into their neighbour
    tall = max(b - a + 1 for a, b in runs); out = []
    for a, b in runs:
        if out and (b - a + 1 < tall / 3 or out[-1][1] - out[-1][0] + 1 < tall / 3) and a - out[-1][1] < tall / 2:
            out[-1] = (out[-1][0], b)
        else: out.append((a, b))
    if n and n > 1 and len(out) != n:
        # tight leading: a descender of one line reaches the next line's ascenders and no empty row is left between them. Cut
        # where the row profile is lowest, near each expected line break (the read knows how many lines there are)
        y0, y1 = ys[0], ys[-1]; prof = np.convolve(M[y0:y1 + 1].sum(1).astype(float), np.ones(3) / 3, 'same'); step = (y1 - y0 + 1) / n
        cuts = []
        for i in range(1, n):
            c = i * step; lo, hi = int(max(1, c - 0.35 * step)), int(min(len(prof) - 2, c + 0.35 * step))
            cuts.append(y0 + lo + int(np.argmin(prof[lo:hi + 1])))
        out = []
        for a, b in zip([y0] + [c + 1 for c in cuts], cuts + [y1]):
            r = np.flatnonzero(M[a:b + 1].any(1))
            if len(r): out.append((a + int(r[0]), a + int(r[-1])))
    lines = []
    for a, b in out:
        xs = np.flatnonzero(M[a:b + 1].any(0)); lines.append((int(xs[0]), int(a), int(xs[-1]), int(b)))
    return lines


def ink_box(font, text, track=0):
    """Ink bbox of a text drawn at the font's size (track = extra px between letters)."""
    if track:
        w = sum(font.getlength(c) for c in text) + track * (len(text) - 1)
        b = font.getbbox(text); return (0, b[1], int(round(w)), b[3])
    return font.getbbox(text)


def glyphs(font, text):
    """(ink mass, height, width) of a text drawn at the font's size: mass = the sum of its coverage, the box = coverage >= 0.5."""
    im = Image.new('L', (int(font.getlength(text)) + 40, int(font.size * 2) + 40), 0)
    ImageDraw.Draw(im).text((20, 20), text, font=font, fill=255)
    A = np.asarray(im) / 255.0; ys, xs = np.nonzero(A >= 0.5)
    if not len(xs): return 0.0, 0, 0
    return float(A.sum()), int(ys.max() - ys.min() + 1), int(xs.max() - xs.min() + 1)


def coverage(med, box, fg, bg, pad=3):
    """The same measure of a line on screen: coverage = (pixel - background) / (letter - background) in luma, 0..1."""
    H, W = med.shape[:2]; x0, y0, x1, y1 = box
    X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad + 1), min(H, y1 + pad + 1)
    g = cv2.cvtColor(med[Y0:Y1, X0:X1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    C = np.clip((g - bg) / (fg - bg if abs(fg - bg) > 1 else 1), 0, 1); ys, xs = np.nonzero(C >= 0.5)
    if not len(xs): return 0.0, y1 - y0 + 1, x1 - x0 + 1
    return float(C.sum()), int(ys.max() - ys.min() + 1), int(xs.max() - xs.min() + 1)


def skew(Mm, rect):
    """Degrees to turn a block so its lines lie level (a hand-written note set at a slant): the turn whose row profile is the
    sharpest, coarse then fine. Under 2 degrees counts as level."""
    x0, y0, x1, y1 = rect; m = (Mm[y0:y1, x0:x1] > 0).astype(np.uint8)
    if not m.any(): return 0.0
    side = int(np.hypot(*m.shape)) + 4; c = np.zeros((side, side), np.uint8)
    oy, ox = (side - m.shape[0]) // 2, (side - m.shape[1]) // 2; c[oy:oy + m.shape[0], ox:ox + m.shape[1]] = m

    def sharp(a):
        r = cv2.warpAffine(c, cv2.getRotationMatrix2D((side / 2, side / 2), float(a), 1.0), (side, side), flags=cv2.INTER_NEAREST)
        return float(np.var(r.sum(1).astype(float)))
    a0 = max(np.arange(-25, 26, 1.0), key=sharp)
    a1 = max(np.arange(a0 - 1, a0 + 1.01, 0.25), key=sharp)
    return float(a1) if abs(a1) >= 2 else 0.0


def draw_line(d, xy, text, font, fill, anchor, track=0):
    if not track:
        d.text(xy, text, font=font, fill=fill, anchor=anchor); return
    w = sum(font.getlength(c) for c in text) + track * (len(text) - 1)
    x = xy[0] - (w / 2 if anchor[0] == 'm' else 0)
    for c in text:
        d.text((x, xy[1]), c, font=font, fill=fill, anchor='l' + anchor[1]); x += font.getlength(c) + track


def room(med, M, line, H, W):
    """Width available to a line: the run of background colour on its middle row around the letters when it is a container (a
    button, a plain panel: the run reaches at least a third of the line's height past the letters on both sides); over a photo the
    run stops at the first texture next to the letters, so the room is the frame."""
    x0, y0, x1, y1 = line; yc = (y0 + y1) // 2
    row = med[yc].astype(int); bgx = [x for x in range(max(0, x0 - 6), max(0, x0 - 2))] + [x for x in range(min(W - 1, x1 + 2), min(W, x1 + 6))]
    bg = np.median(row[bgx], axis=0) if bgx else row[max(0, x0 - 3)]
    near = (np.abs(row - bg).sum(1) < 54) | (M[yc] > 0)
    L = x0
    while L > 0 and near[L - 1]: L -= 1
    R = x1
    while R < W - 1 and near[R + 1]: R += 1
    reach = max(6, (y1 - y0) / 3)
    if x0 - L < reach or R - x1 < reach: return 0, W - 1
    return L, R


def main():
    plan_p, src, work = sys.argv[1:4]
    os.makedirs(work, exist_ok=True)
    P = json.load(open(plan_p))
    W, H, FPS, DUR = probe(src)
    F = read_frames(src, W, H)
    med = np.median(F, axis=0).astype(np.uint8)
    report = {'w': W, 'h': H, 'fps': FPS, 'dur': DUR, 'frames': len(F), 'blocks': []}
    masks, meas, infos, turns = [], [], [], []
    for b in P['blocks']:
        M, Mm, mode, k, rect = letters(F, med, b['rect'], len(b.get('old') or [1]), b.get('ink'))
        if not Mm.any(): raise SystemExit(f'block {b["key"]}: no letters found in its rect')
        # a slanted block is measured and drawn in a frame turned level about its centre, then turned back
        ang = skew(Mm, rect); Rm = None; medb = med
        if ang:
            cxy = ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2); Rm = cv2.getRotationMatrix2D(cxy, ang, 1.0)
            medb = cv2.warpAffine(med, Rm, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            Mm = cv2.warpAffine(Mm, Rm, (W, H), flags=cv2.INTER_NEAREST)
        turns.append((ang, Rm, medb))
        lines = rows_of(Mm, len(b.get('old') or []))
        core = cv2.erode(Mm, ker(3)) > 0
        col = np.median(medb[core if core.any() else Mm > 0], axis=0)          # BGR
        around = (cv2.dilate(Mm, ker(9)) > 0) & ~(cv2.dilate(Mm, ker(5)) > 0)
        bgc = np.median(medb[around], axis=0) if around.any() else col
        lum = lambda c: 0.114 * c[0] + 0.587 * c[1] + 0.299 * c[2]
        masks.append(M); meas.append(Mm); infos.append(dict(key=b['key'], mode=mode, k=k, rect=rect, lines=lines, color=[int(c) for c in col[::-1]], fgL=round(float(lum(col)), 1), bgL=round(float(lum(bgc)), 1),
                                           ink=int((M > 0).sum()), angle=round(ang, 2)))
    def stop(msg):                                   # the job stops; the report says what was measured up to here
        json.dump({**report, 'blocks': infos, 'error': msg}, open(f'{work}/report.json', 'w'), indent=1, ensure_ascii=False)
        raise SystemExit(msg)

    G = np.zeros((H, W), np.uint8)
    for M in masks: G |= M
    G = cv2.dilate(G, ker(2 * GROW + 1))
    ys, xs = np.nonzero(G); box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    # 2. fill every frame, 3. check what is left of the old letters
    plate = np.empty_like(F)
    left = [0] * len(masks)
    rings = []
    for M, inf in zip(masks, infos):
        x0, y0, x1, y1 = inf['rect']; m = M[y0:y1, x0:x1]
        rings.append((cv2.dilate(m, ker(2 * GROW + 9)) > 0) & ~(cv2.dilate(m, ker(2 * GROW + 3)) > 0))
    for i, I in enumerate(F):
        O = fill(I, G, box); plate[i] = O
        g = cv2.cvtColor(O, cv2.COLOR_BGR2GRAY)
        for j, (M, inf) in enumerate(zip(masks, infos)):
            x0, y0, x1, y1 = inf['rect']
            # a residue = the old letters' shape still answers the detector: more of the letter pixels light up than of the pixels
            # around them (a photo's own texture answers too, inside and outside alike)
            r = detect(g[y0:y1, x0:x1], inf['mode'], inf['k'], THR * 0.35); m = M[y0:y1, x0:x1] > 0
            r_in = int((r & m).sum()); r_out = float(r[rings[j]].mean()) if rings[j].any() else 0.0
            if r_in > int(P.get('check', 40)) and r_in / max(1, m.sum()) > max(0.03, 2.5 * r_out): left[j] += 1
    for j, inf in enumerate(infos): inf['residueFrames'] = left[j]
    bad = [f'{inf["key"]} in {inf["residueFrames"]} frames' for inf in infos if inf['residueFrames'] >= RESIDUE]
    if bad and not P.get('allowResidue'): stop('old letters left after the erase: ' + ', '.join(bad))

    # 4. the new words. Fonts first, for all blocks: per block and per family + weight, size = the median over its lines of the
    # height of the >= 0.5 coverage box against the old line drawn at 100 px (blur and the encoder spread a letter's edge but keep
    # its ink); score = the median over lines of |log(drawn width / measured width)| + half the gap of ink share (mass / box).
    # (a plain-ground card: Inter 400 / 500 / 600 = 0.21 / 0.24 / 0.275, measured 0.24 = 500; a narrow display serif over a
    # photo = Instrument Serif, not Playfair.)
    # Blocks of one role (three icon labels) share one family + weight; within a class, every role takes the family of the
    # class's biggest words when it scores within FAMILY_SLACK of its own best (a card's sub is set in its headline's family).
    cand = []
    for b, inf, (_, _, medb) in zip(P['blocks'], infos, turns):
        cls = b.get('font') if b.get('font') in FONTS else 'sans'
        old = [t for t in (b.get('old') or []) if t.strip()]; L = inf['lines']
        pair = list(zip(old, L)) if len(old) == len(L) else [(max(old, key=len) if old else (b.get('new') or [''])[0],
                                                                max(L, key=lambda q: q[2] - q[0]))]
        ms = []
        for t, q in pair:
            mm, mh_, mw_ = coverage(medb, q, inf['fgL'], inf['bgL']); ms.append((t, mh_, mw_, mm / max(1, mh_ * mw_)))
        fams = {b['family']: FONTS[cls][b['family']]} if b.get('family') in FONTS[cls] else FONTS[cls]
        sc = {}
        for fm, wts in fams.items():
            for wt in wts:
                f100 = ImageFont.truetype(font_file(fm, wt), 100)
                sz = float(np.median([100.0 * mh_ / max(1, glyphs(f100, t)[1]) for t, mh_, _, _ in ms]))
                f_ = ImageFont.truetype(font_file(fm, wt), max(8, int(round(sz)))); per = []
                for t, mh_, mw_, sh_ in ms:
                    r_mass, rh, rw = glyphs(f_, t)
                    if rh: per.append(abs(np.log(rw / max(1, mw_))) + 0.5 * abs(r_mass / (rh * rw) - sh_) / max(sh_, 1e-3))
                if per: sc[(fm, wt)] = (float(np.median(per)), max(8.0, sz))
        cand.append(dict(cls=cls, role=b.get('role') or b['key'], ms=ms, sc=sc, mh=float(np.median([m[1] for m in ms]))))
    groups = {}
    for i, c in enumerate(cand): groups.setdefault((c['cls'], c['role']), []).append(i)
    pick = {}
    for g, idx in groups.items():                          # one family + weight per role: the lowest summed score
        keys = set.intersection(*[set(cand[i]['sc']) for i in idx])
        pick[g] = min(keys, key=lambda k_: sum(cand[i]['sc'][k_][0] for i in idx))
    for cls in {g[0] for g in groups}:
        gs = [g for g in groups if g[0] == cls]
        lead = max(gs, key=lambda g: max(cand[i]['mh'] for i in groups[g])); fam0 = pick[lead][0]
        for g in gs:
            if g == lead or pick[g][0] == fam0: continue
            own = sum(cand[i]['sc'][pick[g]][0] for i in groups[g])
            alt = [k_ for k_ in set.intersection(*[set(cand[i]['sc']) for i in groups[g]]) if k_[0] == fam0]
            if alt:
                k_ = min(alt, key=lambda k2: sum(cand[i]['sc'][k2][0] for i in groups[g]))
                if sum(cand[i]['sc'][k_][0] for i in groups[g]) <= FAMILY_SLACK * own + 0.02 * len(groups[g]): pick[g] = k_

    layer = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
    for b, inf, M, c, (ang, Rm, medb) in zip(P['blocks'], infos, meas, cand, turns):
        old = [t for t in (b.get('old') or []) if t.strip()]; new = [t for t in (b.get('new') or []) if t.strip()]
        if not new: stop(f'block {b["key"]}: no new words')
        L = inf['lines']; ms = c['ms']
        fam, wt = pick[(c['cls'], c['role'])]; size = c['sc'][(fam, wt)][1]
        # letter spacing: capitals spread wider than the font draws them (a CTA)
        f = ImageFont.truetype(font_file(fam, wt), int(round(size)))
        spread = [(mw_ - glyphs(f, s_)[2]) / max(1, len(s_) - 1) for s_, _, mw_, _ in ms if len(s_) > 3 and mw_ > glyphs(f, s_)[2] * 1.12]
        track = float(np.median(spread)) if len(spread) * 2 > len(ms) else 0.0
        # room per line, placement
        centred = len(L) == 1 or np.ptp([(q[0] + q[2]) / 2 for q in L]) <= np.ptp([q[0] for q in L])   # else left-aligned
        cx = float(np.mean([(q[0] + q[2]) / 2 for q in L])); lx = min(q[0] for q in L)
        rooms = [room(medb, M, q, H, W) for q in L]
        avail = min(R - Lx for Lx, R in rooms) + 1
        margin = 0.05 * W; mh = float(np.median([m[1] for m in ms]))
        oldw = max(q[2] - q[0] for q in L) + 1
        maxw = max(min(W - 2 * margin, avail - 0.12 * mh * 2), oldw * 1.05)
        pref = oldw * 1.10

        def fit(lines_, lo):
            sz_ = size
            while True:
                f_ = ImageFont.truetype(font_file(fam, wt), int(round(sz_)))
                wd = max(ink_box(f_, t_, track * sz_ / size)[2] - ink_box(f_, t_, track * sz_ / size)[0] for t_ in lines_)
                # first keep the block's look: as wide as the old words + 10 %, down to EASE of the size; then the room, down to lo
                if wd <= maxw and (wd <= pref or sz_ <= size * EASE): return sz_, f_, wd
                if sz_ <= size * lo: return sz_, f_, wd
                sz_ -= 1
        sz, f, widest = fit(new, SHRINK)
        pitch = float(np.mean(np.diff([(q[1] + q[3]) / 2 for q in L]))) if len(L) > 1 else None
        rewrapped = False
        if widest > maxw:
            # one or two more lines, the words wrapped to the room (fine print over a narrow panel); the block must stay on the
            # screen and off every other block
            words = ' '.join(new).split()
            for nl in (len(new) + 1, len(new) + 2):
                sz_try = size * EASE
                while sz_try >= size * SHRINK:
                    f_ = ImageFont.truetype(font_file(fam, wt), int(round(sz_try))); out_, cur = [], ''
                    for w_ in words:
                        t_ = (cur + ' ' + w_).strip()
                        if cur and ink_box(f_, t_)[2] - ink_box(f_, t_)[0] > maxw: out_.append(cur); cur = w_
                        else: cur = t_
                    out_.append(cur)
                    if len(out_) <= nl and max(ink_box(f_, t_)[2] - ink_box(f_, t_)[0] for t_ in out_) <= maxw: break
                    sz_try -= 1
                else: continue
                pt = (pitch or sz_try * 1.25) * sz_try / size; mid = (L[0][1] + L[-1][3]) / 2
                top, bot = mid - len(out_) * pt / 2, mid + len(out_) * pt / 2
                clash = [o['key'] for o in infos if o is not inf and not (bot < o['rect'][1] or top > o['rect'][3])
                         and not (max(q[2] for q in L) < o['rect'][0] or min(q[0] for q in L) > o['rect'][2])]
                if top >= 0.01 * H and bot <= 0.99 * H and not clash:
                    new, sz, f, rewrapped = out_, sz_try, f_, True
                    widest = max(ink_box(f_, t_)[2] - ink_box(f_, t_)[0] for t_ in out_); pitch = pt * size / sz_try; break
        if widest > maxw: stop(f'block {b["key"]}: the new words are {widest}px wide, the room is {int(maxw)}px even at {int(sz)}px')
        tr = track * sz / size
        if len(new) == len(L) and not rewrapped:
            ycs = [(q[1] + q[3]) / 2 for q in L]
        else:
            pt = (pitch or sz * 1.25 / (sz / size)) * sz / size
            mid = (L[0][1] + L[-1][3]) / 2; ycs = [mid + (i - (len(new) - 1) / 2) * pt for i in range(len(new))]
        rgb = tuple(inf['color']) + (255,)
        lay, dd = (layer, d) if not ang else (lambda im: (im, ImageDraw.Draw(im)))(Image.new('RGBA', (W, H), (0, 0, 0, 0)))
        for s_, yc in zip(new, ycs):
            draw_line(dd, (cx if centred else lx, yc), s_, f, rgb, 'mm' if centred else 'lm', tr)
        if ang:
            back = cv2.warpAffine(np.asarray(lay), cv2.invertAffineTransform(Rm), (W, H), flags=cv2.INTER_LINEAR)
            layer.alpha_composite(Image.fromarray(back, 'RGBA'))
        inf.update(font=f'{fam}:{wt}', size=int(round(sz)), measured=round(size, 1), track=round(tr, 2), maxw=int(maxw), widest=int(widest),
                   old=old, new=new, rewrapped=rewrapped)

    A = np.asarray(layer).astype(np.float32); al = A[..., 3:4] / 255.0; bgr = A[..., [2, 1, 0]]
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', f'{FPS}', '-i', '-',
                          '-c:v', 'libx264', '-preset', 'slow', '-crf', str(P.get('crf', 22)), '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an',
                          f'{work}/out.mp4'], stdin=subprocess.PIPE)
    last = None
    for O in plate:
        last = np.clip(O * (1 - al) + bgr * al, 0, 255).astype(np.uint8); p.stdin.write(last.tobytes())
    p.stdin.close(); p.wait()
    if p.returncode: stop('ffmpeg could not write the video')

    # sheet: old | new, the middle frame
    mid = len(F) // 2
    newmid = np.clip(plate[mid] * (1 - al) + bgr * al, 0, 255).astype(np.uint8)
    sheet = np.concatenate([F[mid], np.full((H, 12, 3), 255, np.uint8), newmid], 1)
    sc = 900 / sheet.shape[0]
    cv2.imwrite(f'{work}/sheet.jpg', cv2.resize(sheet, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 85])

    # 5. the page
    edge = np.concatenate([med[:4].reshape(-1, 3), med[-4:].reshape(-1, 3), med[:, :4].reshape(-1, 3), med[:, -4:].reshape(-1, 3)])
    bgc = '#%02X%02X%02X' % tuple(int(v) for v in np.median(edge, 0)[::-1])
    b64 = base64.b64encode(open(f'{work}/out.mp4', 'rb').read()).decode()
    html = PAGE.replace('__LANG__', P.get('lang', 'en')).replace('__TITLE__', esc(P.get('title') or 'Ad')).replace('__BG__', bgc) \
        .replace('__CFG__', json.dumps({'clickUrl': P['clickUrl']}).replace('</', '<\\/')).replace('__SRC__', 'data:video/mp4;base64,' + b64)
    open(f'{work}/out.html', 'w', encoding='utf-8').write(html)
    report.update(blocks=infos, bg=bgc, mp4Bytes=os.path.getsize(f'{work}/out.mp4'), htmlBytes=len(html.encode()))
    json.dump(report, open(f'{work}/report.json', 'w'), indent=1, ensure_ascii=False)
    print(json.dumps({k: report[k] for k in ('frames', 'mp4Bytes', 'htmlBytes', 'bg')}))


def esc(s): return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


PAGE = '''<!DOCTYPE html>
<html lang="__LANG__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>__TITLE__</title>
<style>html,body{margin:0;height:100%;background:__BG__;overflow:hidden;-webkit-tap-highlight-color:transparent}#v{position:fixed;left:0;top:0;width:100%;height:100%;object-fit:contain;background:__BG__}</style>
</head>
<body>
<video id="v" muted playsinline loop preload="auto" src="__SRC__"></video>
<script type="application/json" id="cfg">__CFG__</script>
<script>
var CFG = JSON.parse(document.getElementById('cfg').textContent);
function play() { var v = document.getElementById('v'); var p = v.play && v.play(); if (p && p.catch) p.catch(function () {}); }
function start() {
  if (window.mraid && mraid.isViewable && !mraid.isViewable()) mraid.addEventListener('viewableChange', function (on) { if (on) play(); });
  else play();
}
if (window.mraid) { if (mraid.getState() === 'loading') mraid.addEventListener('ready', start); else start(); } else start();
document.addEventListener('click', function () { if (window.mraid && mraid.open) mraid.open(CFG.clickUrl); else window.open(CFG.clickUrl); });
</script>
</body>
</html>
'''

if __name__ == '__main__':
    main()
