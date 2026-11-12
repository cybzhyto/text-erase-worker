"""Erase old on-screen text from a video by per-letter masks + push-pull (normalized-convolution) fill.
No generated pixels: every filled pixel is a Gaussian-weighted average of the real pixels around the letters.
usage: python3 plate.py ZONES.json SRC.mp4 OUT.mp4            -> the clean plate (x264 crf 12, no audio)
       python3 plate.py ZONES.json SRC.mp4 OUTDIR --at 0.5,6,17.5   -> preview pngs (orig | plate | mask)
zones: [t0, t1, x0, y0, x1, y1 (px), mode light|dark|both|full, k (morph kernel > stroke width), thr (luma step), grow (px),
        lmin (optional, default 150: a light letter must be brighter than this; 60 on dark backgrounds so fade-ins are caught),
        static (optional, 0 = off): the text does not move during [t0, t1), so a pixel that is a letter in >= this share of
        the window's frames is erased in EVERY frame of it. Catches the frames where the per-frame mask misses letters
        (white letters over a light shot, fade-ins); a one-off false hit stays below the share. Prepass cached in
        ZONES.json.static.npz (run `plate.py ZONES SRC - --static-only` once before parallel chunks).]
       python3 plate.py ZONES.json SRC.mp4 - --static-only           -> only build the static-mask cache"""
import json, subprocess, sys, collections, time
import numpy as np, cv2
Zf, SRC, OUT = sys.argv[1:4]
J = json.load(open(Zf)); W, H, FPS = J['w'], J['h'], J['fps']; Z = J['zones']
def on(z, t):   # zone z covers the frame at time t; by frame number, as the renderer shows an element: zone times are rounded
    return round(z[0] * FPS) <= round(t * FPS) < round(z[1] * FPS)   # (0.067 > 2/30: a plain time test skipped every 3rd frame of a tracked zone)
R = 2           # the per-frame mask is unioned over +-R frames of the same shot
CUT = 25        # mean |grey step| of a 64 x 114 thumbnail between two frames = a new shot
def thumb(I): return cv2.resize(cv2.cvtColor(I, cv2.COLOR_BGR2GRAY), (64, 114)).astype(np.float32)
KER = {}
def ker(k):
    if k not in KER: KER[k] = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return KER[k]
def raw_sel(L, z, thr_scale=1.0):
    """The zone's letter pixels in one frame (before grow), 255 / 0, cropped to the zone rect; None for an empty rect."""
    t0, t1, x0, y0, x1, y1, mode, k, thr, grow = z[:10]; lmin = z[10] if len(z) > 10 else 150
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 <= x0 or y1 <= y0: return None
    if mode == 'full': return np.full((y1 - y0, x1 - x0), 255, np.uint8)
    p = k; X0, Y0, X1, Y1 = max(0, x0 - p), max(0, y0 - p), min(W, x1 + p), min(H, y1 + p)
    c = L[Y0:Y1, X0:X1]
    # light = white / yellow letters (outline or shadow is caught by grow), dark = dark letters on a light scene
    s = np.zeros(c.shape, bool)
    if mode in ('light', 'both'):
        s |= (cv2.morphologyEx(c, cv2.MORPH_TOPHAT, ker(k)) > thr * thr_scale) & (c > lmin)
    if mode in ('dark', 'both'):
        s |= cv2.morphologyEx(c, cv2.MORPH_BLACKHAT, ker(k)) > thr * thr_scale
    return (s * 255).astype(np.uint8)[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0]
def static_masks(want=None, share=None, cf=None):
    """Zone index -> letter mask of a static text (pixels hit in >= share of the window's frames), cached next to ZONES."""
    import os
    if want is None:
        want = {i: z for i, z in enumerate(Z) if len(z) > 11 and z[11] and z[6] != 'full'}; cf = Zf + '.static.npz'
    if not want: return {}
    if cf and os.path.exists(cf) and os.path.getmtime(cf) >= os.path.getmtime(Zf):
        d = np.load(cf); return {int(k): d[k] for k in d.files}
    cnt = {i: None for i in want}; n = {i: 0 for i in want}
    t_lo, t_hi = min(z[0] for z in want.values()), max(z[1] for z in want.values())
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-ss', f'{t_lo:.6f}', '-t', f'{t_hi - t_lo + 0.1:.6f}', '-i', SRC, '-f', 'rawvideo',
                          '-pix_fmt', 'bgr24', '-'], stdout=subprocess.PIPE)
    j = round(t_lo * FPS)
    while True:
        b = p.stdout.read(W * H * 3)
        if len(b) < W * H * 3: break
        t = j / FPS; j += 1
        act = [i for i, z in want.items() if on(z, t)]
        if not act: continue
        L = cv2.cvtColor(np.frombuffer(b, np.uint8).reshape(H, W, 3), cv2.COLOR_BGR2GRAY)
        for i in act:
            s = raw_sel(L, want[i])
            if s is None: continue
            cnt[i] = (s > 0).astype(np.int32) if cnt[i] is None else cnt[i] + (s > 0); n[i] += 1
    out = {i: ((cnt[i] >= max(2, (share or want[i][11]) * n[i])) * 255).astype(np.uint8) for i in want if cnt[i] is not None}
    if cf: np.savez_compressed(cf, **{str(i): m for i, m in out.items()})
    print('static masks:', {i: (n[i], int((m > 0).sum())) for i, m in out.items()}, flush=True)
    return out
ST = {}
def frame_mask(I, t, split=False):
    """The frame's erase mask; split=True -> (per-frame part, static part). Only the per-frame part is unioned with the
    neighbour frames (of the same shot): a static mask borrowed across a cut blurred the frames just before an end card."""
    act = [(i, z) for i, z in enumerate(Z) if on(z, t)]
    if not act: return (None, None) if split else None
    L = cv2.cvtColor(I, cv2.COLOR_BGR2GRAY)
    M = np.zeros((H, W), np.uint8); MS = np.zeros((H, W), np.uint8)
    for i, z in act:
        sel = raw_sel(L, z)
        if sel is None: continue
        x0, y0, grow = max(0, z[2]), max(0, z[3]), z[9]
        for part, out in ((sel, M), (ST.get(i), MS)):
            if part is None or part.shape != sel.shape: continue
            g = np.zeros((H, W), np.uint8); g[y0:y0 + sel.shape[0], x0:x0 + sel.shape[1]] = part
            if grow: g = cv2.dilate(g, ker(2 * grow + 1))
            out |= g
    if split: return M, (MS if MS.any() else None)
    return M | MS
def pushpull(I, Wt, need=0.35):
    h, w = Wt.shape; levels = 0
    while min(h, w) >> (levels + 1) >= 4 and levels < 7: levels += 1
    nums, dens = [I * Wt[..., None]], [Wt]
    for _ in range(levels): nums.append(cv2.pyrDown(nums[-1])); dens.append(cv2.pyrDown(dens[-1]))
    F = nums[-1] / np.maximum(dens[-1], 1e-6)[..., None]
    for l in range(levels - 1, -1, -1):
        up = cv2.pyrUp(F, dstsize=(nums[l].shape[1], nums[l].shape[0]))
        a = np.clip(dens[l] / need, 0, 1)[..., None]
        F = a * (nums[l] / np.maximum(dens[l], 1e-6)[..., None]) + (1 - a) * up
    return F
def clean(I, M):
    if M is None or not M.any(): return I
    O = I.copy(); m = 64
    # one fill per horizontal band of mask rows (caption, disclaimer, headline are separate bands): small ROIs = fast
    rows = np.flatnonzero(M.any(1)); bands = []; a = rows[0]; b = rows[0]
    for r in rows[1:]:
        if r - b > 2 * m: bands.append((a, b)); a = r
        b = r
    bands.append((a, b))
    for a, b in bands:
        xs = np.flatnonzero(M[a:b + 1].any(0))
        X0, Y0, X1, Y1 = max(0, xs[0] - m), max(0, a - m), min(W, xs[-1] + m + 1), min(H, b + m + 1)
        Ic = O[Y0:Y1, X0:X1].astype(np.float32); Mc = M[Y0:Y1, X0:X1].astype(np.float32) / 255
        F = pushpull(Ic, 1 - Mc)
        Mf = np.clip(cv2.GaussianBlur(Mc, (0, 0), 1.5) * 1.6, 0, 1)[..., None]
        O[Y0:Y1, X0:X1] = np.clip(Ic * (1 - Mf) + F * Mf, 0, 255).astype(np.uint8)
    return O
F0 = int(sys.argv[sys.argv.index('--from') + 1]) if '--from' in sys.argv else 0       # chunking: 4 cores = 4 processes
FN = int(sys.argv[sys.argv.index('--count') + 1]) if '--count' in sys.argv else 10 ** 9
def frames():
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-ss', f'{F0 / FPS:.6f}', '-i', SRC, '-frames:v', str(FN), '-f', 'rawvideo',
                          '-pix_fmt', 'bgr24', '-'], stdout=subprocess.PIPE)
    n = W * H * 3; i = F0
    while True:
        b = p.stdout.read(n)
        if len(b) < n: break
        yield i, np.frombuffer(b, np.uint8).reshape(H, W, 3); i += 1
ST = static_masks()
if '--static-only' in sys.argv: sys.exit()
if '--check' in sys.argv:
    # QA: letters of the OLD text still visible in a finished plate (OUT = the plate). For every letter zone, its static
    # letter mask (hit in >= 30 % of the window's source frames) is compared with a 3x more sensitive detection on the
    # plate; a frame where > MIN px of the old letters still stand out is listed (and a crop saved next to the plate).
    MIN = int(sys.argv[sys.argv.index('--check') + 1]) if len(sys.argv) > sys.argv.index('--check') + 1 and sys.argv[sys.argv.index('--check') + 1].isdigit() else 40
    CK = static_masks({i: z for i, z in enumerate(Z) if z[6] != 'full' and z[1] - z[0] >= 0.5}, 0.3)
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', OUT, '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'], stdout=subprocess.PIPE)
    j = 0; hits = collections.defaultdict(list)
    while True:
        b = p.stdout.read(W * H * 3)
        if len(b) < W * H * 3: break
        t = j / FPS; j += 1
        act = [i for i in CK if on(Z[i], t)]
        if not act: continue
        I = np.frombuffer(b, np.uint8).reshape(H, W, 3); L = cv2.cvtColor(I, cv2.COLOR_BGR2GRAY)
        for i in act:
            r = raw_sel(L, Z[i], 0.35)
            if r is None or r.shape != CK[i].shape: continue
            n = int(((r > 0) & (CK[i] > 0)).sum())
            if n > MIN: hits[i].append((round(t, 2), n))
    for i, h in sorted(hits.items()):
        z = Z[i]; print('zone %d %s %.2f-%.2f rect %s: %d frames, worst %s' % (i, z[6], z[0], z[1], z[2:6], len(h), max(h, key=lambda q: q[1])))
    if not hits: print('check: no old letters left above', MIN, 'px')
    # Part 2, blur off the text: plate vs source outside the rects (+ grow + 24 px) of the zones active in this frame or
    # in a neighbour frame of the same shot (where the union may borrow). Any frame with > 300 changed px is listed: that
    # is a blur where no text was.
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', OUT, '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'], stdout=subprocess.PIPE)
    src = frames(); win = {}; prev = None; off = []
    def judge(j):
        d, _ = win[j]; ok = np.zeros((H, W), np.uint8)
        for k in range(j - R, j + R + 1):
            if k not in win or any(win[m][1] for m in range(min(j, k) + 1, max(j, k) + 1) if m in win): continue
            for z in Z:
                if on(z, k / FPS):
                    g = z[9] + 24; ok[max(0, z[3] - g):z[5] + g, max(0, z[2] - g):z[4] + g] = 1
        n = int((d & (ok == 0)).sum())
        if n > 300: off.append((round(j / FPS, 2), n))
    for b in iter(lambda: p.stdout.read(W * H * 3), b''):
        if len(b) < W * H * 3: break
        j, SI = next(src); PI = np.frombuffer(b, np.uint8).reshape(H, W, 3)
        th = thumb(SI); cut = prev is not None and float(np.abs(th - prev).mean()) > CUT; prev = th
        dd = cv2.GaussianBlur(cv2.absdiff(cv2.cvtColor(SI, cv2.COLOR_BGR2GRAY), cv2.cvtColor(PI, cv2.COLOR_BGR2GRAY)), (5, 5), 0) > 22
        win[j] = (dd, cut)
        if j - R in win: judge(j - R); win.pop(j - 2 * R - 1, None)
    for j in sorted(k for k in win if k > max(win) - R): judge(j)
    print('check: blur off the text in %d frames %s' % (len(off), off[:20]) if off else 'check: no blur off the text')
    sys.exit()
if '--at' in sys.argv:
    import os; os.makedirs(OUT, exist_ok=True)
    sm = lambda x: cv2.resize(x, (W // 2, H // 2), interpolation=cv2.INTER_AREA)
    for t in [float(x) for x in sys.argv[sys.argv.index('--at') + 1].split(',')]:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(t), '-i', SRC, '-frames:v', '1', f'{OUT}/src_{t}.png'], check=True)
        I = cv2.imread(f'{OUT}/src_{t}.png'); M = frame_mask(I, t); O = clean(I, M)
        Mv = cv2.cvtColor(M if M is not None else np.zeros((H, W), np.uint8), cv2.COLOR_GRAY2BGR)
        cv2.imwrite(f'{OUT}/plate_{t}.png', O)
        cv2.imwrite(f'{OUT}/cmp_{t}.jpg', np.hstack([sm(I), sm(O), sm(Mv)]), [cv2.IMWRITE_JPEG_QUALITY, 82])
    sys.exit()
# full video: temporal union of the masks over +-R frames (text is static per page; kills threshold flicker at letter rims)
enc = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                        '-c:v', 'libx264', '-preset', 'fast', '-crf', '12', '-pix_fmt', 'yuv420p', OUT], stdin=subprocess.PIPE)
# Never across a cut (CUT above): a mask borrowed from the other shot flashed a 1-2-frame blur blotch where that shot had
# no text. Within a shot the union stays: it also covers
# text that outlives its zone by a frame (rounded rect times) and the CTA track's gaps.
buf = {}; nxt = F0; last = -1; prev = None
def emit(j):
    def same_shot(k): return not any(buf[m][3] for m in range(min(j, k) + 1, max(j, k) + 1))
    ms = [buf[k][1] for k in range(j - R, j + R + 1) if k in buf and buf[k][1] is not None and same_shot(k)]
    if buf[j][2] is not None: ms.append(buf[j][2])          # the static part: this frame only
    enc.stdin.write(clean(buf[j][0], np.maximum.reduce(ms) if ms else None).tobytes())
    buf.pop(j - R, None)
for i, I in frames():
    th = thumb(I); cut = prev is not None and float(np.abs(th - prev).mean()) > CUT; prev = th
    buf[i] = (I, *frame_mask(I, i / FPS, split=True), cut); last = i
    while nxt <= i - R: emit(nxt); nxt += 1
    if i % 150 == 0: print('frame', i, time.strftime('%H:%M:%S'), flush=True)
while nxt <= last: emit(nxt); nxt += 1
enc.stdin.close(); enc.wait(); print('done', OUT, last + 1, 'frames')
