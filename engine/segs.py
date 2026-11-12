"""meas JSON -> jewel caption rects + disclaimer tint segments (cfg keys caption_rects, disc_segments)
usage: python3 segs.py MEAS.json CFG_IN.json CFG_OUT.json [T_FROM]"""
import json, sys
F = json.load(open(sys.argv[1])); cfg = json.load(open(sys.argv[2])); TF = float(sys.argv[4]) if len(sys.argv) > 4 else 9.75
DT = 0.1
def rect(f):
    # opaque black caption box (one line, ~117 px): filled solid black later, so keep it exact (+2 / +1 px for antialias);
    # a taller «box» is a translucent plate: blur it like plain text
    if f['box'] and f['box'][3] - f['box'][1] < 140: x0, y0, x1, y1 = f['box']; return ('B', [x0 - 2, y0 - 1, x1 + 2, y1 + 1])
    if f['box']: x0, y0, x1, y1 = f['box']; return ('T', [x0 - 16, y0 - 12, x1 + 16, y1 + 12])
    if f['text']: x0, y0, x1, y1 = f['text']; return ('T', [x0 - 16, y0 - 12, x1 + 16, y1 + 12])
    return None
caps = []; cur = None
for f in F:
    if f['t'] < TF: continue
    r = rect(f)
    if r is None:
        if cur: caps.append(cur); cur = None
        continue
    k, b = r
    if cur and cur['k'] == k and all(abs(a - c) <= 14 for a, c in zip(b[1::2], cur['b'][1::2])) and \
            ((k == 'T' and abs(b[0] - cur['b0']) <= 14) or (k == 'B' and all(abs(a - c) <= 14 for a, c in zip(b, cur['b'])))):
        cur['b'] = [min(cur['b'][0], b[0]), min(cur['b'][1], b[1]), max(cur['b'][2], b[2]), max(cur['b'][3], b[3])]; cur['t1'] = f['t']
    else:
        if cur: caps.append(cur)
        cur = {'k': k, 'b': b, 'b0': b[0], 't0': f['t'], 't1': f['t']}
if cur: caps.append(cur)
# pad each segment by half a frame step on both ends (10 fps sampling)
out = []
for i, c in enumerate(caps):
    t0 = max(TF, c['t0'] - DT / 2 - 0.05); t1 = c['t1'] + DT / 2 + 0.05
    x0, y0, x1, y1 = c['b']
    if c['k'] == 'B' and i + 1 < len(caps) and caps[i + 1]['k'] == 'B' and caps[i + 1]['t0'] - c['t1'] <= 0.2:   # 0.3+ s = a real moment with no box
        # boxes change with a pop animation (0.1-0.5 s with no clean box): bridge to the next box with the union rect,
        # so the black box never blinks and no animating old box shows between two new pages
        n = caps[i + 1]; g0 = t1; g1 = max(g0, n['t0'] - DT / 2 - 0.05)
        if g1 > g0:
            u = [min(x0, n['b'][0]), min(y0, n['b'][1]), max(x1, n['b'][2]), max(y1, n['b'][3])]
            out.append([round(g0, 2), round(g1 - g0, 2), round(u[0] / 10.8, 2), round(u[1] / 19.2, 2), round(u[2] / 10.8, 2), round(u[3] / 19.2, 2), 'B'])
    out.append([round(t0, 2), round(t1 - t0, 2), round(x0 / 10.8, 2), round(y0 / 19.2, 2), round(x1 / 10.8, 2), round(y1 / 19.2, 2), c['k']])
cfg['caption_rects'] = out
# disclaimer: segments of similar background; tint strength by background spread (flat = strong, textured = light)
segs = []; cur = None
for f in F:
    bg = f['bg']; sd = f['sd']
    if cur and max(abs(a - b) for a, b in zip(bg, cur['bg'])) <= 22 and abs(sd - cur['sd']) <= 20:
        cur['t1'] = f['t']; cur['n'] += 1
    else:
        if cur: segs.append(cur)
        cur = {'t0': f['t'], 't1': f['t'], 'bg': bg, 'sd': sd, 'n': 1}
segs.append(cur)
ds = []
for i, s in enumerate(segs):
    t0 = s['t0']; t1 = segs[i + 1]['t0'] if i + 1 < len(segs) else F[-1]['t'] + DT
    # flat background: strong blur + its own colour (no grey haze); textured (rug, crowd): light blur, no tint
    a, bl = (0.65, 2.5) if s['sd'] < 12 else (0.4, 2.0) if s['sd'] < 30 else (0.0, 1.2)
    ds.append([round(t0, 2), round(t1 - t0, 2)] + s['bg'] + [a, bl])
# merge cut flashes (< 0.5 s) into the previous segment: one layer per scene keeps the body small
m = []
for d in ds:
    if m and d[1] < 0.5: m[-1][1] = round(m[-1][1] + d[1], 2)
    else: m.append(d)
ds = m
cfg['disc_segments'] = ds
json.dump(cfg, open(sys.argv[3], 'w'))
print(len(out), 'caption rects;', len(ds), 'disclaimer segments')
for d in ds: print('disc', d)
for o in out[:12]: print('cap', o)
