#!/usr/bin/env python3
"""Run one media job of the caller's recipe off Creatomate's queue (engine/audiomix.py, engine/stills.py), hosted files back on
the record.

Job-record mode (GitHub Actions, .github/workflows/media.yml):
    media_job.py --job recXXXXXXXXXXXXXX --base appXXXXXXXXXXXXXX --table tblXXXXXXXXXXXXXX --field fldXXXXXXXXXXXXXX --key mix
    env AIRTABLE_TOKEN (read + write that one field of the record), FAL_KEY (hosting)
Local mode (no record, no upload):
    media_job.py --body BODY.json [--files URL_TO_PATH.json] --work DIR [--out MIX.wav]     (a batch writes its files in --work)

The job is one key of a long-text JSON field (the caller's Lab Requests › Options › mix), written by the caller before the run:
    {"v": 1, "kind": "mix", "body": {…a Creatomate audio body, see engine/audiomix.py…}, "state": "sent", "nonce": "…",
     …the caller's own keys}
    {"v": 1, "kind": "batch", "items": [{"k": 1, "body": {…}}, …], …}   (2026-11-27) up to MAX_ITEMS bodies in one run, each
     a Creatomate audio body (→ WAV, engine/audiomix.py) or still body (output_format jpg / png → that image, engine/stills.py);
     k = the caller's own id of the item (a number or a short word, unique in the batch); the sources are downloaded once.
The worker answers in the same object (the other keys of the field and of the job stay):
    state  running -> one of
           done     mix: out = hosted WAV (48 kHz stereo), seconds, bytes, peakDb, rmsDb, gainDb
                    batch: outs = [{k, out, format, bytes, …}] in the items' order, renders = how many
           refused  a body asks for something the engines do not do as Creatomate does: why (the caller renders it there);
                    a batch is refused whole, before anything is downloaded
           failed   an error: step, error
    ran = the nonce the run started from (the caller takes a result only when ran is its own nonce), run, at, secs
    The answer drops the job's bodies (body; items keep only their k): they are the field's bulk, and the field holds 100k.
A job whose state is not «sent» is left alone (a second start of the same job, or one the caller has already taken back).
One job at a time per record field: the field is read and written whole, so two jobs on one field at once would race.

The repository is public, and so are its Actions logs: nothing from the record (URLs, names, field values) is printed;
stdout gets step names, counts and timings only."""
import argparse, concurrent.futures, hashlib, json, os, re, subprocess, sys, time, traceback, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'engine'))
sys.path.insert(0, HERE)
import audiomix                                   # noqa: E402
import stills                                     # noqa: E402
import run_job                                    # noqa: E402
from run_job import say, step, http               # noqa: E402

ID = {'job': r'rec[A-Za-z0-9]{14}', 'base': r'app[A-Za-z0-9]{14}', 'table': r'tbl[A-Za-z0-9]{14}', 'field': r'fld[A-Za-z0-9]{14}',
      'key': r'[A-Za-z][A-Za-z0-9]{0,30}'}
RUN_KEYS = ('state', 'run', 'at', 'secs', 'step', 'error', 'why', 'out', 'ran', 'seconds', 'expected', 'elements', 'sources',
            'peakDb', 'rmsDb', 'gainDb', 'bytes', 'dlSecs', 'outs', 'renders')
KINDS = ('mix', 'batch')
MAX_ITEMS = 40
# sources come from the caller's own providers only (fal storage, Creatomate's output bucket, WaveSpeed's media CDN, where the
# recipe's clean copies live): a job record somebody edited by hand cannot make the runner fetch anything else
HOSTS = re.compile(r'(?:[a-z0-9-]+\.)*(?:fal\.media|fal\.ai|backblazeb2\.com)|d2h7xmz5gqybh9\.cloudfront\.net')
MAX_FILE = 200 << 20
CLEAN = {k: v for k, v in os.environ.items() if k not in ('AIRTABLE_TOKEN', 'FAL_KEY', 'GITHUB_TOKEN')}


class Job:
    """One key of one record's long-text JSON field, addressed by field id (survives a field rename)."""
    def __init__(self, base, table, rec, field, key):
        self.url = f'https://api.airtable.com/v0/{base}/{table}/{rec}'
        self.h = {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN']}
        self.field, self.key = field, key

    def _all(self):
        f = http('GET', self.url + '?returnFieldsByFieldId=true', headers=self.h).get('fields', {})
        try:
            o = json.loads(f.get(self.field) or '{}')
        except ValueError:
            raise RuntimeError('the job field is not JSON') from None
        if not isinstance(o, dict):
            raise RuntimeError('the job field is not a JSON object')
        return o

    def read(self):
        j = self._all().get(self.key)
        return j if isinstance(j, dict) else {}

    def write(self, upd, fresh=False, drop=False):
        o = self._all()                   # read again right before the write: the caller may have written other keys
        j = o.get(self.key) if isinstance(o.get(self.key), dict) else {}
        if fresh:
            j = {k: v for k, v in j.items() if k not in RUN_KEYS}
        if drop:                          # the answer: the bodies go (the field's bulk), a batch keeps its items' k
            j.pop('body', None)
            if isinstance(j.get('items'), list):
                j['items'] = [{'k': i.get('k')} for i in j['items'] if isinstance(i, dict)]
        j.update(upd)
        o[self.key] = j
        text = json.dumps(o, ensure_ascii=False, separators=(',', ':'))   # compact, like the caller writes it
        if len(text) > 99000:
            raise RuntimeError('the job field would pass Airtable\'s 100k limit')
        http('PATCH', self.url, {'fields': {self.field: text}}, headers=self.h)
        return j


def host(path, name, mime):
    """fal storage, one PUT (same as endcard_job.host)."""
    key = {'Authorization': 'Key ' + os.environ['FAL_KEY']}
    up = http('POST', 'https://rest.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3',
              {'content_type': mime, 'file_name': name}, headers=key)
    with open(path, 'rb') as f:
        data = f.read()
    http('PUT', up['upload_url'], data, headers={'Content-Type': mime}, timeout=600)
    return up['file_url']


def fetch_all(urls, folder, workers=8):
    """https sources -> local files, in parallel."""
    os.makedirs(folder, exist_ok=True)

    def one(u):
        host_ = urllib.parse.urlsplit(u).hostname or ''
        if not HOSTS.fullmatch(host_):
            raise audiomix.Refused('a source is on a host this worker does not fetch from')
        p = os.path.join(folder, hashlib.sha1(u.encode()).hexdigest()[:16])
        for k in range(3):
            r = subprocess.run(['curl', '-sSf', '--location', '--proto', '=https', '--proto-redir', '=https', '--max-redirs', '3',
                                '--max-filesize', str(MAX_FILE), '--connect-timeout', '20', '--max-time', '180',
                                '-o', p, u], stdout=run_job.LOG, stderr=run_job.LOG, env=CLEAN)
            if r.returncode == 0 and os.path.getsize(p) > 0:
                return u, p
            time.sleep(3 * (k + 1))
        raise RuntimeError('a source could not be downloaded')

    with concurrent.futures.ThreadPoolExecutor(workers) as ex:
        return dict(ex.map(one, urls))


REFUSED = (audiomix.Refused, stills.Refused)


def batch_plan(job):
    """Check every item of a batch job before anything is downloaded: [(k, 'still' | 'mix', body)]. Raises Refused."""
    items = job.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise audiomix.Refused(f'a batch needs 1-{MAX_ITEMS} items')
    seen, out = set(), []
    for n, it in enumerate(items):
        if not isinstance(it, dict) or not isinstance(it.get('body'), dict):
            raise audiomix.Refused(f'item {n}: no body')
        k = it.get('k', n)
        if isinstance(k, bool) or not isinstance(k, (int, str)) or not re.fullmatch(r'-?[A-Za-z0-9_]{1,24}', str(k)):
            raise audiomix.Refused(f'item {n}: k must be a number or a short word')
        if str(k) in seen:
            raise audiomix.Refused(f'item {n}: k {k} is used twice')
        seen.add(str(k))
        body = it['body']
        try:
            if stills.is_still(body):
                stills.plan(body)
                out.append((k, 'still', body))
            else:
                audiomix.plan(body)
                out.append((k, 'mix', body))
        except REFUSED as e:
            raise audiomix.Refused(f'item {k}: {e}') from None
    return out


def run_batch(job, rec, a, W, res):
    plans = batch_plan(job)               # refuse before downloading anything
    srcs = list(dict.fromkeys(e['source'] for _, _, b in plans for e in b['elements']))
    step(f'download {len(srcs)} sources')
    t1 = time.time()
    files = json.load(open(a.files)) if a.files and not rec else fetch_all(srcs, os.path.join(W, 'src'))
    res['dlSecs'] = round(time.time() - t1, 1)
    step(f'render {len(plans)} items')
    info, made = {}, []
    for n, (k, kind, body) in enumerate(plans):
        if kind == 'still':
            path = os.path.join(W, f'item{n}.{body["output_format"]}')
            r = stills.render(body, files, path, info)
            mime = 'image/png' if body['output_format'] == 'png' else 'image/jpeg'
        else:
            wd = os.path.join(W, f'item{n}')
            os.makedirs(wd, exist_ok=True)
            path = os.path.join(wd, 'mix.wav')
            r = audiomix.mix(body, files, path, wd)
            mime = 'audio/wav'
        made.append((k, path, mime, r))
    say(f'{len(made)} items, {sum(m[3]["bytes"] for m in made) >> 20} MiB')
    if rec:
        step(f'host {len(made)} files')
        run = os.environ.get('GITHUB_RUN_ID', 'local')

        def up(m):
            k, path, mime, _ = m
            return host(path, f'{a.job}-{a.key}-{str(k).replace("-", "m")}-{run}{os.path.splitext(path)[1]}', mime)

        with concurrent.futures.ThreadPoolExecutor(6) as ex:
            urls = list(ex.map(up, made))
    else:
        urls = [m[1] for m in made]
    keep = ('format', 'width', 'height', 'seconds', 'bytes')
    res['outs'] = [{'k': k, 'out': u, **{x: r[x] for x in keep if x in r}} for (k, _, _, r), u in zip(made, urls)]
    res['renders'] = len(made)


def run_mix(job, rec, a, W, res):
    body = job['body']
    audiomix.plan(body)                   # refuse before downloading anything
    srcs = list(dict.fromkeys(e['source'] for e in body['elements']))
    step(f'download {len(srcs)} sources')
    t1 = time.time()
    files = json.load(open(a.files)) if a.files and not rec else fetch_all(srcs, os.path.join(W, 'src'))
    res['dlSecs'] = round(time.time() - t1, 1)
    step(f'mix {len(body["elements"])} elements')
    out = a.out or os.path.join(W, 'mix.wav')
    res.update(audiomix.mix(body, files, out, W))
    say(f'{res["seconds"]:.2f} s, peak {res["peakDb"]} dB, gain {res["gainDb"]} dB, {res["bytes"] >> 20} MiB')
    if rec:
        step('host')
        res['out'] = host(out, f'{a.job}-{a.key}-{os.environ.get("GITHUB_RUN_ID", "local")}.wav', 'audio/wav')
    else:
        res['out'] = out


def main():
    ap = argparse.ArgumentParser()
    for k in ID:
        ap.add_argument('--' + k)
    ap.add_argument('--body', help='local mode: a JSON file with the body (or a whole job {kind, body} / {kind: batch, items})')
    ap.add_argument('--files', help='local mode: a JSON map source URL -> local file (no downloads)')
    ap.add_argument('--out', help='local mode: where to write the WAV of a mix')
    ap.add_argument('--work', default=os.environ.get('RUNNER_TEMP', '/tmp') + '/media')
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    W = a.work
    run_job.LOG = open(os.path.join(W, 'log.txt'), 'a')
    rec = None
    if not a.body:
        for k, rx in ID.items():
            if not re.fullmatch(rx, getattr(a, k) or ''):
                sys.exit(f'bad --{k}')
        rec = Job(a.base, a.table, a.job, a.field, a.key)
    t0 = time.time()
    res = {}
    try:
        step('read job')
        if rec:
            job = rec.read()
            if job.get('state') != 'sent':
                say(f'job state is «{job.get("state") or "none"}», not «sent»: nothing to do')
                return
            res['ran'] = job.get('nonce')       # every answer of this run, a refusal too, names the job it answers
            rec.write({'state': 'running', 'ran': res['ran'], 'run': os.environ.get('GITHUB_RUN_ID'),
                       'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, fresh=True)
        else:
            job = json.load(open(a.body))
            if 'body' not in job and 'items' not in job:
                job = {'v': 1, 'kind': 'mix', 'body': job}
            job.setdefault('v', 1)
        if job.get('v') != 1 or job.get('kind') not in KINDS:
            raise audiomix.Refused(f'job v1 with kind in {KINDS} expected')
        if job['kind'] == 'batch':
            run_batch(job, rec, a, W, res)
        elif isinstance(job.get('body'), dict):
            run_mix(job, rec, a, W, res)
        else:
            raise audiomix.Refused('a mix job needs a body')
        res['state'] = 'done'
    except REFUSED as e:
        res.update({'state': 'refused', 'why': str(e)[:500]})
        say('refused')
    except Exception as e:
        run_job.LOG.write(traceback.format_exc()); run_job.LOG.flush()
        res.update({'state': 'failed', 'step': run_job.STEP['name'], 'error': str(e)[:1500]})
        say(f'failed at «{run_job.STEP["name"]}»')
    res['secs'] = round(time.time() - t0, 1)
    if rec:
        try:
            rec.write(res, drop=True)
        except Exception:
            say('could not write the answer to the record')
            sys.exit(1)
    else:
        print(json.dumps(res, indent=1))
    step(f'end: {res["state"]}')
    if res['state'] == 'failed':
        sys.exit(1)


if __name__ == '__main__':
    main()
