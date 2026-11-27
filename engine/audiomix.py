#!/usr/bin/env python3
"""Mix a Creatomate-style audio body with ffmpeg: the audio-only renders of the caller's localization recipe (the fitted dub's
mix; later the voice-clone sample), made here so they never wait in Creatomate's one-render-at-a-time queue.

The body is the JSON the caller would POST to Creatomate's /v2/renders, so Creatomate stays the way back for the same body:
    {"output_format": "mp4", "width": 64, "height": 64, "frame_rate": 1, "duration": D,
     "elements": [{"name", "type": "audio", "track", "time", "duration", "source", "trim_start", "trim_duration",
                   "volume": "55%", "audio_fade_in", "audio_fade_out"}, ...]}
What each key does here (Creatomate's meaning, checked against Creatomate's own render of one real body, see README):
    an element plays its source from trim_start (default 0) for trim_duration (default: to the end of the file), starting at
    `time` on the timeline, for `duration` seconds (default: that trimmed length); a source that ends early leaves silence;
    volume "N%" = linear gain N/100; audio_fade_in / audio_fade_out = linear ramps at the element's own start / end; all
    elements are summed (no normalization, `track` only orders layers); the mix lasts `duration` (default: the last element's
    end). A mono source plays in both channels at -3 dB each (constant power), a stereo one as it is. Width, height and frame rate mean nothing for audio and are ignored.
Anything outside that (an unknown key, a non-audio element, an element with no time, a volume that is not "N%") is refused,
so the caller sends that body to Creatomate instead of getting a mix that sounds different.

Output: WAV, 48 kHz, stereo, 16 bit. The sum is computed in float; only when it would clip is it scaled down to PEAK (the caller
normalizes the loudness of the whole mix afterwards, so one overall gain changes nothing but the clipping).

Sources are local files (the job downloads them). Each one's container is read from its first bytes and only plain audio
containers are opened, with the demuxer named explicitly: a file that is really a playlist or anything else is refused before
ffmpeg reads it. ffmpeg runs without the job's environment (no tokens)."""
import json, math, os, re, subprocess, wave

import numpy as np

SR = 48000
TOP_KEYS = {'output_format', 'width', 'height', 'frame_rate', 'duration', 'elements'}
EL_KEYS = {'name', 'type', 'track', 'time', 'duration', 'source', 'trim_start', 'trim_duration', 'volume', 'audio_fade_in',
           'audio_fade_out'}
MAX_ELEMENTS, MAX_SECONDS, MAX_GAIN = 300, 900.0, 4.0
PEAK = 0.98
MONO = 0.7071068   # a mono source goes to each channel at -3 dB, as Creatomate does (its 1042 mix: lines 0.706 of unity, stereo bed 1.000)
ENV = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8'}   # ffmpeg / ffprobe get nothing else


class Refused(ValueError):
    """The body asks for something this mixer does not do the way Creatomate does."""


def _num(x, what, lo=0.0, hi=MAX_SECONDS, lo_open=False):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x > hi or x < lo or (lo_open and x <= lo):
        raise Refused(f'{what}: {x!r}')
    return float(x)


def _gain(v, what):
    m = re.fullmatch(r'(\d+(?:\.\d+)?) ?%', v.strip()) if isinstance(v, str) else None
    if not m:
        raise Refused(f'{what}: volume {v!r} (expected "N%")')
    g = float(m.group(1)) / 100
    if g > MAX_GAIN:
        raise Refused(f'{what}: volume {v!r} is over {MAX_GAIN * 100:.0f}%')
    return g


def plan(body):
    """Check the body and return the mix plan (no files touched). Raises Refused."""
    if not isinstance(body, dict):
        raise Refused('the body is not an object')
    extra = sorted(set(body) - TOP_KEYS)
    if extra:
        raise Refused('unknown body keys: ' + ', '.join(extra))
    els = body.get('elements')
    if not isinstance(els, list) or not 1 <= len(els) <= MAX_ELEMENTS:
        raise Refused(f'elements: 1–{MAX_ELEMENTS} expected')
    out = []
    for i, e in enumerate(els):
        w = f'element {i}'
        if not isinstance(e, dict):
            raise Refused(w + ' is not an object')
        extra = sorted(set(e) - EL_KEYS)
        if extra:
            raise Refused(f'{w}: unknown keys ' + ', '.join(extra))
        if e.get('type') != 'audio':
            raise Refused(f'{w}: type {e.get("type")!r} (only audio)')
        src = e.get('source')
        if not isinstance(src, str) or not re.fullmatch(r'https://[^\s"\'<>]{4,2000}', src):
            raise Refused(f'{w}: source is not an https URL')
        if 'time' not in e:
            raise Refused(f'{w}: no time (Creatomate would place it after the last element of its track)')
        out.append({
            'i': i, 'source': src, 'time': _num(e['time'], w + ' time'),
            'trim_start': _num(e.get('trim_start', 0), w + ' trim_start'),
            'trim_duration': None if e.get('trim_duration') is None else _num(e['trim_duration'], w + ' trim_duration', lo_open=True),
            'duration': None if e.get('duration') is None else _num(e['duration'], w + ' duration', lo_open=True),
            'gain': _gain(e.get('volume', '100%'), w),
            'fade_in': _num(e.get('audio_fade_in', 0), w + ' audio_fade_in', hi=60),
            'fade_out': _num(e.get('audio_fade_out', 0), w + ' audio_fade_out', hi=60),
        })
    dur = body.get('duration')
    return {'elements': out, 'duration': None if dur is None else _num(dur, 'duration', lo_open=True)}


def sniff(path):
    """ffmpeg demuxer for a downloaded source, from its first bytes; None = not a plain audio container."""
    with open(path, 'rb') as f:
        h = f.read(12)
    if h[:3] == b'ID3':
        return 'mp3'
    if len(h) >= 2 and h[0] == 0xFF and (h[1] & 0xF6) == 0xF0:
        return 'aac'                      # ADTS
    if len(h) >= 2 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0:
        return 'mp3'                      # MPEG audio frame sync
    if h[:4] == b'RIFF' and h[8:12] == b'WAVE':
        return 'wav'
    if h[4:8] == b'ftyp':
        return 'mov'                      # mp4 / m4a / mov (external data references stay off: enable_drefs defaults to 0)
    if h[:4] == b'OggS':
        return 'ogg'
    if h[:4] == b'fLaC':
        return 'flac'
    return None


def probe(path, fmt):
    r = subprocess.run(['ffprobe', '-v', 'error', '-f', fmt, '-select_streams', 'a:0', '-show_entries',
                        'stream=channels,sample_rate:format=duration', '-of', 'json', path],
                       capture_output=True, text=True, timeout=60, env=ENV)
    if r.returncode:
        raise RuntimeError('ffprobe could not read a source: ' + r.stderr.strip()[-300:])
    j = json.loads(r.stdout or '{}')
    st = (j.get('streams') or [{}])[0]
    if not st.get('channels'):
        raise Refused('a source has no audio stream')
    return {'channels': int(st['channels']), 'rate': int(st.get('sample_rate') or 0),
            'dur': float((j.get('format') or {}).get('duration') or 0)}


def _f(x):
    return f'{x:.6f}'.rstrip('0').rstrip('.') or '0'


def graph(p, files):
    """ffmpeg inputs + filter graph for a checked plan; files = {source: {'path', 'fmt', 'channels', 'dur'}}."""
    els = []
    for e in p['elements']:
        avail = max(0.0, files[e['source']]['dur'] - e['trim_start'])
        play = e['trim_duration'] if e['trim_duration'] is not None else avail
        dur = e['duration'] if e['duration'] is not None else play
        if dur > 0:                       # a trim past the end of its file with no duration: nothing to place
            els.append({**e, 'play': play, 'dur': dur})
    if not els:
        raise Refused('no element has a length')
    srcs = list(dict.fromkeys(e['source'] for e in els))
    idx = {s: k for k, s in enumerate(srcs)}
    uses = {s: [e for e in els if e['source'] == s] for s in srcs}
    args, parts, labels, ends = [], [], [], []
    for s in srcs:
        args += ['-f', files[s]['fmt'], '-i', files[s]['path']]
    for s in srcs:
        k, n, ch = idx[s], len(uses[s]), files[s]['channels']
        lay = f'pan=stereo|c0={MONO}*c0|c1={MONO}*c0,' if ch == 1 else ('' if ch == 2 else 'aformat=channel_layouts=stereo,')
        head = f'[{k}:a]aresample={SR},{lay}aformat=sample_fmts=fltp:channel_layouts=stereo'
        parts.append(head + (f',asplit={n}' + ''.join(f'[s{k}_{j}]' for j in range(n)) if n > 1 else f'[s{k}_0]'))
        for j, e in enumerate(uses[s]):
            play, dur = e['play'], e['dur']
            ch_ = [f'atrim=start={_f(e["trim_start"])}' + (f':duration={_f(play)}' if e['trim_duration'] is not None else ''),
                   'asetpts=PTS-STARTPTS', f'apad=whole_dur={_f(dur)}', f'atrim=duration={_f(dur)}']
            if e['fade_in'] > 0:
                ch_.append(f'afade=t=in:st=0:d={_f(min(e["fade_in"], dur))}')
            if e['fade_out'] > 0:
                fo = min(e['fade_out'], dur)
                ch_.append(f'afade=t=out:st={_f(dur - fo)}:d={_f(fo)}')
            if e['gain'] != 1:
                ch_.append(f'volume={_f(e["gain"])}')
            d = int(round(e['time'] * SR))
            if d:
                ch_.append(f'adelay=delays={d}S:all=1')
            lab = f'e{e["i"]}'
            parts.append(f'[s{k}_{j}]' + ','.join(ch_) + f'[{lab}]')
            labels.append(lab); ends.append(e['time'] + dur)
    if not labels:
        raise Refused('no element has a length')
    total = p['duration'] if p['duration'] is not None else max(ends)
    mix = ''.join(f'[{x}]' for x in labels)
    parts.append(f'{mix}amix=inputs={len(labels)}:normalize=0:dropout_transition=0:duration=longest,'
                 f'apad=whole_dur={_f(total)},atrim=duration={_f(total)}[out]' if len(labels) > 1 else
                 f'{mix}apad=whole_dur={_f(total)},atrim=duration={_f(total)}[out]')
    return args, ';'.join(parts), total


def mix(body, files, out_wav, work):
    """body + {source: local path} -> 16-bit stereo WAV at out_wav. Returns the numbers the job reports."""
    p = plan(body)
    info = {}
    for s in dict.fromkeys(e['source'] for e in p['elements']):
        path = files.get(s)
        if not path or not os.path.isfile(path):
            raise RuntimeError('a source was not downloaded')
        fmt = sniff(path)
        if not fmt:
            raise Refused('a source is not a plain audio file (mp3, aac, wav, mp4/m4a, ogg, flac)')
        info[s] = {'path': path, 'fmt': fmt, **probe(path, fmt)}
    args, fg, total = graph(p, info)
    raw = os.path.join(work, 'mix.f32')
    with open(os.path.join(work, 'graph.txt'), 'w') as f:
        f.write(fg)
    r = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', *args,
                        '-filter_complex_script', os.path.join(work, 'graph.txt'), '-map', '[out]',
                        '-f', 'f32le', '-ac', '2', '-ar', str(SR), raw],
                       capture_output=True, text=True, timeout=600, env=ENV)
    if r.returncode:
        raise RuntimeError('ffmpeg mix failed: ' + r.stderr.strip()[-400:])
    x = np.fromfile(raw, dtype='<f4')
    if not x.size:
        raise RuntimeError('ffmpeg wrote an empty mix')
    peak = float(np.max(np.abs(x)))
    g = PEAK / peak if peak > PEAK else 1.0
    pcm = np.clip(np.round(x * (g * 32767.0)), -32768, 32767).astype('<i2')
    with wave.open(out_wav, 'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    os.remove(raw)
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    db = lambda v: round(20 * math.log10(v), 2) if v > 0 else None
    return {'seconds': round(x.size / 2 / SR, 3), 'expected': round(total, 3), 'elements': len(p['elements']),
            'sources': len(info), 'peakDb': db(peak), 'rmsDb': db(rms), 'gainDb': db(g) or 0.0,
            'bytes': os.path.getsize(out_wav)}
