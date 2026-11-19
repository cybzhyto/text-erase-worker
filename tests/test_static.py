"""Tests for engine/staticfix.py on drawn images (no media files, no model calls).

    python -m unittest discover -s tests"""
import os, sys, unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'engine'))
import staticfix as sf

W, H = 540, 960


def ground(seed=1):
    """A textured picture: smoothed noise + gradients + a few shapes, so alignment has something to lock on."""
    rng = np.random.default_rng(seed)
    n = cv2.GaussianBlur(rng.integers(0, 255, (H, W, 3)).astype(np.float32), (0, 0), 6)
    n = (n - n.min()) / (n.max() - n.min()) * 160 + np.linspace(0, 60, W)[None, :, None]
    img = n.clip(0, 255).astype(np.uint8)
    for k in range(12):
        cv2.circle(img, (int(rng.integers(0, W)), int(rng.integers(0, H))), int(rng.integers(8, 40)),
                   tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    return img


BTN = [150, 800, 390, 860]       # px, a red pill with a label


def with_button(img):
    out = img.copy()
    x0, y0, x1, y1 = BTN
    cv2.rectangle(out, (x0 + 30, y0), (x1 - 30, y1), (220, 30, 30), -1)
    cv2.circle(out, (x0 + 30, (y0 + y1) // 2), 30, (220, 30, 30), -1)
    cv2.circle(out, (x1 - 30, (y0 + y1) // 2), 30, (220, 30, 30), -1)
    cv2.putText(out, 'TRY NOW', (x0 + 50, y1 - 18), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3)
    return out


class Boxes(unittest.TestCase):
    def test_to_px(self):
        self.assertEqual(sf.to_px([100, 500, 300, 600], 1000, 2000), [100, 1000, 300, 1200])
        self.assertEqual(sf.to_px([300, 600, 100, 500], 1000, 2000), [100, 1000, 300, 1200])   # swapped corners
        self.assertEqual(sf.to_px([-5, 0, 1200, 1000], 100, 100), [0, 0, 100, 100])            # clamped

    def test_crop_for(self):
        crop, ar = sf.crop_for([BTN], W, H)
        x0, y0, x1, y1 = crop
        self.assertIn(ar, sf.RATIOS)
        self.assertTrue(0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H)
        self.assertTrue(x0 <= BTN[0] and y0 <= BTN[1] and x1 >= BTN[2] and y1 >= BTN[3])
        self.assertAlmostEqual((x1 - x0) / (y1 - y0), sf.RATIOS[ar], delta=0.03)


class Paste(unittest.TestCase):
    def test_only_the_padded_box_changes(self):
        clean = ground()
        orig = with_button(clean)
        crop, _ = sf.crop_for([BTN], W, H)
        x0, y0, x1, y1 = crop
        # the "model": the crop without the button, drawn at 2x, moved 2 px and a little noisier
        e = clean[y0:y1, x0:x1]
        e = cv2.resize(e, ((x1 - x0) * 2, (y1 - y0) * 2), interpolation=cv2.INTER_CUBIC)
        e = cv2.warpAffine(e, np.float32([[1, 0, 4], [0, 1, -3]]), (e.shape[1], e.shape[0]), borderMode=cv2.BORDER_REFLECT)
        e = (e.astype(np.int16) + np.random.default_rng(2).integers(-3, 4, e.shape)).clip(0, 255).astype(np.uint8)
        out, m = sf.paste(orig, e, crop, [{'box': BTN, 'pad': sf.PAD_CTA}])
        self.assertTrue(m['aligned'], m)
        p = sf.PAD_CTA
        mask = np.zeros((H, W), bool)
        mask[BTN[1] - p:BTN[3] + p, BTN[0] - p:BTN[2] + p] = True
        self.assertTrue((out[~mask] == orig[~mask]).all(), 'a pixel outside the padded box changed')
        core = (slice(BTN[1] + 5, BTN[3] - 5), slice(BTN[0] + 40, BTN[2] - 40))
        self.assertLess(np.abs(out[core].astype(int) - clean[core].astype(int)).mean(), 12, 'the button is still there')
        self.assertGreater(np.abs(orig[core].astype(int) - clean[core].astype(int)).mean(), 60)

    def test_unrelated_edit_does_not_align(self):
        orig = with_button(ground(1))
        crop, _ = sf.crop_for([BTN], W, H)
        other = ground(7)[crop[1]:crop[3], crop[0]:crop[2]]
        _, m = sf.paste(orig, other, crop, [{'box': BTN, 'pad': sf.PAD_CTA}])
        self.assertFalse(m['aligned'], m)


WANT = {'cta': 'SEE MORE', 'prices': 'keep'}


def rd(text='SEE MORE', shape='none', arrow=False, underline=False, tags=0, lang='en'):
    return {'cta': None if text is None else {'text': text, 'box': [280, 830, 720, 890], 'shape': shape, 'arrow': arrow,
                                              'underline': underline, 'colour': 'white'},
            'price_tags': [{'text': '$1', 'box': [100 + 50 * k, 300, 140 + 50 * k, 330]} for k in range(tags)], 'language': lang}


class Plan(unittest.TestCase):
    def test_clean_needs_nothing(self):
        self.assertEqual(sf.plan(rd(), WANT, W, H), [])
        self.assertEqual(sf.plan(rd('See more'), WANT, W, H), [])           # case and punctuation do not matter
        self.assertEqual(sf.plan(rd(None), WANT, W, H), [])                  # no call to action: nothing is added

    def test_button_words_arrow(self):
        for r, why in ((rd(shape='pill'), 'shape pill'), (rd('Try now'), 'words'), (rd(arrow=True), 'arrow'),
                       (rd(underline=True), 'underline')):
            f = sf.plan(r, WANT, W, H)
            self.assertEqual([x['kind'] for x in f], ['cta'])
            self.assertIn(why, f[0]['why'])
            self.assertEqual(f[0]['new'], 'SEE MORE')
            self.assertEqual(f[0]['box'], sf.to_px([280, 830, 720, 890], W, H))

    def test_language(self):
        want = {'ctaByLang': {'en': 'SEE MORE', 'es': 'VER MAS'}, 'prices': 'keep'}
        self.assertEqual(sf.plan(rd('VER MAS', lang='es'), want, W, H), [])
        self.assertEqual(sf.plan(rd('Ver todo', lang='es'), want, W, H)[0]['new'], 'VER MAS')
        self.assertEqual(sf.plan(rd('SEE MORE', lang='other'), want, W, H), [])   # unknown language: en

    def test_prices(self):
        self.assertEqual(sf.plan(rd(tags=3), WANT, W, H), [])                # keep: labels stay
        f = sf.plan(rd(tags=3), {**WANT, 'prices': 'remove'}, W, H)
        self.assertEqual([x['kind'] for x in f], ['tags'])
        self.assertEqual(f[0]['n'], 3)
        self.assertEqual(f[0]['pad'], sf.PAD_TAG)


class Qa(unittest.TestCase):
    def test_qa(self):
        fixes = sf.plan(rd('BUY TODAY', shape='pill', arrow=True, tags=2), {**WANT, 'prices': 'remove'}, W, H)
        self.assertEqual(sf.qa(rd(), WANT, fixes), (True, []))
        self.assertFalse(sf.qa(rd(shape='rounded_rect'), WANT, fixes)[0])
        self.assertFalse(sf.qa(rd(arrow=True), WANT, fixes)[0])
        self.assertFalse(sf.qa(rd('SEE MORE NOW'), WANT, fixes)[0])
        self.assertFalse(sf.qa(rd(None), WANT, fixes)[0])                    # the call to action vanished
        self.assertFalse(sf.qa(rd(tags=1), WANT, fixes)[0])                  # a label survived


if __name__ == '__main__':
    unittest.main()
