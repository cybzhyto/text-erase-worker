"""engine/stills.py on a drawn video (no media files, no network): what each key of a Creatomate still body does.

The engine itself was checked against Creatomate's own renders of one real job (the localization run of Lab 1045, 2026-11-27:
8 full-frame jpg stills and 7 png contact sheets of 1-27 tiles from three source videos): mean difference 0.35-1.18 levels per
image, 1.72 on the worst 270 x 480 tile; same frames (the first at or after the moment), same layout, same opaque black where
no tile is drawn. These tests pin the rules that match came from, so a later change cannot drift away from it unnoticed."""
import json, os, subprocess, sys, tempfile, unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'engine'))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'worker'))
import stills  # noqa: E402

FPS, W, H, N = 30, 72, 128, 60          # 2 s, 9:16-ish frames
URL = 'https://v3b.fal.media/files/x/clip.mp4'


def level(n):
    return 20 + 3 * n                   # frame n is drawn at this grey level (20 .. 197): the frame a still shows is readable


def video(path):
    """Frame n: a grey of level(n) in the middle half, white top quarter, black bottom quarter (to see what cover keeps)."""
    fr = np.zeros((N, H, W, 3), np.uint8)
    for n in range(N):
        fr[n, :, :] = level(n)
        fr[n, :H // 4] = 255
        fr[n, 3 * H // 4:] = 0
    raw = path + '.rgb'
    fr.tofile(raw)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS),
                    '-i', raw, '-c:v', 'libx264', '-qp', '0', '-pix_fmt', 'yuv444p', '-g', '15', path], check=True)
    os.remove(raw)
    return path


def read(path):
    pr = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', path],
                        capture_output=True, text=True, check=True)
    w, h = map(int, pr.stdout.strip().split(','))
    fmt = 'rgba' if path.endswith('.png') else 'rgb24'
    r = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', path, '-f', 'rawvideo', '-pix_fmt', fmt, '-'], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 4 if fmt == 'rgba' else 3).astype(np.int16)


def frame_of(px):
    """Which frame a middle-grey patch shows (nearest level)."""
    v = float(np.mean(px))
    return int(round((v - 20) / 3))


class Stills(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.files = {URL: video(os.path.join(cls.tmp, 'clip.mp4'))}

    def still(self, body, name='out'):
        out = os.path.join(self.tmp, f'{name}.{body["output_format"]}')
        r = stills.render(body, self.files, out)
        return r, read(out)

    def full(self, t, **el):
        e = {'type': 'video', 'source': URL, 'time': 0, 'duration': 2, 'fit': 'cover', 'volume': '0%'}
        e.update(el)
        return {'output_format': 'jpg', 'width': W, 'height': H, 'duration': 2, 'snapshot_time': t, 'elements': [e]}

    def test_first_frame_at_or_after_the_moment(self):
        # Creatomate's stills of Lab 1045: 20.17 s at 30 fps showed frame 606 (20.2 s), not 605; an exact frame time shows that frame
        for t, want in ((0.5, 15), (0.51, 16), (0.52, 16), (1.0 / 3, 10), (0.0, 0), (1.95, 59)):
            _, x = self.still(self.full(t))
            self.assertEqual(frame_of(x[H // 2 - 4:H // 2 + 4, W // 2 - 4:W // 2 + 4]), want, t)

    def test_trim_start_and_time(self):
        # the element shows its source at trim_start + (snapshot_time - time)
        _, x = self.still(self.full(1.0, time=0.5, trim_start=0.2))
        self.assertEqual(frame_of(x[H // 2 - 4:H // 2 + 4, W // 2 - 4:W // 2 + 4]), 21)    # 0.7 s
        # not on screen at the moment: nothing drawn, black
        _, x = self.still(self.full(0.2, time=0.5))
        self.assertLess(float(np.abs(x).max()), 3)

    def test_sheet_layout_cover_and_black(self):
        # two 36 x 36 tiles (cover of a 72 x 128 frame keeps its middle: grey only) at their centres; the rest of the 108 x 36
        # image stays opaque black; png = RGBA
        tile = lambda ts, cx: {'type': 'video', 'source': URL, 'trim_start': ts, 'time': 0, 'duration': 1, 'fit': 'cover',
                               'volume': '0%', 'width': 36, 'height': 36, 'x': cx, 'y': 18}
        b = {'output_format': 'png', 'width': 108, 'height': 36, 'duration': 1, 'elements': [tile(0.2, 18), tile(1.0, 54)]}
        r, x = self.still(b, 'sheet')
        self.assertEqual((r['width'], r['height'], r['elements'], r['frames']), (108, 36, 2, 2))
        self.assertEqual(int(x[..., 3].min()), 255)
        self.assertEqual(frame_of(x[4:32, 4:32, :3]), 6)
        self.assertEqual(frame_of(x[4:32, 40:68, :3]), 30)
        self.assertLess(float(np.abs(x[:, 72:, :3]).max()), 1)                              # no tile there: black
        self.assertGreater(float(x[2:34, 2:34, :3].std()), -1)                              # (shape check above is the point)
        self.assertLess(float(np.abs(x[2:34, 2:34, :3] - x[2:34, 2:34, :3].mean()).max()), 6)   # no white / black bands: cover cut them

    def test_cover_keeps_the_middle(self):
        # a 72 x 36 box over the 72 x 128 frame: scaled to cover (no change), its middle 36 rows (46-82) kept = grey only, no white
        # top or black bottom; the box sits centred at y 64 (rows 46-82 of the image)
        _, x = self.still(self.full(0.5, width=72, height=36, x=36, y=64))
        mid = x[47:81]
        self.assertLess(float(np.abs(mid - mid.mean()).max()), 6)
        self.assertEqual(frame_of(mid), 15)
        self.assertLess(float(np.abs(x[:44]).max()), 1)                                    # outside the box: black
        self.assertLess(float(np.abs(x[84:]).max()), 1)
        _, full = self.still(self.full(0.5))
        self.assertGreater(float(full[:20].mean()), 240)                                   # the whole frame shows its white top

    def test_refusals(self):
        bad = [
            ('unknown body key', lambda b: b.update({'fill_color': '#ffffff'})),
            ('unknown element key', lambda b: b['elements'][0].update({'animations': []})),
            ('not video', lambda b: b['elements'][0].update({'type': 'image'})),
            ('another fit', lambda b: b['elements'][0].update({'fit': 'contain'})),
            ('no fit', lambda b: b['elements'][0].pop('fit')),
            ('no time', lambda b: b['elements'][0].pop('time')),
            ('size in %', lambda b: b['elements'][0].update({'width': '50%'})),
            ('a video format', lambda b: b.update({'output_format': 'mp4'})),
            ('not https', lambda b: b['elements'][0].update({'source': 'file:///etc/passwd'})),
            ('box outside', lambda b: b['elements'][0].update({'width': 10, 'height': 10, 'x': -50, 'y': 10})),
            ('half pixels', lambda b: b.update({'width': 71.5})),
            ('no elements', lambda b: b.update({'elements': []})),
        ]
        for what, f in bad:
            b = self.full(0.5)
            f(b)
            with self.subTest(what):
                with self.assertRaises(stills.Refused):
                    stills.plan(b)
        stills.plan(self.full(0.5))                                                         # the good body passes
        with self.assertRaises(stills.Refused):                                             # a moment past the end of the source
            stills.render(self.full(5.0, duration=10), self.files, os.path.join(self.tmp, 'x.jpg'))

    def test_sniff(self):
        self.assertEqual(stills.sniff(self.files[URL]), 'mov')
        p = os.path.join(self.tmp, 'list.mp4')                                              # a playlist named like a video is not opened
        with open(p, 'w') as f:
            f.write('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\nfile:///etc/passwd\n')
        self.assertIsNone(stills.sniff(p))
        with self.assertRaises(stills.Refused):
            stills.render(self.full(0.5), {URL: p}, os.path.join(self.tmp, 'y.jpg'))

    def test_batch_local(self):
        # worker/media_job.py local mode: one still and one audio body in one batch, outputs in the items' order
        wav = os.path.join(self.tmp, 'tone.wav')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-ac', '2', wav], check=True)
        aurl = 'https://v3b.fal.media/files/x/tone.wav'
        job = {'v': 1, 'kind': 'batch', 'items': [
            {'k': 1, 'body': self.full(0.5)},
            {'k': -1, 'body': {'output_format': 'mp4', 'width': 64, 'height': 64, 'frame_rate': 1, 'duration': 1,
                               'elements': [{'type': 'audio', 'track': 1, 'time': 0, 'duration': 1, 'source': aurl, 'volume': '100%'}]}}]}
        jp, fp = os.path.join(self.tmp, 'job.json'), os.path.join(self.tmp, 'files.json')
        json.dump(job, open(jp, 'w'))
        json.dump({**self.files, aurl: wav}, open(fp, 'w'))
        work = os.path.join(self.tmp, 'work')
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'worker', 'media_job.py'), '--body', jp, '--files', fp,
                            '--work', work], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        res = json.loads(r.stdout[r.stdout.index('{'):r.stdout.rindex('}') + 1])
        self.assertEqual(res['state'], 'done', res)
        self.assertEqual([o['k'] for o in res['outs']], [1, -1])
        self.assertEqual((res['outs'][0]['format'], res['outs'][1]['seconds']), ('jpg', 1.0))
        self.assertTrue(all(os.path.isfile(o['out']) for o in res['outs']))
        # a batch with one body the engines refuse is refused whole, before anything is read
        job['items'][1]['body']['elements'][0]['type'] = 'text'
        json.dump(job, open(jp, 'w'))
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), 'worker', 'media_job.py'), '--body', jp, '--files', fp,
                            '--work', work], capture_output=True, text=True)
        res = json.loads(r.stdout[r.stdout.index('{'):r.stdout.rindex('}') + 1])
        self.assertEqual(res['state'], 'refused', res)
        self.assertIn('item -1', res['why'])


if __name__ == '__main__':
    unittest.main()
