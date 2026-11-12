"""Measure the source's own caption boxes / caption text and the disclaimer background, frame by frame (10 fps),
so the blur variant can put a patch exactly where the old old text was.
usage: python3 measure.py SRC.mp4 T0 T1 UNBOXED_UNTIL OUT.json"""
import subprocess, sys, json, numpy as np
S, T0, T1, UNB, OUT = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
W, H, FPS = 1080, 1920, 10
Y0, Y1 = 1150, 1620                      # caption band + disclaimer rows
DY0, DY1 = 1546, 1592                    # old disclaimer rows (Tint 805–829 ‰)
p = subprocess.Popen(['ffmpeg', '-v', 'error', '-ss', str(T0), '-t', str(T1 - T0), '-i', S, '-vf', f'fps={FPS},crop={W}:{Y1-Y0}:0:{Y0}',
                      '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)
n = W * (Y1 - Y0) * 3; i = 0; frames = []
while True:
    b = p.stdout.read(n)
    if len(b) < n: break
    a = np.frombuffer(b, np.uint8).reshape(Y1 - Y0, W, 3).astype(np.int16)
    t = round(T0 + i / FPS, 2); i += 1
    L = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    rec = {'t': t, 'box': None, 'text': None}
    # 1) opaque black caption box through the centre column (lum < 4), rows 1150–1530
    M = L[:380] < 4
    rows = []
    for r in range(M.shape[0]):
        if not M[r, 540]: continue
        x0 = 540
        while x0 > 0 and M[r, x0 - 1]: x0 -= 1
        x1 = 540
        while x1 < W - 1 and M[r, x1 + 1]: x1 += 1
        if x1 - x0 >= 80: rows.append((r, x0, x1))
    if len(rows) >= 12:
        ys = [r for r, _, _ in rows]
        if max(ys) - min(ys) >= 40:
            rec['box'] = [min(x for _, x, _ in rows), min(ys) + Y0, max(x for _, _, x in rows), max(ys) + Y0]
    # 2) unboxed white / yellow caption text (only before UNB: later scenes have white app screens)
    if rec['box'] is None and t < UNB:
        A = a[80:300]                                          # rows 1230–1450
        mx, mn = A.max(2), A.min(2); LA = L[80:300]
        T = (LA > 225) & (((mx - mn) < 45) | ((A[..., 0] > 200) & (A[..., 1] > 170) & (A[..., 2] < 150)))
        if T.sum() >= 250:
            cols = np.nonzero(T.sum(0) >= 3)[0]; rws = np.nonzero(T.sum(1) >= 3)[0]
            rec['text'] = [int(cols.min()), int(rws.min()) + 1230, int(cols.max()), int(rws.max()) + 1230]
    # 3) disclaimer background: median of the rows just above and below the old disclaimer
    ring = np.concatenate([a[DY0 - 14 - Y0:DY0 - 2 - Y0, 103:975].reshape(-1, 3), a[DY1 + 2 - Y0:DY1 + 14 - Y0, 103:975].reshape(-1, 3)])
    rec['bg'] = [int(v) for v in np.median(ring, 0)]; rec['sd'] = round(float(np.std(0.299*ring[:,0]+0.587*ring[:,1]+0.114*ring[:,2])), 1)
    frames.append(rec)
json.dump(frames, open(OUT, 'w'))
print(len(frames), 'frames;', sum(1 for f in frames if f['box']), 'with box;', sum(1 for f in frames if f['text']), 'with text')
