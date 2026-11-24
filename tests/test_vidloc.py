"""Tests for engine/vidloc.py's measuring on frames drawn with OpenCV's own fonts (no media files, no font downloads).

    python -m unittest discover -s tests"""
import os, sys, unittest

import numpy as np, cv2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'engine'))
import vidloc as V

H, W = 400, 360


def frames(n=12, angle=0.0, leading=34, dark=True, seed=1):
    """n frames: a cream ground (dark words) or a brown one (light words), a moving blob (the picture), two still lines."""
    rng = np.random.default_rng(seed); out = []
    for i in range(n):
        f = np.full((H, W, 3), (225, 240, 250) if dark else (70, 50, 40), np.uint8)
        cv2.circle(f, (40 + 20 * i, 330), 30, (60, 90, 140), -1)                        # moves: never a letter
        t = np.zeros((H, W), np.uint8)
        cv2.putText(t, 'Slow fall', (60, 120), cv2.FONT_HERSHEY_DUPLEX, 1.4, 255, 3, cv2.LINE_AA)
        cv2.putText(t, 'gentle days', (60, 120 + leading), cv2.FONT_HERSHEY_DUPLEX, 1.4, 255, 3, cv2.LINE_AA)
        if angle:
            t = cv2.warpAffine(t, cv2.getRotationMatrix2D((160, 110), angle, 1.0), (W, H))
        a = (t.astype(np.float32) / 255)[..., None]
        ink = np.array((20, 20, 30) if dark else (250, 250, 250), np.float32)
        f = (f * (1 - a) + ink * a).astype(np.uint8)
        f = np.clip(f.astype(int) + rng.integers(-3, 4, f.shape), 0, 255).astype(np.uint8)
        out.append(f)
    return np.stack(out)


class Measure(unittest.TestCase):
    def test_letters_two_lines_and_no_blob(self):
        F = frames(); med = np.median(F, axis=0).astype(np.uint8)
        M, Mm, mode, k, rect = V.letters(F, med, [50, 75, 330, 170], 2, 'dark')
        self.assertEqual(mode, 'dark')
        self.assertFalse(M[290:370].any(), 'the moving blob is not a letter')
        L = V.rows_of(Mm, 2)
        self.assertEqual(len(L), 2)
        self.assertLess(L[0][3], L[1][1] + 6)

    def test_tight_leading_still_two_lines(self):
        # descenders of line 1 reach line 2: no empty row between them, the profile's lowest row cuts them
        F = frames(leading=26); med = np.median(F, axis=0).astype(np.uint8)
        _, Mm, _, _, _ = V.letters(F, med, [50, 75, 330, 160], 2, 'dark')
        self.assertEqual(len(V.rows_of(Mm, 2)), 2)

    def test_side_from_border(self):
        F = frames(dark=False); med = np.median(F, axis=0).astype(np.uint8)
        self.assertEqual(V.letters(F, med, [50, 75, 330, 170], 2)[2], 'light')
        F = frames(dark=True); med = np.median(F, axis=0).astype(np.uint8)
        self.assertEqual(V.letters(F, med, [50, 75, 330, 170], 2)[2], 'dark')

    def test_skew(self):
        for ang in (0.0, 12.0, -9.0):
            F = frames(n=4, angle=ang); med = np.median(F, axis=0).astype(np.uint8)
            _, Mm, _, _, rect = V.letters(F, med, [30, 40, 340, 200], 2, 'dark')
            got = V.skew(Mm, rect)
            if ang == 0: self.assertEqual(got, 0.0)
            else: self.assertLess(abs(got + ang), 1.5, f'turned {ang}, measured {got}')

    def test_room_button_vs_picture(self):
        med = np.full((100, 300, 3), 200, np.uint8)
        cv2.rectangle(med, (60, 30), (240, 70), (40, 40, 220), -1)                      # a red button
        M = np.zeros((100, 300), np.uint8); M[42:58, 110:190] = 255
        self.assertEqual(V.room(med, M, (110, 42, 190, 58), 100, 300), (60, 240))
        noisy = np.random.default_rng(2).integers(0, 255, (100, 300, 3)).astype(np.uint8)    # a busy photo: no container
        self.assertEqual(V.room(noisy, M, (110, 42, 190, 58), 100, 300), (0, 299))


if __name__ == '__main__':
    unittest.main()


class Squeeze(unittest.TestCase):
    def test_squeezed_line_keeps_its_centre_and_height(self):
        from PIL import Image, ImageFont
        f = ImageFont.load_default(size=40) if hasattr(ImageFont, 'load_default') else None
        if f is None or not hasattr(f, 'getlength'): self.skipTest('no scalable default font')
        def ink(xs, anchor='mm'):
            im = Image.new('RGBA', (600, 120), (0, 0, 0, 0))
            V.draw_squeezed(im, (300, 60), 'SLOW FALL DAYS', f, (255, 255, 255, 255), anchor, xs)
            a = np.asarray(im)[..., 3]; ys, xs_ = np.nonzero(a > 127)
            return xs_.min(), xs_.max(), ys.max() - ys.min()
        x0, x1, h = ink(1.0); s0, s1, sh = ink(0.65)
        self.assertAlmostEqual((s1 - s0) / (x1 - x0), 0.65, delta=0.04)
        self.assertAlmostEqual((s0 + s1) / 2, (x0 + x1) / 2, delta=3)      # 'mm' stays centred
        self.assertLessEqual(abs(sh - h), 2)                                # the height is kept
        l0, _, _ = ink(0.65, 'lm'); self.assertAlmostEqual(l0, 300, delta=4)  # 'lm' starts at x
