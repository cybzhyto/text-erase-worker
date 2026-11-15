"""Tests for the zone helpers of engine/auto_zones.py on drawn frames (no media files).

    python -m unittest discover -s tests"""
import os, sys, unittest
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'engine'))
import auto_zones as az

W, H = 1080, 1920
FONT = ImageFont.load_default(size=44)


def line(text='Sample words here', y=640, level=170, bg=24):
    """A gray frame with one centred text line whose top is at row y -> (frame, its rect [x0, y0, x1, y1])."""
    im = Image.new('L', (W, H), bg); d = ImageDraw.Draw(im)
    b = d.textbbox((0, 0), text, font=FONT); x = (W - (b[2] - b[0])) // 2
    d.text((x - b[0], y - b[1]), text, font=FONT, fill=level)
    return np.asarray(im).copy(), [x - 12, y - 10, x + b[2] - b[0] + 12, y + b[3] - b[1] + 10]


class CutOut(unittest.TestCase):
    def test_a_box_at_the_corner_leaves_the_rest_of_the_rect(self):
        r = [100, 100, 500, 300]; k = [450, 250, 600, 400]
        out, cut = az.cut_out(r, [k])
        self.assertEqual(cut, [k])
        area = sum((q[2] - q[0]) * (q[3] - q[1]) for q in out)
        self.assertEqual(area, 400 * 200 - 50 * 50)                       # all of the rect but the corner under the box
        for q in out:                                                     # and no piece under the box
            self.assertTrue(q[2] <= k[0] or q[3] <= k[1], q)

    def test_a_box_outside_changes_nothing(self):
        out, cut = az.cut_out([100, 100, 500, 300], [[600, 100, 700, 300]])
        self.assertEqual((out, cut), ([[100, 100, 500, 300]], []))

    def test_a_box_over_the_whole_rect_leaves_nothing(self):
        self.assertEqual(az.cut_out([100, 100, 500, 300], [[0, 0, 900, 900]])[0], [])

    def test_across_is_half_of_the_width(self):
        r = [100, 100, 500, 300]
        self.assertTrue(az.across([150, 0, 480, 110], r))                 # a logo over the block
        self.assertFalse(az.across([450, 250, 600, 400], r))              # a tag at its corner


class BrightParts(unittest.TestCase):
    """Light letters on bright ground: the part of the block that gets a zone for the letters' dark shadow."""
    def frames(self, inside, below):
        L = np.full((H, W), 90, np.uint8); L[600:700, 200:880] = inside; L[700:760, 200:880] = below
        return [(i / 4, L.copy()) for i in range(8)]

    def test_a_dark_block_next_to_bright_cards_gets_none(self):
        self.assertEqual(az.bright_parts(self.frames(90, 250), 0, 2, [200, 600, 880, 700], 50), [])
        self.assertEqual(az.bright_parts(self.frames(200, 250), 0, 2, [200, 600, 880, 700], 50), [])     # mid-bright is not bright

    def test_a_block_on_bright_ground_gets_its_last_lines(self):
        out = az.bright_parts(self.frames(235, 250), 0, 2, [200, 600, 880, 700], 50)
        self.assertEqual(len(out), 1)
        x0, y0, x1, y1 = out[0]
        self.assertTrue(x0 <= 230 and x1 >= 850 and y1 == 700 and 600 <= y0 <= 640, out)

    def test_no_bright_strip_beside_the_block_no_part(self):
        self.assertEqual(az.bright_parts(self.frames(235, 90), 0, 2, [200, 600, 880, 700], 50), [])


class JobLog(unittest.TestCase):
    def test_the_check_is_read_from_a_byte_offset_in_a_log_with_other_alphabets(self):
        import tempfile
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'worker'))
        import run_job
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'log.txt')
            with open(p, 'w', encoding='utf-8') as f:
                f.write('page: ¿Qué más? · сторінка: приклад тексту\n' * 40); f.flush(); mark = os.path.getsize(p)
                f.write('zone 3 cap 1.00-9.00 rect [0, 10, 20, 30]: 12 frames, worst (2.5, 640)\ncheck: blur off the text in 2 frames\n')
            chk = run_job.parse_check(run_job.log_from(p, mark))
        self.assertEqual(chk, {'residue': [[3, 'cap', 1.0, 9.0, 12, 2.5, 640]], 'offText': 2})


class Follow(unittest.TestCase):
    """A line that comes up 90 px while it fades in, as an end-card tagline does."""
    def setUp(self):
        settled, self.rect = line()
        self.tpl = np.ascontiguousarray(settled[self.rect[1]:self.rect[3], self.rect[0]:self.rect[2]])
        self.clip = []                                  # 15 fps: 5 empty frames, 6 on the way, 10 settled
        for i in range(21):
            if i < 5: f = np.full((H, W), 24, np.uint8)
            else:
                k = min(6, i - 4); f, _ = line(y=640 + round(90 * (1 - k / 6) ** 2), level=60 + round(110 * k / 6))
            self.clip.append(f)
        self.real = az.load_gray
        az.load_gray = lambda src, fps, w, h, rows=None, span=None: [
            (span[0] + i / fps, self.clip[i][rows[0]:rows[1]]) for i in range(len(self.clip))]

    def tearDown(self):
        az.load_gray = self.real

    def test_the_path_starts_where_the_line_first_shows_and_ends_settled(self):
        path = az.follow('none', W, H, 10.0, 10.0 + 21 / 15, self.rect, self.tpl)
        self.assertIsNotNone(path)
        self.assertAlmostEqual(path[0][0], 10.0 + 5 / 15, places=2)       # not the empty frames before it
        self.assertEqual((path[0][1], path[-1][1], path[-1][2]), (0, 0, 0))
        self.assertTrue(55 <= path[0][2] <= 70, path[0])                  # first seen 62 px under its place
        dys = [p[2] for p in path]
        self.assertEqual(dys, sorted(dys, reverse=True))                  # it only comes closer

    def test_a_line_that_is_not_there_at_the_end_is_not_followed(self):
        self.clip[-4:] = [np.full((H, W), 24, np.uint8)] * 4
        self.assertIsNone(az.follow('none', W, H, 10.0, 10.0 + 21 / 15, self.rect, self.tpl))

    def test_a_flat_picture_is_not_followed(self):
        self.assertIsNone(az.follow('none', W, H, 10.0, 11.4, self.rect, np.full_like(self.tpl, 24)))



class StillBlock(unittest.TestCase):
    """main() on drawn frames: a light headline that stays in place gets one mask for its time, a moving one gets zones."""
    def run_main(self, ys, extra_reads=(), plate=None, late=False):
        import json, tempfile
        frames = []                                         # 4 fps, 4 s, two shots: a dark wall, then a mid-grey one
        for i in range(16):
            f, rect = line('Plain Sample Words', y=ys[i // 8], level=250, bg=40 if i < 8 else 110)
            if late and i >= 11:                            # a second line comes in at 2.75 s, under the first
                f2, _ = line('Below', y=ys[i // 8] + 80, level=250, bg=110); f[ys[i // 8] + 70:] = f2[ys[i // 8] + 70:]
            frames.append((i / 4, f))
        box = lambda r: {'xmin': r[0] * 1000 / W, 'ymin': r[1] * 1000 / H, 'xmax': r[2] * 1000 / W, 'ymax': r[3] * 1000 / H}
        r0 = line('Plain Sample Words', y=ys[0])[1]; r1 = line('Plain Sample Words', y=ys[1])[1]
        if late: r0, r1 = r0[:3] + [r0[3] + 90], r1[:3] + [r1[3] + 90]       # the reads show both lines
        a = {'overlays': [{'role': 'headline', 'start': 0, 'end': 4, 'text': 'Plain Sample Words', 'box': box(r0)}],
             'groups': [{'t': 1.0, 'role': 'headline', 'text': 'Plain Sample Words', 'box': box(r0), 'lines': 1, 'color': '#fafafa', 'surround': '#282828'},
                        {'t': 3.0, 'role': 'headline', 'text': 'Plain Sample Words', 'box': box(r1), 'lines': 1, 'color': '#fafafa', 'surround': '#6e6e6e'}]
                       + [dict(q, box=box(q['box'])) for q in extra_reads],
             'cuts': [{'from': 2.0, 'to': 2.0}], 'scenes': [{'start': 0, 'end': 2, 'type': 'people'}, {'start': 2, 'end': 4, 'type': 'people'}]}
        rb = {'width': W, 'height': H, 'duration': 4.0, 'frame_rate': 30, 'elements': []}
        real, argv = az.load_gray, sys.argv
        with tempfile.TemporaryDirectory() as d:
            for name, obj in (('a', a), ('rb', rb)):
                with open(f'{d}/{name}.json', 'w') as fh: json.dump(obj, fh)
            az.load_gray = lambda src, fps, w, h, rows=None, span=None: frames
            sys.argv = ['auto_zones.py', d + '/a.json', d + '/rb.json', 'none.mp4', d + '/cfg.json']
            try: az.main()
            finally: az.load_gray, sys.argv = real, argv
            with open(d + '/cfg.json') as fh: cfg = json.load(fh)
        return cfg

    def test_a_headline_that_stays_in_place_is_one_still_text(self):
        cfg = self.run_main((640, 640))
        self.assertEqual([s['role'] for s in cfg.get('still', [])], ['headline'])
        s = cfg['still'][0]
        self.assertEqual(s['times'], [[0, 4.0]]); self.assertEqual(s['min_left'], 0.4)
        self.assertEqual([z for z in cfg['auto']['zones'] if 'z' in z], [])            # and no zone per shot for it

    def test_a_kept_read_on_one_shot_keeps_the_zones_per_shot(self):
        # a sticker over the end of the line on the second shot only: one mask would lose those letters on both shots
        r = line('Plain Sample Words', y=640)[1]
        cfg = self.run_main((640, 640), extra_reads=[{'t': 3.0, 'role': 'sticker', 'text': 'x', 'lines': 1, 'box': [r[2] - 120, r[1] - 10, r[2] + 30, r[3] + 10]}])
        self.assertEqual(cfg.get('still', []), [])
        self.assertIn('kept differs between shots', [q['why'] for q in cfg['auto'].get('still_refused', [])])

    def test_a_line_that_comes_in_late_keeps_the_zones_per_shot(self):
        cfg = self.run_main((640, 640), late=True)
        self.assertEqual(cfg.get('still', []), [])

    def test_the_mask_check_on_its_own(self):
        f, r = line('Plain Sample Words', y=640, level=250, bg=40); r = r[:3] + [r[3] + 90]
        e = {'k': 31, 'thr': 40, 'lmin': 150, 'grow': 5, 'core0': 7.0}
        steady = [(i / 4, f) for i in range(12)]
        self.assertEqual(az.still_mask_ok(steady, 0, 3, r, e), (True, ''))
        g = f.copy(); g[710:] = line('Below', y=720, level=250, bg=40)[0][710:]        # a line under it for the last third
        ok, why = az.still_mask_ok(steady[:8] + [(i / 4, g) for i in range(8, 12)], 0, 3, r, e)
        self.assertFalse(ok, why)
        h = f.copy(); h[:, 540:] = 40                                                   # half of the words for the last third:
        self.assertTrue(az.still_mask_ok(steady[:8] + [(i / 4, h) for i in range(8, 12)], 0, 3, r, e)[0])   # the mask holds them
        self.assertEqual(az.still_mask_ok([(i / 4, np.full_like(f, 40)) for i in range(12)], 0, 3, r, e)[0], False)

    def test_a_headline_that_sits_elsewhere_on_the_second_shot_gets_zones(self):
        cfg = self.run_main((640, 900))
        self.assertEqual(cfg.get('still', []), [])
        self.assertEqual(len([z for z in cfg['auto']['zones'] if 'z' in z and z['role'] == 'headline']), 2)

if __name__ == '__main__':
    unittest.main()
