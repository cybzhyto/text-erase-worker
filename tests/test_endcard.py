"""Tests for engine/endcard.py on clips drawn by ffmpeg (lavfi), no media files. Skipped when ffmpeg is missing.

    python -m unittest discover -s tests"""
import os, shutil, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'engine'))
import endcard as ec

FF = shutil.which('ffmpeg') and shutil.which('ffprobe')
SIZE, RATE = '270x480', 30


def card_src(box_y):
    """A dark card: dark ground, a light 'logo' box near the top, a light 'call to action' box at box_y."""
    return (f'color=c=0x140a16:s={SIZE}:r={RATE},'
            f'drawbox=x=95:y=90:w=80:h=24:color=0xe8e0e8:t=fill,'
            f'drawbox=x=45:y={box_y}:w=180:h=40:color=white:t=fill')


def make(path, parts, audio=True):
    """parts = [(lavfi source, seconds)] concatenated; a sine tone over the whole length."""
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y']
    for src, d in parts:
        cmd += ['-f', 'lavfi', '-t', str(d), '-i', src]
    total = sum(d for _, d in parts)
    if audio:
        cmd += ['-f', 'lavfi', '-t', str(total), '-i', 'sine=frequency=440:sample_rate=48000']
    n = len(parts)
    fc = ''.join(f'[{i}:v]format=yuv420p,setsar=1[v{i}];' for i in range(n)) + ''.join(f'[v{i}]' for i in range(n)) + \
        f'concat=n={n}:v=1:a=0[v]'
    cmd += ['-filter_complex', fc, '-map', '[v]']
    if audio:
        cmd += ['-map', f'{n}:a', '-c:a', 'aac']
    cmd += ['-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', path]
    subprocess.run(cmd, check=True)
    return path


SCENE = f'testsrc2=s={SIZE}:r={RATE}'     # bright, busy: never dark
OLD, NEW = card_src(300), card_src(200)   # two cards that share ground and logo, the CTA moved


@unittest.skipUnless(FF, 'ffmpeg not installed')
class EndCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.old = make(f'{cls.d}/old.mp4', [(SCENE, 6), (OLD, 4)])
        cls.cur = make(f'{cls.d}/cur.mp4', [(SCENE, 6), (NEW, 4)])
        cls.none = make(f'{cls.d}/none.mp4', [(SCENE, 8)])
        cls.dark = make(f'{cls.d}/dark.mp4', [(OLD, 12)])
        cls.card = make(f'{cls.d}/card.mp4', [(NEW, 3)])     # shorter than the slot: its last frame is held

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_find_cut(self):
        i = ec.find_cut(self.old)
        self.assertEqual(i['kind'], 'card')
        self.assertAlmostEqual(i['cut_at'], 6.0, delta=0.1)
        self.assertAlmostEqual(i['card_s'], 4.0, delta=0.15)

    def test_no_card(self):
        self.assertEqual(ec.find_cut(self.none)['kind'], 'none')

    def test_all_dark_refused(self):
        self.assertEqual(ec.find_cut(self.dark)['kind'], 'refuse')

    def test_matches(self):
        ok, _ = ec.matches(self.cur, self.card, ec.find_cut(self.cur))
        self.assertTrue(ok)
        ok, d = ec.matches(self.old, self.card, ec.find_cut(self.old))
        self.assertFalse(ok, d)

    def test_swap(self):
        out = f'{self.d}/out.mp4'
        r = ec.swap(self.old, self.card, out)
        src, o = ec.probe(self.old), ec.probe(out)
        self.assertLessEqual(abs(o['dur'] - src['dur']), 1.5 / RATE + 0.05)
        self.assertTrue(o['audio'])
        self.assertGreater(r['held_s'], 0.9)                                  # slot 4 s, card 3 s
        ok, _ = ec.matches(out, self.card, ec.find_cut(out))                  # the output now ends on the new card
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(ec.sheet(out, r['cut_at'], f'{self.d}/sheet.jpg')))

    def test_swap_refuses_without_card(self):
        with self.assertRaises(RuntimeError):
            ec.swap(self.none, self.card, f'{self.d}/x.mp4')


if __name__ == '__main__':
    unittest.main()
