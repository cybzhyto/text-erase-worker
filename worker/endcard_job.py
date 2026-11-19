#!/usr/bin/env python3
"""Run one end-card job: the old dark end card at the end of a video -> the new card (engine/endcard.py), hosted mp4.

Job-record mode (GitHub Actions, .github/workflows/endcard.yml):
    endcard_job.py --job recXXXXXXXXXXXXXX --base appXXXXXXXXXXXXXX --table tblXXXXXXXXXXXXXX --field fldXXXXXXXXXXXXXX
    env AIRTABLE_TOKEN (read + write that one field of the record), FAL_KEY (hosting)
Local mode (no record, no upload):
    endcard_job.py --src SRC.mp4|URL --card CARD.mp4|URL --work DIR

The field (long text, JSON v1) is written by the caller before the run:
    {"v": 1, "src": "https://… .mp4", "card": "https://… .mp4", …the caller's own keys}
The worker merges its answer into the same JSON (the caller's keys stay):
    state  running -> one of
           done     the old card was replaced: out = hosted mp4, sheet = hosted contact sheet (jpg)
           current  the video already ends on the new card: nothing written
           none     no end card at the end of the video: nothing written
           refused  a dark tail the engine will not touch (longer than a card, the whole file dark): why
           failed   an error or a failed check: step, error
    run, at (UTC start), secs, cutAt, cardS, heldS, crf, bytes, checks, matchDiff
A run starts by dropping the last run's keys (RUN_KEYS), so an old `out` never stands beside a new failure.

The repository is public, and so are its Actions logs: nothing from the record (URLs, names, field values) is printed;
stdout gets step names, counts and timings only."""
import argparse, json, os, re, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'engine'))
sys.path.insert(0, HERE)
import endcard                                    # noqa: E402
import run_job                                    # noqa: E402
from run_job import say, step, http, download     # noqa: E402

ID = {'job': r'rec[A-Za-z0-9]{14}', 'base': r'app[A-Za-z0-9]{14}', 'table': r'tbl[A-Za-z0-9]{14}', 'field': r'fld[A-Za-z0-9]{14}'}
RUN_KEYS = ('state', 'run', 'at', 'secs', 'step', 'error', 'why', 'out', 'sheet', 'cutAt', 'cardS', 'heldS', 'crf',
            'bytes', 'checks', 'matchDiff')


class Field:
    """One long-text JSON field of one record, addressed by field id (survives a field rename)."""
    def __init__(self, base, table, rec, field, run_keys=RUN_KEYS):
        self.url = f'https://api.airtable.com/v0/{base}/{table}/{rec}'
        self.h = {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN']}
        self.field = field
        self.run_keys = run_keys      # the keys a new run drops (static_job.py has its own)

    def read(self):
        f = http('GET', self.url + '?returnFieldsByFieldId=true', headers=self.h).get('fields', {})
        try: return json.loads(f.get(self.field) or '{}')
        except ValueError: raise RuntimeError('the job field is not JSON') from None

    def write(self, upd, fresh=False):
        o = self.read()
        if fresh: o = {k: v for k, v in o.items() if k not in self.run_keys}
        o.update(upd)
        http('PATCH', self.url, {'fields': {self.field: json.dumps(o, ensure_ascii=False, indent=1)}}, headers=self.h)
        return o


def host(path, name, mime):
    """fal storage, one PUT (the engine keeps an output under endcard.MAX_BYTES)."""
    key = {'Authorization': 'Key ' + os.environ['FAL_KEY']}
    up = http('POST', 'https://rest.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3',
              {'content_type': mime, 'file_name': name}, headers=key)
    with open(path, 'rb') as f: data = f.read()
    http('PUT', up['upload_url'], data, headers={'Content-Type': mime}, timeout=600)
    return up['file_url']


def fetch(src, path):
    return download(src, path) if re.match(r'https://', src) else src


def main():
    ap = argparse.ArgumentParser()
    for k in ID: ap.add_argument('--' + k)
    ap.add_argument('--src'); ap.add_argument('--card')
    ap.add_argument('--work', default=os.environ.get('RUNNER_TEMP', '/tmp') + '/endcard')
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True); W = a.work
    run_job.LOG = open(os.path.join(W, 'log.txt'), 'a')
    rec = None
    if not a.src:
        for k, rx in ID.items():
            if not re.fullmatch(rx, getattr(a, k) or ''): sys.exit(f'bad --{k}')
        rec = Field(a.base, a.table, a.job, a.field)
    t0 = time.time()
    res = {}
    try:
        step('read job')
        if rec:
            job = rec.read()
            if job.get('v') != 1 or not job.get('src') or not job.get('card'): raise RuntimeError('job v1 with src + card expected')
            rec.write({'state': 'running', 'run': os.environ.get('GITHUB_RUN_ID'),
                       'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, fresh=True)
        else:
            job = {'src': a.src, 'card': a.card}

        step('download')
        src = fetch(job['src'], f'{W}/src.mp4'); card = fetch(job['card'], f'{W}/card.mp4')
        S = endcard.probe(src)
        say(f'source {S["w"]}x{S["h"]} {S["fps"]:.2f} fps {S["dur"]:.2f} s, audio {"yes" if S["audio"] else "no"}')

        step('find end card')
        info = endcard.find_cut(src)
        if info['kind'] != 'card':
            res = {'state': 'none' if info['kind'] == 'none' else 'refused', 'why': info['why']}
            say(res['state'])
        else:
            res = {'cutAt': info['cut_at'], 'cardS': info['card_s']}
            say(f'card from {info["cut_at"]:.2f} s, {info["card_s"]:.2f} s long')
            ok, d = endcard.matches(src, card, info)
            res['matchDiff'] = d
            if ok:
                res['state'] = 'current'; say('already the new card')
            else:
                step('swap')
                out = f'{W}/out.mp4'
                r = endcard.swap(src, card, out, info)
                res.update({'cardS': r['card_s'], 'heldS': r['held_s'], 'crf': r['crf'], 'bytes': r['bytes'], 'checks': r['checks']})
                say(f'checks passed, crf {r["crf"]}, {r["bytes"] >> 20} MiB')
                step('sheet')
                sh = endcard.sheet(out, r['cut_at'], f'{W}/sheet.jpg')
                if rec:
                    step('host')
                    stem = f'{a.job}-{os.environ.get("GITHUB_RUN_ID", "local")}'
                    res['out'] = host(out, stem + '.mp4', 'video/mp4')
                    res['sheet'] = host(sh, stem + '.jpg', 'image/jpeg')
                else:
                    res['out'], res['sheet'] = out, sh
                res['state'] = 'done'
        res['secs'] = round(time.time() - t0, 1)
        if rec: rec.write(res)
        else: print(json.dumps(res, indent=1))
        step(f'end: {res["state"]}')
    except Exception as e:
        run_job.LOG.write(traceback.format_exc()); run_job.LOG.flush()
        err = {'state': 'failed', 'step': run_job.STEP['name'], 'error': str(e)[:1500], 'secs': round(time.time() - t0, 1)}
        say(f'failed at «{run_job.STEP["name"]}»')
        if rec:
            try: rec.write({**res, **err})
            except Exception: say('could not write the failure to the record')
        else: print(json.dumps({**res, **err}, indent=1))
        sys.exit(1)


if __name__ == '__main__':
    main()
