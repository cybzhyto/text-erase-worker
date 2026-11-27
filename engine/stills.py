#!/usr/bin/env python3
"""Render Creatomate-style STILL bodies with ffmpeg: the frames the caller's localization recipe reads (source stills for its
text read, contact sheets for its colour and pixel checks, QA frames of the finished video), made here so they never wait in
Creatomate's one-render-at-a-time queue.

The body is the JSON the caller would POST to Creatomate's /v2/renders, so Creatomate stays the way back for the same body:
    {"output_format": "jpg" | "png", "width": W, "height": H, "duration": D, "snapshot_time": T,
     "elements": [{"type": "video", "source": URL, "time": 0, "duration": D, "fit": "cover", "volume": "0%",
                   "trim_start": S, "width": w, "height": h, "x": cx, "y": cy}, ...]}
What each key does here (Creatomate's meaning, checked against Creatomate's own renders of one real job, 8 stills + 7 contact
sheets of the recipe's Lab 1045 run, 2026-11-27, see README):
    the image is the composition at snapshot_time (default 0). An element is drawn when time <= T < time + duration and shows
    its source at trim_start + (T - time): the FIRST frame whose timestamp is at or after that moment (20.17 s at 30 fps is
    frame 606, not 605; 33.07 s is 993), which is what ffmpeg's accurate seek gives. fit "cover": the frame is scaled to cover
    its box and the middle is kept, with the area filter and BT.601 colours (Creatomate's 270 x 480 tiles: mean difference
    0.7-1.7 levels; bilinear 1.1-2.8; BT.709 is worse on full frames). The box is width x height (default: the whole image)
    centred at x, y (default: the middle). Later elements are drawn on top; where nothing is drawn the image is black.
    png = RGBA with every pixel opaque, as Creatomate's; jpg = 4:2:0 at ffmpeg quality 3 (Creatomate's: PSNR 41.4 dB against
    the exact frame at 178 KB; ours 42.5 dB at 172 KB). volume, name and the body's duration change nothing in a still.
Anything outside that (an unknown key, an image or text element, another fit, a size or position given in %, an element with
no time, a moment past the end of its source) is refused, so the caller sends that body to Creatomate instead.

Sources are local files (the job downloads them). Only MP4 / MOV containers are opened, with the demuxer named explicitly: a file
that is really a playlist or anything else is refused before ffmpeg reads it. ffmpeg runs without the job's environment."""
import concurrent.futures, json, math, os, re, subprocess

import numpy as np

TOP_KEYS = {'output_format', 'width', 'height', 'duration', 'snapshot_time', 'frame_rate', 'elements'}
EL_KEYS = {'name', 'type', 'track', 'source', 'time', 'duration', 'fit', 'volume', 'trim_start', 'width', 'height', 'x', 'y'}
FORMATS = ('jpg', 'png')
MAX_SIDE, MAX_PIXELS, MAX_ELEMENTS, MAX_SECONDS = 8192, 40_000_000, 64, 3600.0
JPG_Q = 3
SCALE = 'flags=area:in_color_matrix=bt601:out_color_matrix=bt601'
ENV = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8'}   # ffmpeg / ffprobe get nothing else


class Refused(ValueError):
    """The body asks for something this renderer does not do the way Creatomate does."""


def _num(x, what, lo=0.0, hi=MAX_SECONDS, lo_open=False):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x > hi or x < lo or (lo_open and x <= lo):
        raise Refused(f'{what}: {x!r}')
    return float(x)


def _side(x, what):
    v = _num(x, what, lo=1, hi=MAX_SIDE)
    if abs(v - round(v)) > 1e-6:
        raise Refused(f'{what}: {x!r} is not a whole number of pixels')
    return int(round(v))


def is_still(body):
    """A body this module is for (a still format), whether or not it passes plan()."""
    return isinstance(body, dict) and body.get('output_format') in FORMATS


def plan(body):
    """Check the body and return the drawing plan (no files touched). Raises Refused."""
    if not isinstance(body, dict):
        raise Refused('the body is not an object')
    extra = sorted(set(body) - TOP_KEYS)
    if extra:
        raise Refused('unknown body keys: ' + ', '.join(extra))
    fmt = body.get('output_format')
    if fmt not in FORMATS:
        raise Refused(f'output_format {fmt!r} (only {", ".join(FORMATS)})')
    W, H = _side(body.get('width'), 'width'), _side(body.get('height'), 'height')
    if W * H > MAX_PIXELS:
        raise Refused(f'{W} x {H} is over {MAX_PIXELS} pixels')
    T = _num(body.get('snapshot_time', 0), 'snapshot_time')
    els = body.get('elements')
    if not isinstance(els, list) or not 1 <= len(els) <= MAX_ELEMENTS:
        raise Refused(f'elements: 1-{MAX_ELEMENTS} expected')
    out = []
    for i, e in enumerate(els):
        w = f'element {i}'
        if not isinstance(e, dict):
            raise Refused(w + ' is not an object')
        extra = sorted(set(e) - EL_KEYS)
        if extra:
            raise Refused(f'{w}: unknown keys ' + ', '.join(extra))
        if e.get('type') != 'video':
            raise Refused(f'{w}: type {e.get("type")!r} (only video)')
        if e.get('fit') != 'cover':
            raise Refused(f'{w}: fit {e.get("fit")!r} (only cover)')
        src = e.get('source')
        if not isinstance(src, str) or not re.fullmatch(r'https://[^\s"\'<>]{4,2000}', src):
            raise Refused(f'{w}: source is not an https URL')
        if 'time' not in e:
            raise Refused(f'{w}: no time (Creatomate would place it after the last element of its track)')
        vol = e.get('volume', '0%')
        if not isinstance(vol, str) or not re.fullmatch(r'\d+(?:\.\d+)? ?%', vol.strip()):
            raise Refused(f'{w}: volume {vol!r} (expected "N%")')
        if 'track' in e and (isinstance(e['track'], bool) or not isinstance(e['track'], int) or not 1 <= e['track'] <= 999):
            raise Refused(f'{w}: track {e["track"]!r}')
        t0 = _num(e['time'], w + ' time')
        dur = None if e.get('duration') is None else _num(e['duration'], w + ' duration', lo_open=True)
        trim = _num(e.get('trim_start', 0), w + ' trim_start')
        bw = W if e.get('width') is None else _side(e['width'], w + ' width')
        bh = H if e.get('height') is None else _side(e['height'], w + ' height')
        cx = W / 2 if e.get('x') is None else _num(e['x'], w + ' x', lo=-MAX_SIDE, hi=2 * MAX_SIDE)
        cy = H / 2 if e.get('y') is None else _num(e['y'], w + ' y', lo=-MAX_SIDE, hi=2 * MAX_SIDE)
        x0, y0 = int(round(cx - bw / 2)), int(round(cy - bh / 2))
        if x0 >= W or y0 >= H or x0 + bw <= 0 or y0 + bh <= 0:
            raise Refused(f'{w}: its box lies outside the image')
        if T < t0 or (dur is not None and T >= t0 + dur):
            continue                          # not on screen at the snapshot moment: Creatomate draws nothing for it
        out.append({'i': i, 'source': src, 'tau': round(trim + T - t0, 6), 'box': (x0, y0, bw, bh), 'track': e.get('track', 0)})
    # later elements on top; a higher track on top of a lower one (Creatomate's layering), list order within a track
    out.sort(key=lambda d: (d['track'], d['i']))
    return {'fmt': fmt, 'w': W, 'h': H, 't': T, 'elements': out, 'given': len(els)}


def sniff(path):
    """ffmpeg demuxer for a downloaded video source, from its first bytes; None = not an MP4 / MOV file."""
    with open(path, 'rb') as f:
        h = f.read(12)
    return 'mov' if h[4:8] in (b'ftyp', b'moov', b'mdat', b'wide', b'free') else None


def probe(path, fmt):
    r = subprocess.run(['ffprobe', '-v', 'error', '-f', fmt, '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,r_frame_rate:format=duration', '-of', 'json', path],
                       capture_output=True, text=True, timeout=60, env=ENV)
    if r.returncode:
        raise RuntimeError('ffprobe could not read a source: ' + r.stderr.strip()[-300:])
    j = json.loads(r.stdout or '{}')
    st = (j.get('streams') or [{}])[0]
    if not st.get('width'):
        raise Refused('a source has no video stream')
    return {'width': int(st['width']), 'height': int(st['height']), 'dur': float((j.get('format') or {}).get('duration') or 0)}


def grab(path, fmt, tau, bw, bh):
    """The first frame at or after tau, scaled to cover bw x bh and cut to it: an (bh, bw, 3) uint8 array, or None past the end."""
    vf = f'scale={bw}:{bh}:force_original_aspect_ratio=increase:{SCALE},crop={bw}:{bh},format=rgb24'
    r = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-ss', f'{tau:.6f}', '-f', fmt, '-i', path,
                        '-frames:v', '1', '-vf', vf, '-f', 'rawvideo', '-'], capture_output=True, timeout=120, env=ENV)
    if r.returncode:
        raise RuntimeError('ffmpeg could not read a frame: ' + r.stderr.decode(errors='replace').strip()[-300:])
    if len(r.stdout) != bw * bh * 3:
        return None
    return np.frombuffer(r.stdout, np.uint8).reshape(bh, bw, 3)


def encode(img, fmt, out_path):
    h, w = img.shape[:2]
    if fmt == 'png':
        px = np.concatenate([img, np.full((h, w, 1), 255, np.uint8)], axis=2)   # RGBA, opaque everywhere (as Creatomate's)
        # pred mixed: 5.1 MB for a 1080 x 3360 sheet (Creatomate's 5.1 MB) instead of 6.7 MB, +0.6 s
        args = ['-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{w}x{h}', '-i', '-', '-frames:v', '1', '-pix_fmt', 'rgba', '-f', 'image2',
                '-c:v', 'png', '-pred', 'mixed', out_path]
    else:
        px = img
        args = ['-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{w}x{h}', '-i', '-', '-frames:v', '1', '-q:v', str(JPG_Q),
                '-pix_fmt', 'yuvj420p', '-f', 'image2', '-c:v', 'mjpeg', out_path]
    r = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', *args], input=px.tobytes(),
                       capture_output=True, timeout=120, env=ENV)
    if r.returncode or not os.path.isfile(out_path):
        raise RuntimeError('ffmpeg could not write the still: ' + r.stderr.decode(errors='replace').strip()[-300:])


def sources(bodies):
    """Every source URL the bodies draw, in first-use order (to download once for a batch)."""
    return list(dict.fromkeys(e['source'] for b in bodies for e in (b.get('elements') or []) if isinstance(e, dict) and 'source' in e))


def render(body, files, out_path, info=None, workers=4):
    """body + {source: local path} -> the still at out_path. info caches each source's probe across a batch.
    Returns the numbers the job reports."""
    p = plan(body)
    info = {} if info is None else info
    for e in p['elements']:
        s = e['source']
        if s in info:
            continue
        path = files.get(s)
        if not path or not os.path.isfile(path):
            raise RuntimeError('a source was not downloaded')
        fmt = sniff(path)
        if not fmt:
            raise Refused('a source is not an MP4 / MOV video')
        info[s] = {'path': path, 'fmt': fmt, **probe(path, fmt)}
    for e in p['elements']:
        if e['tau'] >= info[e['source']]['dur'] + 1e-3:
            raise Refused('a still moment is past the end of its source')
    canvas = np.zeros((p['h'], p['w'], 3), np.uint8)
    keys = list(dict.fromkeys((e['source'], e['tau'], e['box'][2], e['box'][3]) for e in p['elements']))
    with concurrent.futures.ThreadPoolExecutor(workers) as ex:
        got = dict(zip(keys, ex.map(lambda k: grab(info[k[0]]['path'], info[k[0]]['fmt'], k[1], k[2], k[3]), keys)))
    for e in p['elements']:
        fr = got[(e['source'], e['tau'], e['box'][2], e['box'][3])]
        if fr is None:
            raise Refused('a still moment is past the end of its source')
        x0, y0, bw, bh = e['box']
        cx0, cy0 = max(0, x0), max(0, y0)
        cx1, cy1 = min(p['w'], x0 + bw), min(p['h'], y0 + bh)
        canvas[cy0:cy1, cx0:cx1] = fr[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
    encode(canvas, p['fmt'], out_path)
    return {'format': p['fmt'], 'width': p['w'], 'height': p['h'], 'elements': len(p['elements']), 'frames': len(keys),
            'bytes': os.path.getsize(out_path)}
