"""Tests for engine/captions.py on drawn frames (no media files): python -m unittest discover -s tests

The text is drawn the way a renderer draws an outlined caption: a soft shadow of the text shape (blurred, moved down,
at a strength), then a dark outline, then a white fill."""
import os, sys, unittest
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont, ImageFilter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'engine'))
import captions

W, H = 1080, 240
SHADOW = (0.75, 11.0, 3)            # strength, sigma px, offset down px
FONT = ImageFont.load_default(size=74)


def ground(kind):
    rng = np.random.default_rng(7)
    if kind == 'white': g = np.full((H, W, 3), 253, np.uint8)
    elif kind == 'black': g = np.full((H, W, 3), 6, np.uint8)
    elif kind == 'tan': g = np.zeros((H, W, 3), np.uint8) + np.array([80, 110, 150], np.uint8)
    elif kind == 'split': g = np.full((H, W, 3), 235, np.uint8); g[:, : W // 2] = 30
    elif kind == 'texture': g = cv2.GaussianBlur(rng.integers(90, 170, (H, W, 3)).astype(np.uint8), (0, 0), 2)
    else: raise ValueError(kind)
    return g


def draw(bg, text, y=120, outline=4, shadow=SHADOW):
    """bg (BGR) with outlined text drawn on it -> (frame BGR, text bbox [x0, y0, x1, y1])."""
    shape = Image.new('L', (W, H), 0); d = ImageDraw.Draw(shape)
    d.text((W // 2, y), text, font=FONT, anchor='mm', fill=255, stroke_width=outline, stroke_fill=255)
    fill = Image.new('L', (W, H), 0); ImageDraw.Draw(fill).text((W // 2, y), text, font=FONT, anchor='mm', fill=255)
    f = bg.astype(np.float32)
    if shadow:
        A, sig, dy = shadow
        a = np.asarray(shape.filter(ImageFilter.GaussianBlur(sig)), np.float32) / 255 * A
        a = np.roll(a, dy, axis=0); f *= (1 - a)[..., None]
    s = np.asarray(shape, np.float32)[..., None] / 255; f = f * (1 - s)                     # the outline: black
    w = np.asarray(fill, np.float32)[..., None] / 255; f = f * (1 - w) + 255 * w            # the fill: white
    return np.clip(f, 0, 255).astype(np.uint8), list(fill.getbbox())


class Find(unittest.TestCase):
    def test_a_line_is_found_on_every_ground(self):
        for kind in ('white', 'black', 'tan', 'split', 'texture'):
            f, bb = draw(ground(kind), 'WHEN YOUR CLOTHES')
            r = captions.find(f, 0)
            self.assertEqual(len(r['lines']), 1, kind)
            x0, y0, x1, y1 = r['lines'][0]
            self.assertLessEqual(x0, bb[0], kind); self.assertGreaterEqual(x1, bb[2], kind)       # first and last letter inside
            self.assertGreater((r['mask'] > 0).sum(), 3000, kind)

    def test_plain_grounds_and_page_text_give_no_line(self):
        for kind in ('white', 'black', 'tan', 'split', 'texture'):
            self.assertEqual(captions.find(ground(kind), 0)['lines'], [], kind)
        page = Image.fromarray(ground('white')); d = ImageDraw.Draw(page)
        d.text((W // 2, 120), 'Evening 3 Items 184$ (8-10)', font=FONT, anchor='mm', fill=(20, 20, 20))    # dark page text: counters are ringed
        for x in (200, 300, 400, 500):                                                                    # radio buttons
            d.ellipse([x - 20, 20, x + 20, 60], outline=(0, 0, 0), width=6)
        self.assertEqual(captions.find(np.asarray(page), 47)['lines'], [])

    def test_punctuation_joins_the_line_but_a_dot_beside_it_does_not(self):
        f, bb = draw(ground('tan'), 'CHANGE IT BACK."')
        r = captions.find(f, 0)
        self.assertEqual(len(r['lines']), 1)
        self.assertGreaterEqual(r['lines'][0][2], bb[2])                    # the closing quote is inside the line
        img = Image.fromarray(f); d = ImageDraw.Draw(img)                   # a ringed dot at mid height just past the text
        cx = bb[2] + 30; d.ellipse([cx - 16, 104, cx + 16, 136], fill=(0, 0, 0)); d.ellipse([cx - 6, 114, cx + 6, 126], fill=(255, 255, 255))
        r2 = captions.find(np.asarray(img), 0)
        self.assertFalse(r2['mask'][114:126, cx - 5:cx + 5].any())


class Erase(unittest.TestCase):
    def test_no_line_is_left_and_a_white_page_comes_back_white(self):
        bg = ground('white'); f, bb = draw(bg, 'ALREADY PLANNED.')
        fit = captions.fit_shadow([f], 0 or captions.find(f, 0)['cap_h'])
        self.assertIsNotNone(fit['shadow'])
        out, n = captions.erase(f, captions.find(f, 0)['cap_h'], (), fit['shadow'], fit['core'], 0)
        self.assertEqual(n, 1); self.assertEqual(captions.find(out, 0)['lines'], [])
        self.assertLess(float(np.abs(out.astype(np.float32) - bg).mean()), 1.5)               # the shadow is gone, not blurred
        self.assertLess(float(np.percentile(np.abs(out.astype(np.float32) - bg), 99.5)), 40)

    def test_texture_next_to_the_letters_keeps_its_detail(self):
        bg = ground('texture'); f, bb = draw(bg, 'NO MORE RANDOM')
        cap_h = captions.find(f, 0)['cap_h']
        out, n = captions.erase(f, cap_h, (), [3.0, SHADOW[0], SHADOW[1], SHADOW[2]], 7.0, 0)
        self.assertEqual(captions.find(out, 0)['lines'], [])
        d = cv2.distanceTransform((captions.find(f, 0)['mask'] == 0).astype(np.uint8), cv2.DIST_L2, 5)
        ring = (d > 12) & (d < 24)                                                             # shadow only, no fill
        err = np.abs(out.astype(np.float32) - bg)[ring]
        self.assertLess(float(err.mean()), 6.0)                                                # the real pixels, shadow divided out

    def test_without_a_shadow_model_the_letters_are_filled_with_a_grown_mask(self):
        f, bb = draw(ground('tan'), 'SOMEONE ELSE.')
        out, n = captions.erase(f, captions.find(f, 0)['cap_h'], (), None, 7.0, 16)
        self.assertEqual(captions.find(out, 0)['lines'], [])

    def test_a_frame_without_a_caption_is_returned_as_it_is(self):
        g = ground('texture'); out, n = captions.erase(g, 47)
        self.assertEqual(n, 0); self.assertIs(out, g)


class Shadow(unittest.TestCase):
    def test_the_fit_finds_the_drawn_shadow_and_none_where_there_is_none(self):
        frames = [draw(ground('white'), t)[0] for t in ('EVERY OUTFIT IS', 'CAPSULE FROM', '10-PIECE FALL')]
        cap_h = captions.find(frames[0], 0)['cap_h']
        fit = captions.fit_shadow(frames, cap_h)
        self.assertIsNotNone(fit['shadow']); r, A, sig, dy = fit['shadow']
        self.assertLess(abs(A - SHADOW[0]), 0.2); self.assertLess(abs(sig - SHADOW[1]), 3.5)
        self.assertLessEqual(fit['err'], 3.0)
        bare = [draw(ground('white'), t, shadow=None)[0] for t in ('EVERY OUTFIT IS', 'CAPSULE FROM')]
        self.assertIsNone(captions.fit_shadow(bare, cap_h)['shadow'])


class Fill(unittest.TestCase):
    def test_a_hole_wider_than_the_pyramid_reaches_is_not_black(self):
        I = np.full((420, 900, 3), 120, np.float32); Wt = np.ones((420, 900), np.float32); Wt[60:360, 60:840] = 0
        F = captions.pushpull(I, Wt)
        self.assertGreater(float(F[60:360, 60:840].min()), 100)


if __name__ == '__main__':
    unittest.main()
