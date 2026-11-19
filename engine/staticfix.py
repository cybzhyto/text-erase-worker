"""Static image fixes: an image-edit model's result pasted back into the original ONLY inside the regions that had to
change (a call-to-action button, price labels). Pure functions on numpy arrays; the model calls live in
worker/static_job.py.

Why a paste and not the model's whole image: an edit model redraws the whole picture it gets (faces, fabric, small
text drift a little), so its output is used only inside padded boxes around what had to change, aligned to the original
first, feathered at the edge. Every pixel outside those boxes is the original, byte for byte.

    crop, ratio = crop_for(boxes_px, W, H)          the window sent to the model, at a ratio the model can draw
    out, m = paste(orig, edit, crop, regions)       regions = [{box: px, pad: px}], m = alignment metrics
    fixes = plan(read, want, W, H)                  what a vision read says must change (empty = publishable)
    ok, why = qa(read_after, want, fixes)           the vision read of the result says it is fixed
"""
import re

import cv2
import numpy as np

RATIOS = {'1:1': 1, '3:4': .75, '4:3': 4 / 3, '2:3': 2 / 3, '3:2': 1.5, '9:16': 9 / 16, '16:9': 16 / 9, '4:5': .8,
          '5:4': 1.25, '21:9': 21 / 9}
SHAPES_OK = ('none',)          # a call to action may only be words straight on the picture
PAD_CTA = 20                   # px round a call-to-action box (vision boxes sit within ~10 px)
PAD_TAG = 80                   # px round a price label: its pointer dot / line sits 60-70 px outside the label box
MIN_ECC = 0.80                 # alignment of the model's output to the original (correlation, outside the regions)
MAX_DRIFT = 20.0               # mean abs difference (0-255) outside the regions after alignment
MIN_CONTRAST = 3.0             # new call-to-action words vs the ground right behind them (WCAG ratio; large text AA = 3).
                               # Measured 2026-11-19: approved fixes 3.8-16, dark-blue words left on a navy page 1.4


def to_px(box, W, H):
    """A vision box in thousandths of the image (x0, y0, x1, y1) -> pixels, clamped, x0 < x1, y0 < y1."""
    x0, y0, x1, y1 = [float(v) for v in box]
    x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
    return [int(max(0, min(W, x0 * W / 1000))), int(max(0, min(H, y0 * H / 1000))),
            int(max(0, min(W, round(x1 * W / 1000)))), int(max(0, min(H, round(y1 * H / 1000))))]


def crop_for(boxes, W, H, margin=140):
    """The window round all boxes (+ margin), widened to the nearest ratio of RATIOS, kept inside the image."""
    x0 = max(0, min(b[0] for b in boxes) - margin); y0 = max(0, min(b[1] for b in boxes) - margin)
    x1 = min(W, max(b[2] for b in boxes) + margin); y1 = min(H, max(b[3] for b in boxes) + margin)
    w, h = x1 - x0, y1 - y0
    ar = min(RATIOS, key=lambda k: abs(RATIOS[k] - w / h))
    if w / h > RATIOS[ar]:
        h = w / RATIOS[ar]
    else:
        w = h * RATIOS[ar]
    w, h = min(w, W), min(h, H)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    c0 = int(max(0, min(W - w, cx - w / 2))); c1 = int(max(0, min(H - h, cy - h / 2)))
    return [c0, c1, int(c0 + w), int(c1 + h)], ar


def _windows(regions, crop, shape):
    """core = the boxes, win = the padded boxes, both in crop coordinates."""
    x0, y0 = crop[0], crop[1]
    ch, cw = shape
    win = np.zeros((ch, cw), np.uint8); core = np.zeros((ch, cw), np.uint8)
    for r in regions:
        bx0, by0, bx1, by1 = [int(v) for v in r['box']]
        p = int(r.get('pad', PAD_CTA))
        core[max(0, by0 - y0):max(0, by1 - y0), max(0, bx0 - x0):max(0, bx1 - x0)] = 1
        win[max(0, by0 - y0 - p):max(0, by1 - y0 + p), max(0, bx0 - x0 - p):max(0, bx1 - x0 + p)] = 1
    return core, win


def paste(orig, edit, crop, regions, feather=2.5):
    """orig: H×W×3 uint8 (RGB), edit: the model's image of orig[crop] (any size, same ratio). Returns (out, metrics).

    The edit is resized to the crop, aligned to it by ECC (affine, on everything outside the padded regions), and the whole
    padded box of each region is taken from it (a white label on a white wall and a thin pointer line stay under any
    difference threshold, so a diff mask misses them), feathered by `feather` px. Pixels outside the padded boxes are
    the original's, exactly."""
    x0, y0, x1, y1 = crop
    a = orig[y0:y1, x0:x1]
    ch, cw = a.shape[:2]
    e = cv2.resize(edit, (cw, ch), interpolation=cv2.INTER_AREA if edit.shape[1] > cw else cv2.INTER_LANCZOS4)
    core, win = _windows(regions, crop, (ch, cw))
    g1 = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(e, cv2.COLOR_RGB2GRAY).astype(np.float32)
    wm = np.eye(2, 3, dtype=np.float32)
    cc = None
    try:
        cc, wm = cv2.findTransformECC(g1, g2, wm, cv2.MOTION_AFFINE,
                                      (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 300, 1e-6),
                                      ((1 - win) * 255).astype(np.uint8), 5)
        e = cv2.warpAffine(e, wm, (cw, ch), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)
    except cv2.error:
        cc = None
    d = np.abs(cv2.GaussianBlur(e, (5, 5), 0).astype(np.int16) - cv2.GaussianBlur(a, (5, 5), 0).astype(np.int16)).max(-1)
    ring = win == 0
    drift = float(d[ring].mean()) if ring.any() else 0.0
    # feather INSIDE the padded box: shrink it by 3 sigma, blur, keep within the box, so outside it stays the original
    k = int(round(3 * feather))
    inner = cv2.erode(win, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * k + 1, 2 * k + 1))) if k else win
    soft = (cv2.GaussianBlur(inner.astype(np.float32), (0, 0), feather) if feather else inner.astype(np.float32)) * win
    soft = soft[..., None]
    out = orig.copy()
    out[y0:y1, x0:x1] = (e * soft + a * (1 - soft)).round().clip(0, 255).astype(np.uint8)
    changed = int((np.abs(out.astype(np.int16) - orig.astype(np.int16)).max(-1) > 0).sum())
    return out, {'ecc': None if cc is None else round(float(cc), 4), 'shift': np.round(wm[:, 2], 2).tolist(),
                 'drift': round(drift, 2), 'windowPx': int(win.sum()), 'changedPx': changed,
                 'aligned': cc is not None and cc >= MIN_ECC and drift <= MAX_DRIFT}


def norm(text):
    """Words only, upper case: «See more →» and «SEE MORE» are the same call to action."""
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', str(text or ''), flags=re.UNICODE)).strip().upper()


def target_cta(want, read):
    """The call-to-action words the image must show: want.cta, or want.ctaByLang[read.language] when given."""
    by = (want or {}).get('ctaByLang') or {}
    lang = str((read or {}).get('language') or '').lower()
    return by.get(lang) or (want or {}).get('cta') or by.get('en') or ''


def plan(read, want, W, H):
    """What must change for the image to be publishable under `want` ({cta | ctaByLang, prices: remove|keep}).
    Returns [] or a list of fixes: {kind: cta, box, pad, old, new, shape, colour, why} / {kind: tags, boxes, pad, n, why}."""
    read = read or {}
    fixes = []
    c = read.get('cta')
    new = target_cta(want, read)
    if c and c.get('box') and new:
        why = []
        if norm(c.get('text')) != norm(new): why.append('words')
        if str(c.get('shape') or 'none') not in SHAPES_OK: why.append('shape ' + str(c.get('shape')))
        if c.get('arrow'): why.append('arrow')
        if c.get('underline'): why.append('underline')
        if why:
            fixes.append({'kind': 'cta', 'box': to_px(c['box'], W, H), 'pad': PAD_CTA, 'old': str(c.get('text') or ''),
                          'new': new, 'shape': str(c.get('shape') or ''), 'colour': str(c.get('colour') or ''),
                          'fill': str(c.get('fill') or ''), 'why': why})
    tags = [t for t in (read.get('price_tags') or []) if t.get('box')]
    if (want or {}).get('prices') == 'remove' and tags:
        fixes.append({'kind': 'tags', 'boxes': [to_px(t['box'], W, H) for t in tags], 'pad': PAD_TAG, 'n': len(tags),
                      'why': [f'{len(tags)} price label(s)']})
    return fixes


def _lum(rgb):
    c = np.asarray(rgb, np.float64) / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]


def contrast(img, box):
    """Contrast ratio (WCAG, 1-21) of the two tones inside a box: Otsu splits letters from ground, the ratio is taken
    between the two classes' mean colours. Words that sink into the ground score under MIN_CONTRAST."""
    x0, y0, x1, y1 = box
    a = img[y0:y1, x0:x1]
    if a.size == 0: return 1.0
    g = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)
    t, _ = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = g > t
    if m.sum() < 20 or (~m).sum() < 20: return 1.0
    l1, l2 = float(_lum(a[m].mean(0))), float(_lum(a[~m].mean(0)))
    return round((max(l1, l2) + 0.05) / (min(l1, l2) + 0.05), 2)


def qa(read, want, fixes, img=None):
    """The read of the fixed image (and the image itself, for the measured checks): every fix took and nothing it
    touched came back. Returns (ok, why)."""
    read = read or {}
    bad = []
    for f in fixes:
        if f['kind'] == 'cta':
            c = read.get('cta')
            if not c: bad.append('the call to action is gone')
            else:
                if norm(c.get('text')) != norm(f['new']): bad.append('words: ' + norm(c.get('text'))[:40])
                if str(c.get('shape') or 'none') not in SHAPES_OK: bad.append('shape ' + str(c.get('shape')))
                if c.get('arrow'): bad.append('arrow')
                if c.get('underline'): bad.append('underline')
                if img is not None and c.get('box'):
                    k = contrast(img, to_px(c['box'], img.shape[1], img.shape[0]))
                    if k < MIN_CONTRAST: bad.append(f'the words hardly stand out (contrast {k} < {MIN_CONTRAST})')
        if f['kind'] == 'tags' and (read.get('price_tags') or []):
            bad.append(f'{len(read["price_tags"])} price label(s) left')
    return not bad, bad
