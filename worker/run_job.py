#!/usr/bin/env python3
"""Run one text-erase job end to end: plate (old text erased from real pixels) + new overlay -> hosted mp4.

Job-record mode (GitHub Actions, .github/workflows/erase.yml):
    run_job.py --job recXXXXXXXXXXXXXX --base appXXXXXXXXXXXXXX --table tblXXXXXXXXXXXXXX
    env AIRTABLE_TOKEN (read + write the job record), CREATOMATE_API_KEY (overlay renders), FAL_KEY (hosting)
    The record's Options field (JSON) holds `erase.spec` = an https URL of the job spec (below), or the spec itself.
Local mode (no record, no upload):
    run_job.py --spec spec.json --work DIR

Spec v1 (JSON, written by the caller):
    {"v": 1,
     "src": "https://… .mp4"                  the source video (the old text is burned in)
     "renderBody": {…} | "renderBodyUrl": "…" the caller's full render body (RenderScript) of the new version
     "cfg": {…}                               erase zones and overlay rules for engine/build_math.py (see README)
     "name": "file-stem",                     hosted file name, optional
     "check": 40,                             plate.py --check threshold, optional
     "onDone": {field: value},                extra record fields to set after success (e.g. a status), optional
     "onFail": {field: value},                extra record fields to set after a failure, optional
     "gz": "…"}                               optional: base64 gzip of a JSON object merged into the spec (an inline spec
                                              in the record stays under its size limit)
On success the record gets Options.deliverUrl = the hosted mp4 and Options.erase = {state: done, …, check}.
On failure: Options.erase = {state: failed, step, error}; the caller decides what a failure means.

The repository is public, so are its Actions logs: nothing from the spec or the record (URLs, texts, field values)
is ever printed. Engine output goes to WORK/log.txt (never uploaded); stdout gets step names, counts and timings only."""
import argparse, base64, gzip, json, os, re, subprocess, sys, time, traceback, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), 'engine')
ID = {'job': r'rec[A-Za-z0-9]{14}', 'base': r'app[A-Za-z0-9]{14}', 'table': r'tbl[A-Za-z0-9]{14}'}
T0 = time.time()
STEP = {'name': 'start'}
LOG = None


def say(msg):
    """Public log line: step names, counts and timings only."""
    print(f'[{time.time() - T0:6.1f} s] {msg}', flush=True)


def step(name):
    STEP['name'] = name
    say(name)


def http(method, url, body=None, headers=None, timeout=120, tries=4):
    """JSON request with retries on 429 / 5xx / network errors. Returns the parsed answer (or {} for an empty one)."""
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    h = {'Content-Type': 'application/json'} if body is not None and not isinstance(body, bytes) else {}
    h.update(headers or {})
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=h), timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode('utf-8', 'replace')
            if e.code in (429, 500, 502, 503, 504) and k < tries - 1:
                time.sleep(5 * (k + 1)); continue
            raise RuntimeError(f'{method} {url.split("?")[0][:60]}… HTTP {e.code}: {detail}') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if k < tries - 1:
                time.sleep(5 * (k + 1)); continue
            raise RuntimeError(f'{method} failed after {tries} tries: {e}') from None


def download(url, path, tries=4):
    for k in range(tries):
        r = subprocess.run(['curl', '-sSfL', '--retry', '3', '-o', path, url], stdout=LOG, stderr=LOG)
        if r.returncode == 0 and os.path.getsize(path) > 0: return path
        time.sleep(5 * (k + 1))
    raise RuntimeError(f'download failed ({os.path.basename(path)})')


def run(args, **kw):
    """An engine step; its output goes to the private log only."""
    LOG.write(f'\n$ {" ".join(os.path.basename(a) if a.startswith("/") else a for a in args[:3])} …\n'); LOG.flush()
    r = subprocess.run(args, stdout=LOG, stderr=subprocess.STDOUT, **kw)
    if r.returncode: raise RuntimeError(f'{os.path.basename(args[1]) if len(args) > 1 else args[0]} exited {r.returncode}')


def probe(path):
    o = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,width,height,r_frame_rate',
                                   '-show_entries', 'format=duration', '-of', 'json', path], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in o['streams'] if s['codec_type'] == 'video')
    n, d = v['r_frame_rate'].split('/')
    return {'w': v['width'], 'h': v['height'], 'fps': float(n) / float(d), 'dur': float(o['format']['duration']),
            'vcodec': v['codec_name'], 'audio': [s['codec_name'] for s in o['streams'] if s['codec_type'] == 'audio']}


# ---- the job record (Airtable REST) ----
class Record:
    def __init__(self, base, table, rec, field='Options'):
        self.url = f'https://api.airtable.com/v0/{base}/{table}/{rec}'
        self.h = {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN']}
        self.field = field

    def options(self):
        f = http('GET', self.url, headers=self.h).get('fields', {})
        raw = f.get(self.field) or '{}'
        try: return json.loads(raw)
        except ValueError: raise RuntimeError(f'{self.field} is not JSON') from None

    def patch(self, erase=None, extra=None, deliver=None):
        """Read-modify-write of Options (erase block merged, deliverUrl set) + extra fields, in one PATCH."""
        o = self.options()
        if erase is not None: o['erase'] = {**(o.get('erase') or {}), **erase}
        if deliver: o['deliverUrl'] = deliver
        txt = json.dumps(o, ensure_ascii=False, indent=1)
        if len(txt) > 90000: txt = json.dumps(o, ensure_ascii=False, separators=(',', ':'))   # long text holds 100k chars
        fields = {self.field: txt}
        fields.update(extra or {})
        http('PATCH', self.url, {'fields': fields, 'typecast': True}, headers=self.h)


# ---- overlay renders (Creatomate v2) ----
def cm_start(body):
    r = http('POST', 'https://api.creatomate.com/v2/renders', body,
             headers={'Authorization': 'Bearer ' + os.environ['CREATOMATE_API_KEY']}, timeout=180)
    r = r[0] if isinstance(r, list) else r
    return r['id']


def cm_wait(rid, limit=1800):
    t = time.time()
    while time.time() - t < limit:
        r = http('GET', f'https://api.creatomate.com/v2/renders/{rid}', headers={'Authorization': 'Bearer ' + os.environ['CREATOMATE_API_KEY']})
        if r.get('status') == 'succeeded': return r['url']
        if r.get('status') == 'failed': raise RuntimeError(f'overlay render failed: {str(r.get("error_message"))[:200]}')
        time.sleep(15)
    raise RuntimeError('overlay render did not finish in 30 min')


# ---- hosting (fal storage: free; the caller accepts its links) ----
def host(path, name):
    size = os.path.getsize(path)
    if size > 95 * 2 ** 20: raise RuntimeError(f'output {size >> 20} MiB: over the one-PUT limit (multipart not built)')
    key = {'Authorization': 'Key ' + os.environ['FAL_KEY']}
    up = http('POST', 'https://rest.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3',
              {'content_type': 'video/mp4', 'file_name': name}, headers=key)
    with open(path, 'rb') as f: data = f.read()
    http('PUT', up['upload_url'], data, headers={'Content-Type': 'video/mp4'}, timeout=600)
    return up['file_url']


def parse_check(text):
    """plate.py --check output -> {residue: [[zone, mode, t0, t1, frames, worst_t, worst_px]], offText: n}"""
    res = [[int(m[0]), m[1], float(m[2]), float(m[3]), int(m[4]), float(m[5]), int(m[6])] for m in
           re.findall(r'zone (\d+) (\w+) ([\d.]+)-([\d.]+) rect \[[^\]]*\]: (\d+) frames, worst \(([\d.]+), (\d+)\)', text)]
    off = re.search(r'blur off the text in (\d+) frames', text)
    return {'residue': res, 'offText': int(off.group(1)) if off else 0}


def main():
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument('--job'); ap.add_argument('--base'); ap.add_argument('--table')
    ap.add_argument('--spec', help='local spec file (no record, no upload)')
    ap.add_argument('--plate-only', action='store_true', help='local: stop after the plate check (no overlay renders)')
    ap.add_argument('--work', default=os.environ.get('RUNNER_TEMP', '/tmp') + '/erase')
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True); W = a.work
    LOG = open(os.path.join(W, 'log.txt'), 'a')
    rec = None
    if not a.spec:
        for k, rx in ID.items():
            if not re.fullmatch(rx, getattr(a, k) or ''): sys.exit(f'bad --{k}')
        rec = Record(a.base, a.table, a.job)
    spec = {}
    try:
        step('read job')
        if rec:
            sp = (rec.options().get('erase') or {}).get('spec')
            if isinstance(sp, dict): spec = sp                          # inline (tests; a big render body would not fit)
            elif isinstance(sp, str) and sp.startswith('https://'): spec = http('GET', sp)
            else: raise RuntimeError('Options.erase.spec is missing')
            rec.patch(erase={'state': 'running', 'run': os.environ.get('GITHUB_RUN_ID'), 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        else:
            spec = json.load(open(a.spec))
        if spec.get('gz'): spec = {**spec, **json.loads(gzip.decompress(base64.b64decode(spec.pop('gz'))))}   # packed fields
        if spec.get('v') != 1: raise RuntimeError('spec v1 expected')
        rb = spec.get('renderBody') or http('GET', spec['renderBodyUrl'])
        json.dump(rb, open(f'{W}/rb.json', 'w')); json.dump(spec.get('cfg') or {}, open(f'{W}/cfg.json', 'w'))

        step('download source')
        src = download(spec['src'], f'{W}/src.mp4'); S = probe(src)
        say(f'source {S["w"]}x{S["h"]} {S["fps"]:.2f} fps {S["dur"]:.2f} s')

        step('split render body')
        run([sys.executable, f'{ENGINE}/build_math.py', f'{W}/rb.json', spec['src'], f'{W}/ov', f'{W}/cfg.json'])
        Z = json.load(open(f'{W}/ov.zones.json'))
        say(f'{len(Z["zones"])} erase zones')

        if a.plate_only:
            ids = {}
        else:
            step('start overlay renders')
            ids = {k: cm_start(json.load(open(f'{W}/ov.{k}.json'))) for k in ('black', 'white')}

        step('erase (plate)')
        run([sys.executable, f'{ENGINE}/plate.py', f'{W}/ov.zones.json', src, '-', '--static-only'])
        run([sys.executable, f'{ENGINE}/plate.py', f'{W}/ov.zones.json', src, f'{W}/plate.mp4'])

        step('check plate')
        mark = LOG.tell()
        run([sys.executable, f'{ENGINE}/plate.py', f'{W}/ov.zones.json', src, f'{W}/plate.mp4', '--check', str(spec.get('check', 40))])
        LOG.flush(); chk = parse_check(open(f'{W}/log.txt').read()[mark:])
        say(f'check: {len(chk["residue"])} zones with residue, blur off the text in {chk["offText"]} frames')

        if a.plate_only:
            step(f'done (plate only): {W}/plate.mp4'); return
        step('wait overlay renders')
        for k, rid in ids.items(): download(cm_wait(rid), f'{W}/ov{k[0].upper()}.mp4')

        step('composite')
        cap = [f'{W}/ov.captions.json'] if os.path.exists(f'{W}/ov.captions.json') else []
        run([sys.executable, f'{ENGINE}/composite.py', f'{W}/plate.mp4', f'{W}/ovB.mp4', f'{W}/ovW.mp4', f'{W}/out.mp4'] + cap)

        step('verify output')
        O = probe(f'{W}/out.mp4')
        bad = [x for x, ok in (('frame size', (O['w'], O['h']) == (S['w'], S['h'])), ('length', abs(O['dur'] - S['dur']) <= 0.2),
                               ('video codec', O['vcodec'] in ('h264', 'hevc')), ('audio', O['audio'][:1] == ['aac'])) if not ok]
        if bad: raise RuntimeError('output differs from the source: ' + ', '.join(bad))
        say(f'output {O["dur"]:.2f} s, {os.path.getsize(W + "/out.mp4") >> 20} MiB')

        if not rec:
            step(f'done (local): {W}/out.mp4'); return
        step('host output')
        name = re.sub(r'[^A-Za-z0-9_.-]', '-', spec.get('name') or 'erase') + '.mp4'
        url = host(f'{W}/out.mp4', name)

        step('write job')
        rec.patch(erase={'state': 'done', 'secs': round(time.time() - T0), 'check': chk, 'error': None}, deliver=url, extra=spec.get('onDone'))
        step('done')
    except Exception as e:
        LOG.write('\n' + traceback.format_exc()); LOG.flush()
        say(f'FAILED at «{STEP["name"]}»: {type(e).__name__}')   # the message itself may carry private text: record only
        if rec:
            try:
                tail = open(f'{W}/log.txt').read()[-1500:]
                rec.patch(erase={'state': 'failed', 'step': STEP['name'], 'error': f'{e}'[:600], 'logTail': tail,
                                 'secs': round(time.time() - T0)}, extra=spec.get('onFail'))
            except Exception as e2:
                say(f'could not write the failure to the job: {type(e2).__name__}')
        sys.exit(1)


if __name__ == '__main__':
    main()
