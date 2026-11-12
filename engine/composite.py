"""Difference-matting composite: the overlay-only body rendered over solid black (B) and solid white (W) gives
alpha = 1 - (W - B) / 255 per channel, and the colour already multiplied by alpha = B. Final = plate * (1 - alpha) + B.
usage: python3 composite.py PLATE.mp4 BLACK.mp4 WHITE.mp4 OUT.mp4 [CAPTIONS.json]   (audio comes from BLACK.mp4)
CAPTIONS.json (optional, from build_math.py caption_pages with "local": true):
  {"pages": [{"t0", "t1", "text", "font", "size", "color", "box": [x0, y0, x1, y1] | null, "radius", "fill",
              "keys": [[t, cx, cy], ...] (a text that rides a moving element) }],
   "clear": [[t0, t1, x0, y0, x1, y1], ...]}   (px; zero an old overlay render there, e.g. its baked-in caption boxes)
The pages are drawn here with the real font (PIL + raqm, the same Montserrat file), so box and words always come
from the same page and their size is exact; no Creatomate render is needed for them."""
import json, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont
P, Bk, Wt, OUT = sys.argv[1:5]
CAP = json.load(open(sys.argv[5])) if len(sys.argv) > 5 else {'pages': [], 'clear': []}
def probe(f):
    """Frame size and rate of the plate: every input must share them (the overlays are rendered at the source's size)."""
    o = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,r_frame_rate',
                        '-of', 'csv=p=0', f], capture_output=True, text=True, check=True).stdout.strip().split(',')
    n, d = o[2].split('/'); return int(o[0]), int(o[1]), float(n) / float(d)
W, H, FPS = probe(P)
def sprite(pg):
    """RGB + alpha arrays of one page (box with antialiased corners + text), and its top-left corner."""
    font = ImageFont.truetype(pg['font'], pg['size'])
    if pg.get('box'):
        x0, y0, x1, y1 = pg['box']; X0, Y0 = int(np.floor(x0)), int(np.floor(y0)); w, h = int(np.ceil(x1)) - X0, int(np.ceil(y1)) - Y0
        S = 4; m = Image.new('L', (w * S, h * S), 0)      # 4x supersampled box for smooth rounded corners
        ImageDraw.Draw(m).rounded_rectangle([(x0 - X0) * S, (y0 - Y0) * S, (x1 - X0) * S - 1, (y1 - Y0) * S - 1], radius=pg.get('radius', 0) * S, fill=255)
        ba = np.asarray(m.resize((w, h), Image.LANCZOS), np.float32) / 255
        cx, cy = (x0 + x1) / 2 - X0, (y0 + y1) / 2 - Y0
    else:
        L, T, R, B = font.getbbox(pg['text'], anchor='mm'); w, h = int(R - L) + 8, int(B - T) + 8
        X0 = Y0 = 0; ba = np.zeros((h, w), np.float32); cx, cy = w / 2, h / 2
    t = Image.new('L', (w, h), 0)      # a page may hold two lines («\n»): centred, line gap 0.15 em
    ImageDraw.Draw(t).text((cx, cy), pg['text'], font=font, anchor='mm', fill=255, align='center', spacing=round(0.15 * pg['size']))
    ta = np.asarray(t, np.float32)[..., None] / 255
    rgb = np.array(pg.get('fill', [0, 0, 0]), np.float32) * (1 - ta) + np.array(pg.get('color', [255, 255, 255]), np.float32) * ta
    a = np.maximum(ba, ta[..., 0])
    return rgb, a, X0, Y0, cx, cy
SP = [sprite(pg) for pg in CAP['pages']]
def at(keys, t):
    k = np.array(keys, np.float64); return np.interp(t, k[:, 0], k[:, 1]), np.interp(t, k[:, 0], k[:, 2])
def rd(f):
    return subprocess.Popen(['ffmpeg', '-v', 'error', '-i', f, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)
ps = [rd(f) for f in (P, Bk, Wt)]
import os
SHOW = sorted(round(float(x) * FPS) for x in os.environ.get('SHOW', '').split(',') if x)   # check stills: SHOW=1.5,20 -> OUT is a folder of PNGs
if SHOW: os.makedirs(OUT, exist_ok=True)
enc = None if SHOW else subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                        '-i', Bk, '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'medium', '-crf', '17',
                        '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart', '-shortest', OUT], stdin=subprocess.PIPE)
n = W * H * 3; i = 0
while True:
    bs = [p.stdout.read(n) for p in ps]
    if any(len(b) < n for b in bs): break
    if SHOW and i not in SHOW:
        i += 1
        if i > SHOW[-1]: break
        continue
    p, b, w = (np.frombuffer(x, np.uint8).reshape(H, W, 3).astype(np.float32) for x in bs)
    a = np.clip(1 - (w - b) / 255, 0, 1)
    empty = ((w - b).min(2) > 247) & (b.max(2) < 6)          # nothing drawn here: keep the plate pixel exactly (codec noise)
    a[empty] = 0; b[empty] = 0
    t = i / FPS
    for t0, t1, x0, y0, x1, y1 in CAP.get('clear', []):
        if t0 <= t < t1: a[y0:y1, x0:x1] = 0; b[y0:y1, x0:x1] = 0
    o = p * (1 - a) + b
    for pg, (rgb, sa, X0, Y0, cx, cy) in zip(CAP['pages'], SP):
        if not pg['t0'] <= t < pg['t1']: continue
        if pg.get('keys'):
            kx, ky = at(pg['keys'], t); X0, Y0 = int(round(kx - cx)), int(round(ky - cy))
        h_, w_ = sa.shape; ys, xs = slice(max(Y0, 0), min(Y0 + h_, H)), slice(max(X0, 0), min(X0 + w_, W))
        sy, sx = slice(ys.start - Y0, ys.stop - Y0), slice(xs.start - X0, xs.stop - X0)
        al = sa[sy, sx][..., None]; o[ys, xs] = o[ys, xs] * (1 - al) + rgb[sy, sx] * al
    o = np.clip(o, 0, 255).astype(np.uint8)
    if SHOW: Image.fromarray(o).save(f'{OUT}/f{i / FPS:.2f}.png')
    else: enc.stdin.write(o.tobytes())
    i += 1
if enc: enc.stdin.close(); enc.wait()
print('composited', i, 'frames ->', OUT)
