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


if __name__ == '__main__':
    unittest.main()
