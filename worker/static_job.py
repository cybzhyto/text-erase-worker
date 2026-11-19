#!/usr/bin/env python3
"""Run one static job: read a still ad image with a vision model, and when it breaks the caller's wants (call-to-action
words / no button shape / no arrow, price labels removed) fix only those regions with an image-edit model
(engine/staticfix.py), check the result with a second read, host it.

Job-record mode (GitHub Actions, .github/workflows/static.yml):
    static_job.py --job recXXXXXXXXXXXXXX --base appXXXXXXXXXXXXXX --table tblXXXXXXXXXXXXXX --field fldXXXXXXXXXXXXXX
    env AIRTABLE_TOKEN (read + write that one field of the record), FAL_KEY (models + hosting)
Local mode (no record, no upload; for a look at one image):
    static_job.py --spec job.json --work DIR          (FAL_KEY still needed for the models)

The field (long text, JSON v1) is written by the caller before the run; every model id and prompt comes from it, so
nothing about the caller's ads lives in this public repository:
    {"v": 1, "kind": "static", "src": "https://… .png|.jpg", "mime": "image/png"|"image/jpeg",
     "want": {"cta": "WORDS" | "ctaByLang": {"en": …, "es": …}, "prices": "remove"|"keep"},
     "read": {"model": "<vision model id>", "prompt": "… answer one JSON object …"},
     "edit": {"model": "<image-edit model id>", "cta": "prompt with {old} {new} {shape} {colour}", "tags": "prompt",
              "louder": "sentence added on the second try"},
     "hold": false, …the caller's own keys}
The read must answer {"cta": null | {"text", "box": [x0, y0, x1, y1] in thousandths, "shape", "arrow", "underline",
"colour"}, "price_tags": [{"text", "box"}], "language"}.
The worker merges its answer into the same JSON (the caller's keys stay):
    state  running -> one of
           current  nothing to fix: the image may be published as it is
           done     fixed: out = hosted image (same format as src), md5Fixed, bytes, fixes, qa
           review   fixed, but the job asked to hold it for a person's look (hold: true); same keys as done
           failed   an error or the check of the result failed after the tries: step, error
    run, at (UTC start), secs, readBefore, readAfter, fixes, metrics
A run starts by dropping the last run's keys (RUN_KEYS), so an old `out` never stands beside a new failure.

The repository is public, and so are its Actions logs: nothing from the record or the image (URLs, words, prompts) is
printed; stdout gets step names, counts and timings only."""
import argparse, hashlib, json, os, re, sys, time, traceback

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'engine'))
sys.path.insert(0, HERE)
import staticfix as sf                            # noqa: E402
import run_job                                    # noqa: E402
from run_job import say, step, http, download     # noqa: E402
from endcard_job import Field, host, ID           # noqa: E402

RUN_KEYS = ('state', 'run', 'at', 'secs', 'step', 'error', 'why', 'out', 'bytes', 'md5Fixed', 'fixes', 'qa',
            'readBefore', 'readAfter', 'metrics', 'tryN')
TRIES = 2                      # edit + check rounds inside one job (the second adds edit.louder)
FAL = 'https://queue.fal.run/'


def fal(model, body, limit=600):
    """One fal queue request: submit, poll, fetch the answer."""
    key = {'Authorization': 'Key ' + os.environ['FAL_KEY']}
    q = http('POST', FAL + model, body, headers=key)
    t = time.time()
    while time.time() - t < limit:
        time.sleep(3)
        s = http('GET', q['status_url'], headers=key)
        if s.get('status') == 'COMPLETED': return http('GET', q['response_url'], headers=key)
        if s.get('status') not in ('IN_QUEUE', 'IN_PROGRESS'): raise RuntimeError('model status ' + str(s.get('status')))
    raise RuntimeError(f'model did not answer in {limit} s')


def read(job, url):
    """The vision read of one hosted image -> dict (raises when the answer is not one JSON object)."""
    r = job['read']
    # reasoning: some models refuse a call with it switched off («Reasoning is mandatory for this endpoint», 2026-11-19)
    ans = fal('openrouter/router/vision', {'model': r['model'], 'prompt': r['prompt'], 'image_urls': [url], 'temperature': 0,
                                          'reasoning': bool(r.get('reasoning', True)),
                                          'max_tokens': int(r.get('maxTokens') or 6000)}, limit=300)
    o = last_object(str(ans.get('output') or ''), r.get('keys') or ('cta', 'price_tags'))
    if o is None: raise RuntimeError('the vision read has no JSON answer')
    return o


def last_object(text, keys):
    """The last JSON object in the text that has one of `keys` (an answer may come after reasoning that holds braces)."""
    dec, best = json.JSONDecoder(), None
    for i in [k for k, ch in enumerate(text) if ch == '{']:
        try:
            o, _ = dec.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(o, dict) and any(k in o for k in keys): best = o
    return best


def load(path):
    im = Image.open(path)
    alpha = im.getchannel('A') if im.mode in ('RGBA', 'LA') else None
    return np.array(im.convert('RGB')), alpha


def save(arr, alpha, path, mime):
    o = Image.fromarray(arr)
    if mime == 'image/jpeg':
        o.save(path, 'JPEG', quality=95, subsampling=0)
    else:
        if alpha is not None: o.putalpha(alpha)
        o.save(path, 'PNG')
    return path


def edit_region(job, img, boxes, prompt, W, H, work, n, hosted):
    """Crop round the boxes at a model ratio, host the crop, run the edit model, download its image."""
    crop, ratio = sf.crop_for(boxes, W, H)
    cp = save(img[crop[1]:crop[3], crop[0]:crop[2]], None, f'{work}/crop{n}.png', 'image/png')
    url = hosted(cp, f'crop{n}.png', 'image/png')
    e = job['edit']
    ans = fal(e['model'], {'prompt': prompt, 'image_urls': [url], 'aspect_ratio': ratio, 'resolution': e.get('resolution', '2K'),
                           'output_format': 'png', 'num_images': 1})
    imgs = ans.get('images') or []
    if not imgs or not imgs[0].get('url'): raise RuntimeError('the edit model returned no image')
    ep = download(imgs[0]['url'], f'{work}/edit{n}.png')
    return crop, np.array(Image.open(ep).convert('RGB'))


def fix(job, img, fixes, W, H, work, louder, hosted):
    """Every fix on the image, one edit call per kind; returns (image, metrics)."""
    e = job['edit']; out = img; metrics = []
    for k, f in enumerate(fixes):
        if f['kind'] == 'cta':
            prompt = e['cta'].format(old=f['old'], new=f['new'], shape=f['shape'] or 'button', colour=f['colour'] or 'the same colour as the old label')
            boxes, regions = [f['box']], [{'box': f['box'], 'pad': f['pad']}]
        else:
            prompt = e['tags']
            boxes, regions = f['boxes'], [{'box': b, 'pad': f['pad']} for b in f['boxes']]
        if louder and e.get('louder'): prompt += ' ' + e['louder']
        crop, ed = edit_region(job, out, boxes, prompt, W, H, work, f'{k}{"b" if louder else ""}', hosted)
        out, m = sf.paste(out, ed, crop, regions)
        m['kind'] = f['kind']; metrics.append(m)
        say(f'{f["kind"]}: ecc {m["ecc"]}, drift {m["drift"]}, {m["changedPx"]} px changed')
        if not m['aligned']: raise RuntimeError(f'{f["kind"]}: the edit could not be aligned to the original (ecc {m["ecc"]}, drift {m["drift"]})')
    return out, metrics


def main():
    ap = argparse.ArgumentParser()
    for k in ID: ap.add_argument('--' + k)
    ap.add_argument('--spec')
    ap.add_argument('--work', default=os.environ.get('RUNNER_TEMP', '/tmp') + '/static')
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True); W_ = a.work
    run_job.LOG = open(os.path.join(W_, 'log.txt'), 'a')
    rec = None
    if not a.spec:
        for k, rx in ID.items():
            if not re.fullmatch(rx, getattr(a, k) or ''): sys.exit(f'bad --{k}')
        rec = Field(a.base, a.table, a.job, a.field, run_keys=RUN_KEYS)
    stem = f'{a.job or "local"}-{os.environ.get("GITHUB_RUN_ID", "local")}'
    hosted = lambda p, name, mime: host(p, f'{stem}-{name}', mime)
    t0 = time.time()
    res = {}
    try:
        step('read job')
        if rec:
            job = rec.read()
            rec.write({'state': 'running', 'run': os.environ.get('GITHUB_RUN_ID'),
                       'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, fresh=True)
        else:
            job = json.load(open(a.spec))
        if job.get('v') != 1 or job.get('kind') != 'static' or not job.get('src') or not job.get('want') \
                or not (job.get('read') or {}).get('prompt') or not (job.get('edit') or {}).get('model'):
            raise RuntimeError('job v1 kind static with src, want, read, edit expected')
        mime = job.get('mime') or 'image/png'
        if mime not in ('image/png', 'image/jpeg'): raise RuntimeError('mime image/png or image/jpeg expected')

        step('download')
        src = download(job['src'], f'{W_}/src.img')
        img, alpha = load(src)
        H, W = img.shape[:2]
        say(f'image {W}x{H}')

        step('read')
        r0 = read(job, job['src'])
        res['readBefore'] = r0
        fixes = sf.plan(r0, job['want'], W, H)
        res['fixes'] = fixes
        if not fixes:
            res['state'] = 'current'; say('nothing to fix')
        else:
            say('to fix: ' + ', '.join(f['kind'] for f in fixes))
            ok, why, op, url = False, [], None, None
            for n in range(TRIES):
                res['tryN'] = n + 1
                step(f'edit, try {n + 1}')
                try:
                    out, metrics = fix(job, img, fixes, W, H, W_, n > 0, hosted)
                except RuntimeError as e:          # a model refusal / an edit that does not line up: one more try
                    why = [str(e)[:300]]; say('edit failed'); continue
                step(f'check, try {n + 1}')
                op = save(out, alpha, f'{W_}/out{n}.' + ('jpg' if mime == 'image/jpeg' else 'png'), mime)
                url = hosted(op, f'out{n}.' + ('jpg' if mime == 'image/jpeg' else 'png'), mime)
                r1 = read(job, url)
                ok, why = sf.qa(r1, job['want'], fixes)
                res.update({'readAfter': r1, 'metrics': metrics, 'qa': {'ok': ok, 'why': why}})
                say('check passed' if ok else f'check failed: {len(why)} issue(s)')
                if ok: break
            if not ok: raise RuntimeError('the fixed image failed the check: ' + '; '.join(why))
            data = open(op, 'rb').read()
            res.update({'out': url, 'bytes': len(data), 'md5Fixed': hashlib.md5(data).hexdigest(),
                        'state': 'review' if job.get('hold') else 'done'})
        res['secs'] = round(time.time() - t0, 1)
        if rec: rec.write(res)
        else: print(json.dumps(res, indent=1, ensure_ascii=False))
        step(f'end: {res["state"]}')
    except Exception as e:
        run_job.LOG.write(traceback.format_exc()); run_job.LOG.flush()
        err = {'state': 'failed', 'step': run_job.STEP['name'], 'error': str(e)[:1500], 'secs': round(time.time() - t0, 1)}
        say(f'failed at «{run_job.STEP["name"]}»')
        if rec:
            try: rec.write({**res, **err})
            except Exception: say('could not write the failure to the record')
        else: print(json.dumps({**res, **err}, indent=1, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
