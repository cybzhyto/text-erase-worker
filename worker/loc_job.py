#!/usr/bin/env python3
"""Run one playable localization job (a stage of it): test a localized playable in a browser, or localize a looping end-card
video (engine/vidloc.py) and wrap it in a playable page.

Job-record mode (GitHub Actions, .github/workflows/loc.yml):
    loc_job.py --job recXXXXXXXXXXXXXX --base appXXXXXXXXXXXXXX --table tblXXXXXXXXXXXXXX
    env AIRTABLE_TOKEN (read + write the record's Options field), FAL_KEY (hosting)
Local mode (no record, no upload):
    loc_job.py --spec job.json --work DIR

The record's Options field (JSON) holds `locPlay`, written by the caller before each run:
    {"v": 1, "stage": "test" | "frame" | "build",
     "src": "https://…",            the source playable (html; hosted by the caller)
     "out": "https://…",            stage test: the localized playable the caller built
     "mechanic": "match" | …,       stage test: the smoke test to run (playable/smoke/<mechanic>.js; video = the video wrapper)
     "video": "https://… .mp4",     frame / build, optional: the end card's video (else taken from src: an inline
                                    data:video/mp4 source, or an AI interactive's videoSrc)
     "plan": {…},                   stage build: engine/vidloc.py PLAN (blocks in px, clickUrl, title, lang)
     "name": "file-stem",           hosted file names, optional
     "onDone": {field: value}, "onFail": {field: value}}   extra record fields after the run (the caller's re-queue)
The worker answers in the same block (the caller's keys stay):
    state  running -> frame    stage frame: frame = hosted jpg of the video's median frame (the words stand still in it),
                               w, h, fps, dur, frames, videoUrl (the video, hosted when it came inline)
                   -> done     stage test / build: Options.deliverUrl = the hosted localized playable (.txt, text/html inside);
                               smoke = {src: [fails], out: [fails]}, sheet (build: old | new frame), report (build)
                   -> failed   step, error (and smoke when a test failed)
A test passes when every check the source passes also passes on the localized file (a source that fails its own check is
not the localization's fault, so it is listed, not held against it).
A run starts by dropping the last run's keys (RUN_KEYS) and Options.deliverUrl.

The repository is public, and so are its Actions logs: nothing from the record (URLs, words, field values) is printed;
stdout gets step names, counts and timings only. The smoke tests' own output goes to WORK/log.txt (never uploaded)."""
import argparse, base64, json, os, re, subprocess, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'engine'))
sys.path.insert(0, HERE)
import run_job                                    # noqa: E402
from run_job import say, step, http, download     # noqa: E402
from endcard_job import host                      # noqa: E402

ID = {'job': r'rec[A-Za-z0-9]{14}', 'base': r'app[A-Za-z0-9]{14}', 'table': r'tbl[A-Za-z0-9]{14}'}
RUN_KEYS = ('state', 'run', 'at', 'secs', 'step', 'error', 'smoke', 'sheet', 'report', 'frame', 'w', 'h', 'fps', 'dur',
            'frames', 'videoUrl', 'delivered')
SMOKES = ('dress', 'match', 'match3', 'sudoku', 'dressup', 'style', 'assemble', 'outfits', 'swap', 'duel', 'video')
CFG_RE = re.compile(r'<script type="application/json" id="cfg">([\s\S]*?)</script>')


class Rec(run_job.Record):
    def patch_loc(self, upd=None, deliver=None, extra=None, fresh=False):
        """Read-modify-write of Options: the locPlay block merged, deliverUrl set or dropped, extra fields, one PATCH."""
        o = self.options()
        blk = o.get('locPlay') or {}
        if fresh:
            blk = {k: v for k, v in blk.items() if k not in RUN_KEYS}
            o.pop('deliverUrl', None)
        o['locPlay'] = {**blk, **(upd or {})}
        if deliver: o['deliverUrl'] = deliver
        txt = json.dumps(o, ensure_ascii=False, indent=1)
        if len(txt) > 90000: txt = json.dumps(o, ensure_ascii=False, separators=(',', ':'))
        http('PATCH', self.url, {'fields': {self.field: txt, **(extra or {})}, 'typecast': True}, headers=self.h)


def get(src, path):
    """A https link downloaded, or a local path (local mode) as it is."""
    if re.match(r'https://', str(src or '')): return download(src, path)
    if src and os.path.exists(src): return src
    raise RuntimeError('a source link is missing or not https')


def click_of(html):
    m = CFG_RE.search(html)
    try: return (json.loads(m.group(1)) if m else {}).get('clickUrl') or ''
    except ValueError: return ''


def smoke(mech, path, shots):
    """One smoke test on one file -> the list of failed checks (numbers blanked, so two files compare), [] = passed."""
    os.makedirs(shots, exist_ok=True)
    js = os.path.join(ROOT, 'playable', 'smoke', mech + '.js')
    html = open(path, encoding='utf-8').read()
    args = ['node', js, path, '--shots', shots] if mech == 'duel' else ['node', js, path, shots]
    env = {**os.environ, 'SMOKE_CLICK': click_of(html)}
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=600, env=env, cwd=ROOT)
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return ['the smoke test did not finish in 10 min']
    run_job.LOG.write(f'--- smoke {mech} {os.path.basename(path)}\n{out[-20000:]}\n'); run_job.LOG.flush()
    if mech == 'duel':
        if 'all runs passed' in out: return []
        fails = [l.strip() for l in out.splitlines() if l.strip().startswith('✗') or l.startswith('    ')]
    else:
        if out.rstrip().endswith('ALL OK'): return []
        tail = ('\n' + out).split('\nFAIL\n', 1)
        fails = [l.strip() for l in tail[1].splitlines() if l.strip()] if len(tail) == 2 else []
    fails = fails or ['the smoke test did not run to its end: ' + (out.strip().splitlines() or ['no output'])[-1][:200]]
    return sorted({re.sub(r'\d+(?:\.\d+)?', '#', f)[:300] for f in fails})


def video_of(job, W):
    """The end card's video: the job's link, the inline data:video/mp4 source of src, or an AI interactive's videoSrc."""
    if job.get('video'): return get(job['video'], f'{W}/src.mp4'), job['video']
    html = open(get(job['src'], f'{W}/src.html'), encoding='utf-8', errors='replace').read()
    m = re.search(r'data:video/mp4;base64,([A-Za-z0-9+/=\s]+)', html)
    if m:
        p = f'{W}/src.mp4'
        with open(p, 'wb') as f: f.write(base64.b64decode(re.sub(r'\s+', '', m.group(1))))
        return p, None
    m = re.search(r'window\.__SSR_PROPS__\s*=\s*(\{[\s\S]*?\})\s*(?:;|</script>)', html)
    if m:
        try: url = json.loads(m.group(1)).get('videoSrc')
        except ValueError: url = None
        if url and re.match(r'https://', url): return download(url, f'{W}/src.mp4'), url
    raise RuntimeError('no video in the source (no inline mp4, no videoSrc)')


def median_frame(path, out, n=24):
    """The median of n frames spread over the video: the still words stay sharp, what moves blurs out."""
    import numpy as np, cv2
    W_, H_, fps, dur = probe(path)
    frames = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.3f}', '-i', path, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
                             capture_output=True, check=True).stdout
        if len(raw) == W_ * H_ * 3: frames.append(np.frombuffer(raw, np.uint8).reshape(H_, W_, 3))
    if not frames: raise RuntimeError('no frame could be read from the video')
    med = np.median(np.stack(frames), axis=0).astype(np.uint8)
    cv2.imwrite(out, med, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return {'w': W_, 'h': H_, 'fps': round(fps, 3), 'dur': round(dur, 3), 'frames': int(round(fps * dur))}


def probe(path):
    o = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,r_frame_rate',
                                   '-show_entries', 'format=duration', '-of', 'json', path], capture_output=True, text=True, check=True).stdout)
    s = o['streams'][0]; n, d = s['r_frame_rate'].split('/')
    return s['width'], s['height'], float(n) / float(d), float(o['format']['duration'])


def main():
    ap = argparse.ArgumentParser()
    for k in ID: ap.add_argument('--' + k)
    ap.add_argument('--spec')
    ap.add_argument('--work', default=os.environ.get('RUNNER_TEMP', '/tmp') + '/loc')
    ap.add_argument('--setup-failed', action='store_true', help='the workflow failed before the job ran: write that and re-queue')
    a = ap.parse_args()
    if a.setup_failed:
        for k, rx in ID.items():
            if not re.fullmatch(rx, getattr(a, k) or ''): sys.exit(f'bad --{k}')
        rec = Rec(a.base, a.table, a.job)
        job = rec.options().get('locPlay') or {}
        rec.patch_loc({'state': 'failed', 'run': os.environ.get('GITHUB_RUN_ID'), 'step': 'setup',
                       'error': 'the worker could not set up its tools (see the GitHub Actions run)'}, fresh=True, extra=job.get('onFail'))
        print('setup failure written'); return
    os.makedirs(a.work, exist_ok=True); W = a.work
    run_job.LOG = open(os.path.join(W, 'log.txt'), 'a')
    rec = None
    if not a.spec:
        for k, rx in ID.items():
            if not re.fullmatch(rx, getattr(a, k) or ''): sys.exit(f'bad --{k}')
        rec = Rec(a.base, a.table, a.job)
    t0 = time.time(); res = {}; job = {}; deliver = None
    stem = f'{a.job or "local"}-{os.environ.get("GITHUB_RUN_ID", "local")}'
    try:
        step('read job')
        job = (rec.options().get('locPlay') or {}) if rec else json.load(open(a.spec))
        if job.get('v') != 1 or job.get('stage') not in ('test', 'frame', 'build'): raise RuntimeError('locPlay v1 with a stage expected')
        if rec: rec.patch_loc({'state': 'running', 'run': os.environ.get('GITHUB_RUN_ID'),
                               'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, fresh=True)
        stage = job['stage']; say(f'stage {stage}')

        if stage == 'test':
            mech = job.get('mechanic')
            if mech not in SMOKES: raise RuntimeError('no smoke test for this mechanic')
            step('download')
            src = get(job['src'], f'{W}/src.html'); out = get(job['out'], f'{W}/out.html')
            step('smoke source'); fs = smoke(mech, src, f'{W}/shots/src'); say(f'source: {len(fs)} failed check(s)')
            step('smoke localized'); fo = smoke(mech, out, f'{W}/shots/out'); say(f'localized: {len(fo)} failed check(s)')
            res['smoke'] = {'src': fs, 'out': fo}
            new = [f for f in fo if f not in fs]
            if new: raise RuntimeError(f'{len(new)} check(s) fail on the localized file only: ' + ' | '.join(new)[:1200])
            deliver = job['out']
            res['state'] = 'done'

        elif stage == 'frame':
            step('video')
            path, url = video_of(job, W)
            step('median frame')
            res.update(median_frame(path, f'{W}/frame.jpg'))
            say(f'{res["w"]}x{res["h"]}, {res["dur"]} s')
            if rec:
                step('host')
                res['frame'] = host(f'{W}/frame.jpg', stem + '-frame.jpg', 'image/jpeg')
                res['videoUrl'] = url or host(path, stem + '-src.mp4', 'video/mp4')
            else:
                res['frame'], res['videoUrl'] = f'{W}/frame.jpg', url or path
            res['state'] = 'frame'

        else:   # build
            if not isinstance(job.get('plan'), dict) or not job['plan'].get('blocks'): raise RuntimeError('stage build needs a plan with blocks')
            step('video')
            path = get(job['videoUrl'], f'{W}/src.mp4') if job.get('videoUrl') else video_of(job, W)[0]
            json.dump(job['plan'], open(f'{W}/plan.json', 'w'), ensure_ascii=False)
            step('localize video')
            r = subprocess.run([sys.executable, os.path.join(ROOT, 'engine', 'vidloc.py'), f'{W}/plan.json', path, f'{W}/vid'],
                               capture_output=True, text=True, timeout=1500)
            run_job.LOG.write(r.stdout + r.stderr); run_job.LOG.flush()
            rep = json.load(open(f'{W}/vid/report.json')) if os.path.exists(f'{W}/vid/report.json') else {}
            res['report'] = {'blocks': [{k: b.get(k) for k in ('key', 'font', 'size', 'measured', 'residueFrames', 'angle', 'rewrapped', 'lines')}
                                        for b in rep.get('blocks', [])], 'htmlBytes': rep.get('htmlBytes'), 'mp4Bytes': rep.get('mp4Bytes')}
            if r.returncode:
                raise RuntimeError('the video engine stopped: ' + ((r.stderr or r.stdout).strip().splitlines() or ['no output'])[-1][:600])
            step('smoke')
            fo = smoke('video', f'{W}/vid/out.html', f'{W}/shots/out'); res['smoke'] = {'out': fo}
            if fo: raise RuntimeError(f'{len(fo)} check(s) fail on the localized end card: ' + ' | '.join(fo)[:1200])
            if rec:
                step('host')
                deliver = host(f'{W}/vid/out.html', (job.get('name') or stem) + '.txt', 'text/plain')
                res['sheet'] = host(f'{W}/vid/sheet.jpg', stem + '-sheet.jpg', 'image/jpeg')
            else:
                deliver, res['sheet'] = f'{W}/vid/out.html', f'{W}/vid/sheet.jpg'
            res['state'] = 'done'

        res['secs'] = round(time.time() - t0, 1)
        if deliver: res['delivered'] = True
        if rec: rec.patch_loc(res, deliver=deliver, extra=job.get('onDone'))
        else: print(json.dumps({**res, 'deliverUrl': deliver}, indent=1, ensure_ascii=False))
        step(f'end: {res["state"]}')
    except Exception as e:
        run_job.LOG.write(traceback.format_exc()); run_job.LOG.flush()
        err = {'state': 'failed', 'step': run_job.STEP['name'], 'error': str(e)[:1500], 'secs': round(time.time() - t0, 1)}
        say(f'failed at «{run_job.STEP["name"]}»')
        if rec:
            try: rec.patch_loc({**res, **err}, extra=job.get('onFail'))
            except Exception: say('could not write the failure to the record')
        else: print(json.dumps({**res, **err}, indent=1, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
