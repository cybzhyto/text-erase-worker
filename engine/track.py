"""Track a moving flat-colour element (a CTA pill that bounces) so its old text can be erased and the new text rides it.
usage: python3 track.py SRC.mp4 T0 T1 X0 Y0 X1 Y1 R G B [TOL] > track.json
Per frame: the largest blob of pixels within TOL of colour (R, G, B) inside the search rect (px) -> its centre and an
inner rect (8 px in from the left/right, 7 px from top/bottom). Output: {"keys": [[t, cx, cy]], "zones": [[t, t + 1/fps,
x0, y0, x1, y1, "light", 15, 40, 2, 110]]} -> cfg zones_px (erase light letters inside the pill, frame by frame) and
CAPTIONS.json page "keys" / cfg text_y_keys (the new text follows the pill centre).
Why: a pill that bounces ~20 px under a static new label shows the old label in half the frames."""
import cv2, json, sys
import numpy as np
src, t0, t1, x0, y0, x1, y1, R, G, B = sys.argv[1], *map(float, sys.argv[2:4]), *map(int, sys.argv[4:8]), *map(int, sys.argv[8:11])
tol = int(sys.argv[11]) if len(sys.argv) > 11 else 40
cap = cv2.VideoCapture(src); fps = cap.get(cv2.CAP_PROP_FPS) or 30
cap.set(cv2.CAP_PROP_POS_FRAMES, round(t0 * fps)); i = round(t0 * fps); keys, zones = [], []
while i < round(t1 * fps):
    ok, f = cap.read()
    if not ok: break
    r = f[y0:y1, x0:x1].astype(int)
    m = (np.abs(r[..., 2] - R) < tol) & (np.abs(r[..., 1] - G) < tol) & (np.abs(r[..., 0] - B) < tol)
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:
        k = 1 + int(np.argmax(st[1:, 4])); x, y, w, h, a = (int(v) for v in st[k]); t = round(i / fps, 4)
        keys.append([t, x0 + x + w / 2, y0 + y + h / 2])
        zones.append([t, round(t + 1 / fps, 4), x0 + x + 12, y0 + y + 7, x0 + x + w - 12, y0 + y + h - 7, 'light', 15, 40, 2, 110])
    i += 1
json.dump({'keys': keys, 'zones': zones}, sys.stdout)
print(f'{len(keys)} frames tracked', file=sys.stderr)
