"""Letters of outlined captions: a bright fill with a crisp dark outline, any background.

Top-hat masks (plate.py modes light / dark) need the letters to stand out of their surround, so white letters over a
white coat or a white app page are missed. An outlined letter does not depend on the background: its fill is an island
of the fill colour whose rim is the dark outline all the way round. So:
  island  = a 4-connected component of fill-coloured pixels, letter sized
  ringed  = the pixels 2-3 px outside it are dark (share >= RING)
  strong  = a ringed island as tall as a capital letter; strong islands on one row are a text line
  letters = every ringed island inside a line's rect (punctuation and counters included)
A white coat, a white card, a pearl on skin are islands too, but nothing dark rings them. Measured on test videos: letter
islands 0.77-1.00 dark in the ring, everything else in the caption rows 0.00-0.10.
The erase mask is the islands grown by `grow` px: the outline (3-4 px) and the soft shadow past it (~9 px)."""
import numpy as np, cv2

WHITE = 225      # every channel of a white fill pixel is at least this (H.264 keeps a white fill at 250+)
DARK = 140       # every channel of an outline pixel is at most this (the antialiased rim sits around 60-110)
RING = 0.72      # dark share of the ring of a strong island
RING_IN = 0.55   # of a small island inside a line's rect
K3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
K7 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))


def fill_mask(bgr, colors=()):
    """Fill-coloured pixels: white, or within 40 per channel of one of `colors` ([b, g, r], e.g. a highlighted word)."""
    m = bgr.min(2) >= WHITE
    for c in colors:
        m |= (np.abs(bgr.astype(np.int16) - np.array(c, np.int16)).max(2) <= 40)
    return m


def islands(bgr, colors=(), max_h=110, max_w=170):
    """Letter-sized fill islands of a BGR crop: ([x0, y0, x1, y1, area, ring share, label], label image)."""
    F = fill_mask(bgr, colors).astype(np.uint8)
    D = bgr.max(2) <= DARK
    n, lab, st, _ = cv2.connectedComponentsWithStats(F, connectivity=4)
    H, W = F.shape; out = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if a < 12 or h > max_h or w > max_w: continue
        X0, Y0, X1, Y1 = max(0, x - 4), max(0, y - 4), min(W, x + w + 4), min(H, y + h + 4)
        c = (lab[Y0:Y1, X0:X1] == i).astype(np.uint8)
        ring = (cv2.dilate(c, K7) > 0) & (cv2.dilate(c, K3) == 0)        # 2-3 px out: past the antialiased rim
        if ring.sum() < 8: continue
        out.append([x, y, x + w, y + h, a, float(D[Y0:Y1, X0:X1][ring].mean()), i])
    return out, lab


def find(bgr, cap_h=0, colors=()):
    """The outlined text lines of a BGR crop.
    -> {'cap_h': capital height px, 'lines': [[x0, y0, x1, y1]], 'strong': n, 'mask': uint8 0/255 of the letters' fill
        (not grown)}; no line -> lines [] and an empty mask.
    cap_h 0 = take it from this crop (the median strong-candidate height; needs two of them)."""
    isl, lab = islands(bgr, colors)
    H, W = bgr.shape[:2]
    M = np.zeros((H, W), np.uint8)
    if not cap_h:
        hs = sorted(q[3] - q[1] for q in isl if q[5] >= RING and 24 <= q[3] - q[1] <= 100)
        if len(hs) < 2: return {'cap_h': 0, 'lines': [], 'strong': 0, 'mask': M}
        cap_h = hs[len(hs) // 2]
    strong = [q for q in isl if q[5] >= RING and 0.72 * cap_h <= q[3] - q[1] <= 1.3 * cap_h]
    rows = []                                        # strong islands grouped by their vertical centre
    for q in sorted(strong, key=lambda q: q[1] + q[3]):
        cy = (q[1] + q[3]) / 2
        if rows and abs(cy - rows[-1][0]) <= 0.45 * cap_h:
            rows[-1][1].append(q); rows[-1][0] = float(np.mean([(p[1] + p[3]) / 2 for p in rows[-1][1]]))
        else: rows.append([cy, [q]])
    lines = []
    for _, it in rows:
        # a stray ringed island far from the text is not part of the line: keep the widest run of letters
        # (a gap of more than 2.2 capital heights ends a run; a word space is about 0.5)
        it.sort(key=lambda q: q[0]); runs = [[it[0]]]
        for q in it[1:]:
            if q[0] - max(p[2] for p in runs[-1]) <= 2.2 * cap_h: runs[-1].append(q)
            else: runs.append([q])
        run = max(runs, key=lambda r: sum(q[2] - q[0] for q in r))
        if len(run) < 2: continue                    # one ringed island alone is not a caption
        m = int(round(0.45 * cap_h))
        top = float(np.median([q[1] for q in run])); base = float(np.median([q[3] for q in run]))
        X0, X1 = min(q[0] for q in run), max(q[2] for q in run)
        inside = [q for q in isl if q[5] >= RING_IN and q[3] - q[1] <= 1.3 * cap_h and X0 <= (q[0] + q[2]) / 2 <= X1
                  and top - m <= (q[1] + q[3]) / 2 <= base + m]             # between the capitals: counters, commas, dashes
        # past the first / last capital only punctuation joins, glyph by glyph: a small ringed island that sits on the
        # base line (full stop, comma), hangs from the capitals' top (quote, apostrophe) or is a dash, and starts within
        # 0.36 capital heights of the glyph before it. A ringed dot in the middle of the row next to the text (a radio
        # button, the counter of a digit of the page under the caption) is not the caption's
        def punct(q):
            h, w = q[3] - q[1], q[2] - q[0]
            if h > 0.6 * cap_h: return False
            return abs(q[3] - base) <= 0.25 * cap_h or abs(q[1] - top) <= 0.2 * cap_h or (w >= 2.2 * h and top < (q[1] + q[3]) / 2 < base)
        ends = list(inside)
        for _ in range(6):
            lo, hi = min([X0] + [q[0] for q in ends]), max([X1] + [q[2] for q in ends])
            add = [q for q in isl if q not in ends and q[5] >= RING_IN and punct(q) and top - m <= (q[1] + q[3]) / 2 <= base + m
                   and (0 <= lo - q[2] <= 0.36 * cap_h or 0 <= q[0] - hi <= 0.36 * cap_h)]
            if not add: break
            ends += add
        R = [min(q[0] for q in ends + run) - m, min(q[1] for q in run) - m, max(q[2] for q in ends + run) + m, max(q[3] for q in run) + m]
        lines.append(R)
        for q in ends: M[lab == q[6]] = 255
    return {'cap_h': int(cap_h), 'lines': lines, 'strong': len(strong), 'mask': M}


# ---- erase: the letters are filled from the pixels around them; their soft shadow is undone, not blurred ----
def pushpull(I, Wt, need=0.35):
    """Normalized-convolution fill (pull-push): every pixel = a Gaussian-weighted average of the pixels with weight."""
    h, w = Wt.shape; levels = 0
    while min(h, w) >> (levels + 1) >= 4 and levels < 7: levels += 1
    nums, dens = [I * Wt[..., None]], [Wt]
    for _ in range(levels): nums.append(cv2.pyrDown(nums[-1])); dens.append(cv2.pyrDown(dens[-1]))
    F = nums[-1] / np.maximum(dens[-1], 1e-6)[..., None]
    hole = dens[-1] < 1e-4                        # a hole wider than the coarsest level reaches: its middle had no known
    if hole.any() and not hole.all():             # pixel at all and came out black; it takes the known pixels' mean
        F[hole] = nums[-1].sum((0, 1)) / float(dens[-1].sum())
    for l in range(levels - 1, -1, -1):
        up = cv2.pyrUp(F, dstsize=(nums[l].shape[1], nums[l].shape[0]))
        a = np.clip(dens[l] / need, 0, 1)[..., None]
        F = a * (nums[l] / np.maximum(dens[l], 1e-6)[..., None]) + (1 - a) * up
    return F


def shadow_alpha(dist, sh):
    """Alpha of a soft drop shadow at every pixel. dist = distance (px) from the letters' fill; sh = [r, A, sigma, dy]:
    the shadow is the text shape (the fill grown by r px: fill + outline), blurred by a Gaussian of sigma px, moved dy px
    down, at strength A. A text renderer draws exactly that under the letters, so outside them the frame holds
    background x (1 - alpha)."""
    r, A, sig, dy = sh
    S = (dist <= r).astype(np.float32)
    a = cv2.GaussianBlur(S, (0, 0), sig)
    if dy: a = cv2.warpAffine(a, np.float32([[1, 0, 0], [0, 1, dy]]), (S.shape[1], S.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return A * a


def erase(bgr, cap_h, colors=(), shadow=None, core=7.0, grow=0):
    """Erase the outlined caption lines of a BGR crop -> (cleaned crop, number of lines found).
    shadow [r, A, sigma, dy]: the soft shadow is divided out (the background keeps its real texture right up to the
    letters) and only the letters (fill + outline = `core` px past the fill) are filled from the pixels around them.
    No shadow model: the letters grown by `grow` px more are filled (a soft patch as wide as the shadow reached)."""
    r = find(bgr, cap_h, colors)
    if not r['lines']: return bgr, 0
    return erase_mask(bgr, r['mask'], shadow, core, grow), len(r['lines'])


def erase_mask(bgr, mask, shadow=None, core=7.0, grow=0):
    """Erase the letters of `mask` (uint8, the letters' fill) from a BGR crop: shadow divided out, letters filled."""
    if not mask.any(): return bgr
    d = cv2.distanceTransform((mask == 0).astype(np.uint8), cv2.DIST_L2, 5)
    I = bgr.astype(np.float32)
    E = d <= core + grow
    if shadow:
        a = shadow_alpha(d, shadow)
        I = np.clip(I / np.maximum(1 - a, 0.2)[..., None], 0, 255)
        E |= a >= 0.42                           # between and inside letters the shadow is deep: dividing it out is not exact
    # outline past the core (the mitred tips of A, V, W; a thicker outline than measured): pixels near the letters that
    # are still much darker than the background around the letters
    E0 = E.astype(np.float32); Lg = I @ np.float32([0.114, 0.587, 0.299])
    den = cv2.GaussianBlur(1 - E0, (0, 0), 6); bgl = cv2.GaussianBlur(Lg * (1 - E0), (0, 0), 6) / np.maximum(den, 1e-3)
    spur = (~E) & (d <= core + grow + 9) & (Lg < 0.62 * bgl - 6) & (den > 0.05)
    if spur.any(): E |= cv2.dilate(spur.astype(np.uint8), K3) > 0
    E = E.astype(np.float32)
    F = pushpull(I, 1 - E)
    Mf = np.clip(cv2.GaussianBlur(E, (0, 0), 1.5) * 1.6, 0, 1)[..., None]
    out = I * (1 - Mf) + F * Mf
    if shadow:                                   # untouched pixels stay bit-exact: only where the shadow reached
        keep = (a < 0.004) & (Mf[..., 0] == 0)
        out[keep] = bgr[keep]
    else:
        keep = Mf[..., 0] == 0; out[keep] = bgr[keep]
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def profile(bgr, cap_h, colors=()):
    """Median luma by distance (1..45 px) from the letters' fill, None per distance without pixels; None with no line."""
    r = find(bgr, cap_h, colors)
    if not r['lines']: return None
    L = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    d = cv2.distanceTransform((r['mask'] == 0).astype(np.uint8), cv2.DIST_L2, 5)
    x0, y0, x1, y1 = r['lines'][0]; box = np.zeros(L.shape, bool); box[max(0, y0 - 40):y1 + 40, max(0, x0 - 40):x1 + 40] = True
    return L, d, box


def fit_shadow(crops, cap_h=0, colors=(), masks=None):
    """Measure the captions' outline reach and soft shadow on BGR band crops (frames with a caption).
    -> {'core': px past the fill that hold the outline, 'shadow': [r, A, sigma, dy] | None, 'reach': px the shadow is
        visible, 'err': mean |restored - background| in luma levels of the fit, 'n': frames used}
    Uses the frames whose surround (30-45 px from the letters) is the flattest (robust spread <= 8 luma levels): there
    the background level is known, so the darkening by distance is the shadow alone."""
    cand = []
    for j, c in enumerate(crops):
        if masks is not None:                     # a given letter mask per crop (a static text): no line search
            if not masks[j].any(): continue
            L = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY).astype(np.float32)
            d = cv2.distanceTransform((masks[j] == 0).astype(np.uint8), cv2.DIST_L2, 5); box = d <= 60
            p = (L, d, box)
        else:
            p = profile(c, cap_h, colors)
        if p is None: continue
        L, d, box = p
        far = L[(d > 30) & (d <= 45) & box]
        near = L[(d > 6) & (d <= 30) & box]
        if far.size < 400 or near.size < 400: continue
        md = float(np.median(far))                # flat = most of the surround at one level (a button or a line of
        cand.append((1.4826 * float(np.median(np.abs(far - md))), md, L, d, box))   # page text in it is an outlier)
    cand.sort(key=lambda q: q[0])
    use = [q for q in cand[:6] if q[0] <= 8]
    out = {'core': 7.0, 'shadow': None, 'reach': 0, 'err': None, 'n': len(use)}
    if not use: return out
    # the outline: the last distance whose median is still much darker than the surround, on bright surrounds
    br = [q for q in use if q[1] >= 150]
    if br:
        ends = []
        for _, bg, L, d, box in br:
            e = 0
            for k in range(1, 14):
                s = (d > k - 1) & (d <= k) & box
                if s.any() and np.median(L[s]) < 0.62 * bg: e = k
            ends.append(e)
        out['core'] = float(min(12, max(5, sorted(ends)[len(ends) // 2] + 3)))
    # how dark the ring just past the outline is, relative to the surround
    dk = []; reach = []
    for _, bg, L, d, box in use:
        c0 = out['core']
        s = (d > c0 + 1) & (d <= c0 + 4) & box
        if s.any() and bg > 30: dk.append(1 - float(np.median(L[s])) / bg)
        rr = 0
        for k in range(int(c0) + 1, 40):
            s = (d > k - 1) & (d <= k) & box
            if s.any() and np.median(L[s]) < bg - max(3.0, 0.015 * bg): rr = k
        reach.append(rr)
    out['reach'] = int(sorted(reach)[len(reach) // 2]) if reach else 0
    if not dk or sorted(dk)[len(dk) // 2] < 0.04:
        return out                                # no shadow past the outline
    # the search: one blur per (frame, radius, sigma); strength and offset are cheap on top of it. Each frame is cut to
    # the letters' surround and its ring pixels thinned to 6000 at most, so the whole grid takes a second or two
    rng = np.random.default_rng(1); prep = []
    for _, bg, L, d, box in use:
        ys, xs = np.nonzero(d <= 40); y0, y1, x0, x1 = max(0, ys.min() - 8), ys.max() + 9, max(0, xs.min() - 8), xs.max() + 9
        Lc, dc, bc = L[y0:y1, x0:x1], d[y0:y1, x0:x1], box[y0:y1, x0:x1]
        idx = np.flatnonzero((dc >= out['core']) & (dc <= 32) & bc)
        if idx.size > 6000: idx = rng.choice(idx, 6000, replace=False)
        prep.append((bg, Lc.ravel()[idx], dc, idx))
    As = np.array((0.3, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.9, 1.0), np.float32)
    best = None
    for r in (2.5, 3.0, 3.5, 4.0, 5.0, 6.0):
        for sig in (4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16):
            G = [cv2.GaussianBlur((dc <= r).astype(np.float32), (0, 0), sig) for _, _, dc, _ in prep]
            for dy in (0, 1.5, 3, 4.5, 6):
                err = np.zeros(len(As), np.float32)
                for (bg, Lv, dc, idx), g in zip(prep, G):
                    gs = cv2.warpAffine(g, np.float32([[1, 0, 0], [0, 1, dy]]), (g.shape[1], g.shape[0]), flags=cv2.INTER_LINEAR,
                                        borderMode=cv2.BORDER_REPLICATE).ravel()[idx] if dy else g.ravel()[idx]
                    e = np.abs(Lv[None, :] / np.maximum(1 - As[:, None] * gs[None, :], 0.2) - bg)        # strengths x pixels
                    k = max(1, int(0.8 * e.shape[1]))                    # other content near the text is an outlier
                    err += np.partition(e, k - 1, axis=1)[:, :k].mean(1)
                err /= len(prep); j = int(np.argmin(err))
                if best is None or err[j] < best[0]: best = (float(err[j]), [r, round(float(As[j]), 2), float(sig), dy])
    base = 0.0                                    # the same measure with no shadow model
    for bg, Lv, dc, idx in prep:
        e = np.abs(Lv - bg); k = max(1, int(0.8 * e.size)); base += float(np.partition(e, k - 1)[:k].mean())
    base /= len(prep)
    out['err'] = round(best[0], 2); out['base'] = round(base, 2)
    if best[0] <= 3.0 and best[0] <= 0.5 * base: out['shadow'] = best[1]
    return out
