#!/usr/bin/env python3
"""Replace a dark end card at the end of a finished video with a new card, ffmpeg + numpy only (no generated pixels).

The caller's old end cards are dark screens (a logo and a call to action over a dark ground) that run to the end of the
file; the caller's scenes are bright. So the end card = the run of dark frames that reaches the end of the file.

find_cut(src)          where the old card starts (kind: card | none | refuse)
matches(src, card, c)  the tail already is the new card (the frames match the new card, allowing a small time shift)
swap(src, card, out)   the new card in the old card's place, then the checks below

What a swap keeps, so that only the end picture changes:
  - the length of the file: the new card runs exactly as long as the old card did (a longer slot holds its last frame);
  - the whole audio track, copied bit for bit (a voice line can run over the card start; a cut would clip it);
  - resolution, frame rate and pixel format of the source; the new card is scaled to fit (letterboxed if needed).

Checks after writing (a failed check deletes the output):
  - duration equals the source within one frame and a half;
  - the last kept frame equals the source (mean abs diff <= MAX_KEEP_DIFF of 255); the cut is a frame number, so the
    old card's first frame is never kept;
  - three frames inside the new slot equal the new card (<= MAX_CARD_DIFF). The old and the new card share a ground and
    a logo, so this bound is tight: two different cards differ by 12–18 from 1 s on, a re-encoded copy by < 1.

  python3 engine/endcard.py find SRC.mp4
  python3 engine/endcard.py swap SRC.mp4 CARD.mp4 OUT.mp4 [SHEET.jpg]
"""
import json
import math
import os
import re
import subprocess
import sys

import numpy as np

SCAN_TAIL_S = 14          # seconds from the end that are scanned
SCAN_FPS = 10
SMALL = (54, 96)          # w, h of the scan frames
DARK_MEAN = 0.20          # card frames: mean grey 0.00–0.16; scenes >= 0.36 (measured on 21 files)
DARK_P90 = 0.30           # card frames: 90th percentile <= 0.25 (small text); scenes >= 0.70
MIN_CARD_S = 1.0          # a shorter dark tail is a fade to black, not a card
MAX_CARD_S = 9.5          # longer = a dark scene of the video itself: refuse
FADE_MAX_S = 1.0          # a cross-fade into the card is walked back at most this far
FADE_STEP = 0.008         # per-frame drop of mean grey that counts as fading (natural wobble <= 0.005)

CHECK_W, CHECK_H = 108, 192
MAX_KEEP_DIFF = 6.0       # a re-encode at crf 17 differs by ~1–3
MAX_CARD_DIFF = 5.0       # a re-encoded copy of the card < 1; another card 12–18
MATCH_SHIFT_S = 0.6       # matches(): the card may start up to this much earlier / later than the found cut
MAX_BYTES = 95 * 2 ** 20  # the caller's hosting takes one PUT up to ~100 MB; a bigger output is re-encoded at a higher crf


def run(cmd):
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def probe(path):
    j = json.loads(run(['ffprobe', '-v', 'error', '-show_entries',
                        'stream=codec_type,width,height,r_frame_rate,pix_fmt,start_time:format=duration', '-of', 'json',
                        path]))
    v = next(s for s in j['streams'] if s['codec_type'] == 'video')
    n, d = v['r_frame_rate'].split('/')
    return {'w': v['width'], 'h': v['height'], 'rate': v['r_frame_rate'], 'fps': float(n) / float(d),
            'pix_fmt': v.get('pix_fmt') or 'yuv420p', 'dur': float(j['format']['duration']),
            'start': float(v.get('start_time') or 0.0),
            'audio': any(s['codec_type'] == 'audio' for s in j['streams'])}


def frames(path, start, fps, dur=None, size=SMALL):
    w, h = size
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-ss', '%.3f' % max(0.0, start), '-i', path]
    if dur:
        cmd += ['-t', '%.3f' % dur]
    cmd += ['-vf', 'fps=%g,scale=%d:%d,format=gray' % (fps, w, h), '-f', 'rawvideo', '-']
    return np.frombuffer(run(cmd), np.uint8).reshape(-1, h, w).astype(np.float32) / 255.0


def frames_timed(path, start, count, size=SMALL):
    """`count` frames from `start` at the file's own rate (no resampling), each with its own time in the source
    (showinfo pts_time; -copyts keeps the source clock), so a cut is a real frame and not a time rounded in between."""
    w, h = size
    start = max(0.0, start)
    # trim drops the frames a seek decodes before `start` before showinfo sees them; the filters may run a few frames
    # past `count`, so the first len(frames) times are the frames returned
    r = subprocess.run(['ffmpeg', '-nostdin', '-v', 'info', '-hide_banner', '-copyts', '-ss', '%.3f' % max(0.0, start - 1),
                        '-i', path, '-vf', 'trim=start=%.6f,showinfo,scale=%d:%d,format=gray' % (start, w, h),
                        '-frames:v', str(count), '-f', 'rawvideo', '-'], capture_output=True, check=True)
    f = np.frombuffer(r.stdout, np.uint8).reshape(-1, h, w).astype(np.float32) / 255.0
    ts = [float(x) for x in re.findall(rb'pts_time:\s*(-?[0-9.]+)', r.stderr)]
    if len(ts) < len(f) or not len(f):
        raise RuntimeError('frame times: %d frames, %d times' % (len(f), len(ts)))
    return f, ts[:len(f)]


def grab(path, t):
    """One frame at t (gray, CHECK size, 0–255). Past the end → the last frame."""
    for tt in (t, t - 0.1, t - 0.25, t - 0.5):
        raw = run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', '%.3f' % max(0.0, tt), '-i', path, '-frames:v', '1',
                   '-vf', 'scale=%d:%d,format=gray' % (CHECK_W, CHECK_H), '-f', 'rawvideo', '-'])
        if raw:
            return np.frombuffer(raw, np.uint8).astype(np.float32)
    raise RuntimeError('no frame at %.2f s' % t)


def diff(a, b):
    return float(np.abs(a - b).mean())


def dark(f):
    flat = f.reshape(len(f), -1)
    return (flat.mean(axis=1) < DARK_MEAN) & (np.percentile(flat, 90, axis=1) < DARK_P90)


def find_cut(path):
    """→ {kind: 'card', cut_at, card_s, duration, fps} | {kind: 'none', why} (no end card) | {kind: 'refuse', why}."""
    p = probe(path)
    fps, dur = p['fps'], p['dur']
    t0 = max(0.0, dur - SCAN_TAIL_S)
    d = dark(frames(path, t0, SCAN_FPS))
    if not len(d) or not d[-1]:
        return {'kind': 'none', 'why': 'no dark end card in the last %d s' % SCAN_TAIL_S}
    s = len(d) - 1
    while s - 1 >= 0 and d[s - 1]:
        s -= 1
    if s == 0 and t0 > 0:
        return {'kind': 'refuse', 'why': 'the dark tail is longer than the scan (%d s)' % SCAN_TAIL_S}
    coarse = t0 + s / SCAN_FPS
    # refine at the file's own frame rate: the first dark frame of the tail, then back over the cross-fade into the
    # card (the card's logo already shows over the last frames of the scene), so no ghost of the old card stays
    lo = max(0.0, coarse - FADE_MAX_S - 0.3)
    fr, ts = frames_timed(path, lo, int(math.ceil((FADE_MAX_S + 0.6) * fps)))
    fine = dark(fr)
    k = next((i for i in range(len(fine)) if fine[i:].all()), None)
    if k is None:
        n = math.ceil((coarse - p['start']) * fps - 1e-6)
    else:
        # back over a cross-fade into the card (the card's logo already shows over the last frames of the scene), so no
        # ghost of the old card stays: step back while the frame before is itself darker than the one before it
        m = fr.reshape(len(fr), -1).mean(axis=1)
        j, floor = k, k - int(round(FADE_MAX_S * fps))
        while j - 2 >= max(0, floor) and m[j - 2] - m[j - 1] > FADE_STEP:
            j -= 1
        n = int(round((ts[j] - p['start']) * fps))   # the frame number of the first card frame
    cut = n / fps
    card = dur - cut
    info = {'kind': 'card', 'cut_at': round(cut, 3), 'cut_frame': n, 'card_s': round(card, 3), 'duration': round(dur, 3),
            'fps': round(fps, 3)}
    if card < MIN_CARD_S:
        return {'kind': 'none', 'why': 'dark tail %.2f s is shorter than %.1f s: a fade, not a card' % (card, MIN_CARD_S)}
    if cut < 1.0:
        return {'kind': 'refuse', 'why': 'the whole file is dark'}
    if card > MAX_CARD_S:
        return {'kind': 'refuse', 'why': 'dark tail %.2f s is longer than %.1f s: a dark scene?' % (card, MAX_CARD_S)}
    return info


def check_times(slot, card_dur):
    """Offsets inside the slot that are compared with the card (past its first second: the cards differ most there)."""
    return [min(1.6, slot * 0.4), min(3.0, slot * 0.7), min(card_dur - 0.2, slot - 0.2)]


def cut_frame(info, fps):
    """The source frame the card starts at (frames 0 … n-1 are kept)."""
    return int(info.get('cut_frame') or round(info['cut_at'] * fps))


def at(k, fps):
    """A seek time that returns frame k: `-ss t` gives the first frame whose time is >= t (seen 2026-11-19: -ss 68.967
    on a 30 fps file gives frame 2070 at 69.000, not frame 2069 at 68.9667), so seek half a frame before it."""
    return (k - 0.5) / fps


def matches(src, card, info):
    """→ (bool, diffs): the source's tail already shows `card`. Each check frame is compared with the card's frames
    within ±MATCH_SHIFT_S of the same offset (the card may start a little off the found cut); the best match counts."""
    c = probe(card)
    cut, slot = info['cut_at'], info['card_s']
    out = []
    for t in check_times(slot, c['dur']):
        a = grab(src, cut + t)
        best = min(diff(a, grab(card, min(c['dur'] - 0.05, max(0.0, t + s))))
                   for s in np.arange(-MATCH_SHIFT_S, MATCH_SHIFT_S + 1e-6, 0.2))
        out.append(round(best, 2))
    return max(out) <= MAX_CARD_DIFF, out


def encode(src, card, out, s, c, n, slot, crf):
    fit = ('scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black'
           % (s['w'], s['h'], s['w'], s['h']))
    hold = max(0.0, slot - c['dur']) + 0.5
    fc = ('[0:v]trim=end_frame=%d,setpts=PTS-STARTPTS,format=yuv420p,setsar=1[a];'
          '[1:v]%s,setsar=1,fps=%s,format=yuv420p,tpad=stop_mode=clone:stop_duration=%.3f,'
          'trim=end=%.4f,setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1:a=0[v]') % (n, fit, s['rate'], hold, slot)
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', src, '-i', card, '-filter_complex', fc, '-map', '[v]']
    if s['audio']:
        cmd += ['-map', '0:a:0', '-c:a', 'copy']
    cmd += ['-c:v', 'libx264', '-crf', str(crf), '-preset', 'medium', '-pix_fmt', 'yuv420p', '-r', s['rate'],
            '-t', '%.4f' % s['dur'], '-movflags', '+faststart', out]
    run(cmd)


def swap(src, card, out, info=None):
    """→ info + {checks, crf, bytes} or raises RuntimeError (output removed) when a check fails."""
    info = dict(info or find_cut(src))
    if info.get('kind') != 'card':
        raise RuntimeError('no end card to swap: ' + info.get('why', ''))
    s, c = probe(src), probe(card)
    fps = s['fps']
    n = cut_frame(info, fps)
    cut = n / fps
    slot = s['dur'] - cut
    for crf in (17, 20, 23):
        encode(src, card, out, s, c, n, slot, crf)
        if os.path.getsize(out) <= MAX_BYTES:
            break
    o = probe(out)
    checks = {
        'durationDiff': round(abs(o['dur'] - s['dur']), 3),
        'keepDiff': round(diff(grab(out, at(n - 1, fps)), grab(src, at(n - 1, fps))), 2),   # the last kept frame
        'cardDiff': [round(diff(grab(out, at(n + k, fps)), grab(card, at(k, fps))), 2)
                     for k in (int(round(t * fps)) for t in check_times(slot, c['dur']))],
        'audio': 'copied' if s['audio'] else 'none',
    }
    info.update({'cut_at': round(cut, 3), 'cut_frame': n, 'card_s': round(slot, 3),
                 'held_s': round(max(0.0, slot - c['dur']), 3), 'crf': crf, 'bytes': os.path.getsize(out), 'checks': checks})
    bad = []
    if checks['durationDiff'] > 1.5 / s['fps'] + 0.05:
        bad.append('duration changed')
    if checks['keepDiff'] > MAX_KEEP_DIFF:
        bad.append('the picture before the cut changed')
    if max(checks['cardDiff']) > MAX_CARD_DIFF:
        bad.append('the end slot does not show the new card')
    if os.path.getsize(out) > MAX_BYTES:
        bad.append('output over %d MiB' % (MAX_BYTES >> 20))
    if bad:
        os.remove(out)
        raise RuntimeError('; '.join(bad) + ' ' + json.dumps(checks))
    return info


def sheet(video, cut, out, extra_ts=()):
    """A contact sheet for a look by eye: 0.5 s before the cut, the last frame before it, the slot, the last frame."""
    p = probe(video)
    n = int(round(cut * p['fps']))
    ts = [cut - 0.5, at(n - 1, p['fps']), cut + 0.3, cut + min(1.6, (p['dur'] - cut) / 2), p['dur'] - 0.1] + list(extra_ts)
    tiles = []
    for i, t in enumerate(ts):
        f = '%s.%d.png' % (out, i)
        run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', '%.3f' % max(0.0, t), '-i', video, '-frames:v', '1',
             '-vf', 'scale=216:-2', f])
        tiles.append(f)
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y'] + sum([['-i', f] for f in tiles], []) +
        ['-filter_complex', 'hstack=inputs=%d' % len(tiles), '-q:v', '3', out])
    for f in tiles:
        os.remove(f)
    return out


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['find'] and len(a) == 2:
        print(json.dumps(find_cut(a[1])))
    elif a[:1] == ['swap'] and len(a) in (4, 5):
        i = find_cut(a[1])
        if i['kind'] != 'card':
            print(json.dumps(i)); sys.exit(2)
        ok, d = matches(a[1], a[2], i)
        if ok:
            print(json.dumps({**i, 'kind': 'current', 'matchDiff': d})); sys.exit(0)
        r = swap(a[1], a[2], a[3], i)
        if len(a) == 5:
            r['sheet'] = sheet(a[3], r['cut_at'], a[4])
        print(json.dumps({**r, 'matchDiff': d}))
    else:
        print(__doc__); sys.exit(64)
