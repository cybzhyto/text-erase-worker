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
            f, bb = draw(ground(kind), 'SEVEN GREEN BOXES')
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
        f, bb = draw(ground('tan'), 'MOVE IT LEFT."')
        r = captions.find(f, 0)
        self.assertEqual(len(r['lines']), 1)
        self.assertGreaterEqual(r['lines'][0][2], bb[2])                    # the closing quote is inside the line
        img = Image.fromarray(f); d = ImageDraw.Draw(img)                   # a ringed dot at mid height just past the text
        cx = bb[2] + 30; d.ellipse([cx - 16, 104, cx + 16, 136], fill=(0, 0, 0)); d.ellipse([cx - 6, 114, cx + 6, 126], fill=(255, 255, 255))
        r2 = captions.find(np.asarray(img), 0)
        self.assertFalse(r2['mask'][114:126, cx - 5:cx + 5].any())


    def test_a_question_mark_that_ends_a_line_is_taken_whole(self):
        # a caption font whose «?» hook is shorter than its capitals (about 0.68 of them, its dot below): the hook is
        # neither a capital nor small punctuation, and it stayed on the plate next to the erased line
        for kind in ('tan', 'texture', 'white'):
            f, bb = draw(ground(kind), 'WHERE IS THE KEY')
            ch = captions.find(f, 0)['cap_h']; top = bb[1]
            hook = Image.new('L', (W, H), 0); dh = ImageDraw.Draw(hook)
            x = bb[2] + 10; hw = int(0.5 * ch); hh = int(0.66 * ch)
            dh.arc([x, top, x + hw, top + int(0.55 * ch)], 200, 90, fill=255, width=9)
            dh.line([x + hw // 2, top + int(0.5 * ch), x + hw // 2, top + hh], fill=255, width=9)
            dh.ellipse([x + hw // 2 - 6, top + ch - 12, x + hw // 2 + 6, top + ch], fill=255)
            sh = cv2.dilate(np.asarray(hook), np.ones((9, 9), np.uint8)) > 0
            g = f.copy(); g[sh] = (0, 0, 0); g[np.asarray(hook) > 0] = (255, 255, 255)
            r = captions.find(g, ch)
            isl, lab = captions.islands(g)
            hf = np.asarray(hook) > 128
            q = [i for i in isl if i[0] >= x - 2 and (hf[lab == i[6]]).mean() > 0.5]          # the drawn glyph's own islands
            self.assertGreaterEqual(len(q), 2, kind)                                            # its hook and its dot
            self.assertTrue(any(i[3] - i[1] > 0.6 * ch for i in q), kind)
            for i in q: self.assertTrue((r['mask'][lab == i[6]] > 0).all(), (kind, i))

    def test_a_last_capital_with_a_paler_ring_joins_the_line(self):
        f, bb = draw(ground('white'), 'BRING THE BLUE KITE')
        ch = captions.find(f, 0)['cap_h']
        isl, lab = captions.islands(f)
        last = max((q for q in isl if q[3] - q[1] > 0.72 * ch), key=lambda q: q[2])
        c = (lab == last[6]).astype(np.uint8)
        ring = (cv2.dilate(c, captions.K7) > 0) & (cv2.dilate(c, captions.K3) == 0)
        ys, xs = np.nonzero(ring); pale = xs > np.percentile(xs, 60)          # part of its ring is not dark
        g = f.copy(); g[ys[pale], xs[pale]] = (175, 175, 175)
        q = [x for x in captions.islands(g)[0] if x[6] == last[6]][0]
        self.assertTrue(captions.RING_IN <= q[5] < captions.RING, q)
        r = captions.find(g, ch)
        self.assertTrue((r['mask'][lab == last[6]] > 0).all())

    def test_the_counters_of_an_app_pages_own_words_are_not_the_captions(self):
        page = Image.fromarray(ground('white')); d = ImageDraw.Draw(page)
        small = ImageFont.load_default(size=34)
        d.text((W // 2 - 120, 164), 'Oak bed', font=small, anchor='mm', fill=(20, 20, 20))      # under the caption's base
        d.text((W // 2 + 60, 74), 'blue door', font=small, anchor='mm', fill=(20, 20, 20))   # over its top
        page_only = np.asarray(page).copy()
        f, bb = draw(page_only, 'PAINT A HOUSE')
        r = captions.find(f, 0)
        own = (page_only.max(2) > 200) & (cv2.dilate((page_only.max(2) < 120).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
        isl, lab = captions.islands(page_only)                          # the white counters of the page's letters
        cnt = [x for x in isl if x[5] >= captions.RING_IN and x[3] - x[1] < 0.4 * r['cap_h']]
        self.assertGreater(len(cnt), 3)
        for x in cnt: self.assertFalse((r['mask'][lab == x[6]] > 0).any(), x)

class Erase(unittest.TestCase):
    def test_no_line_is_left_and_a_white_page_comes_back_white(self):
        bg = ground('white'); f, bb = draw(bg, 'QUIETLY SORTED.')
        fit = captions.fit_shadow([f], 0 or captions.find(f, 0)['cap_h'])
        self.assertIsNotNone(fit['shadow'])
        out, n = captions.erase(f, captions.find(f, 0)['cap_h'], (), fit['shadow'], fit['core'], 0)
        self.assertEqual(n, 1); self.assertEqual(captions.find(out, 0)['lines'], [])
        self.assertLess(float(np.abs(out.astype(np.float32) - bg).mean()), 1.5)               # the shadow is gone, not blurred
        self.assertLess(float(np.percentile(np.abs(out.astype(np.float32) - bg), 99.5)), 40)

    def test_texture_next_to_the_letters_keeps_its_detail(self):
        bg = ground('texture'); f, bb = draw(bg, 'TWO SMALL ROUND')
        cap_h = captions.find(f, 0)['cap_h']
        out, n = captions.erase(f, cap_h, (), [3.0, SHADOW[0], SHADOW[1], SHADOW[2]], 7.0, 0)
        self.assertEqual(captions.find(out, 0)['lines'], [])
        d = cv2.distanceTransform((captions.find(f, 0)['mask'] == 0).astype(np.uint8), cv2.DIST_L2, 5)
        ring = (d > 12) & (d < 24)                                                             # shadow only, no fill
        err = np.abs(out.astype(np.float32) - bg)[ring]
        self.assertLess(float(err.mean()), 6.0)                                                # the real pixels, shadow divided out

    def test_without_a_shadow_model_the_letters_are_filled_with_a_grown_mask(self):
        f, bb = draw(ground('tan'), 'ANOTHER DAY.')
        out, n = captions.erase(f, captions.find(f, 0)['cap_h'], (), None, 7.0, 16)
        self.assertEqual(captions.find(out, 0)['lines'], [])

    def test_the_edge_of_a_filled_line_fades_into_a_knit_ground_and_nothing_further_is_blurred(self):
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)                    # a knit-like ground: dense stitches, strong contrast
        knit = 128 + 45 * np.sin(xx * 1.1) * np.sin(yy * 0.9) + 20 * np.sin(xx * 0.23 + yy * 0.31)
        bg = np.clip(np.dstack([knit * 0.8, knit * 0.9, knit]), 0, 255).astype(np.uint8)
        f, bb = draw(bg, 'BRING A WARM HAT', shadow=None)
        r = captions.find(f, 0); d = cv2.distanceTransform((r['mask'] == 0).astype(np.uint8), cv2.DIST_L2, 5)
        def sd(im):
            L = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32); mu = cv2.blur(L, (7, 7))
            return np.sqrt(np.maximum(cv2.blur(L * L, (7, 7)) - mu * mu, 0))
        R = captions.FEATHER[0]
        soft = captions.erase_mask(f, r['mask'], None, 7.0, 0, feather=captions.FEATHER)
        hard = captions.erase_mask(f, r['mask'], None, 7.0, 0)
        edge, far = (d >= 9) & (d <= 12), (d > 7 + R + 6) & (d < 7 + R + 30)
        self.assertGreater(float(sd(hard)[edge].mean()), 0.8 * float(sd(hard)[far].mean()))   # a sharp edge: full texture at once
        self.assertLess(float(sd(soft)[edge].mean()), 0.6 * float(sd(soft)[far].mean()))     # feathered: the texture fades in
        out_of_reach = d > 7 + R + 1
        self.assertTrue(np.array_equal(soft[out_of_reach], hard[out_of_reach]))               # nothing past R px is touched

    def test_a_frame_without_a_caption_is_returned_as_it_is(self):
        g = ground('texture'); out, n = captions.erase(g, 47)
        self.assertEqual(n, 0); self.assertIs(out, g)


    def test_an_app_pages_words_next_to_the_letters_stay_dark(self):
        page = Image.fromarray(ground('white')); d = ImageDraw.Draw(page)
        small = ImageFont.load_default(size=30)
        d.text((W // 2, 80), 'Pick a colour for the tall shelf', font=small, anchor='mm', fill=(15, 15, 15))
        page_only = np.asarray(page).copy()
        f, bb = draw(page_only, 'PAINT A HOUSE')
        ch = captions.find(f, 0)['cap_h']
        out, n = captions.erase(f, ch, (), [3.0, SHADOW[0], SHADOW[1], SHADOW[2]], 7.0, 0)
        dd = cv2.distanceTransform((captions.find(f, 0)['mask'] == 0).astype(np.uint8), cv2.DIST_L2, 5)
        words = (page_only.max(2) < 100) & (dd > 7.0 + 3) & (dd <= 7.0 + 9)   # the page's letters just off the outline
        self.assertGreater(int(words.sum()), 100)
        kept = (out.max(2) < 140)[words].mean()
        self.assertGreater(float(kept), 0.9)

class Fade(unittest.TestCase):
    def test_a_patch_fades_out_after_its_line_and_in_before_the_next_and_never_past_a_cut(self):
        A = np.zeros((4, 10), bool); A[:, :8] = True                # a long line
        B = np.zeros((4, 10), bool); B[:, :3] = True                # the short line after it
        o, i = captions.FADE
        h = captions.fade_strength(B, past=[B] * 4 + [A] * 30)       # 5 frames after the long line left
        self.assertTrue(np.all(h[B] == 1))
        self.assertAlmostEqual(float(h[0, 5]), 1 - 5 / (o + 1), places=5)
        self.assertTrue(np.all(captions.fade_strength(B, past=[B] * o + [A] * 30)[~B] == 0))   # faded out
        self.assertTrue(np.all(captions.fade_strength(B, past=[B] * 2)[~B] == 0))              # the caller stopped at a cut
        h = captions.fade_strength(B, future=[None] * 3 + [A] * 10)  # the long line shows in 4 frames
        self.assertAlmostEqual(float(h[0, 5]), 1 - 4 / (i + 1), places=5)
        near = captions.fade_strength(B, future=[A]); far_ = captions.fade_strength(B, future=[None] * (i - 1) + [A])
        self.assertGreater(float(near[0, 5]), float(far_[0, 5])); self.assertGreater(float(far_[0, 5]), 0)

    def test_the_patch_of_a_line_that_left_is_held_and_fades_and_ground_far_away_is_untouched(self):
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)               # knit-like ground, as in the feather test
        knit = 128 + 45 * np.sin(xx * 1.1) * np.sin(yy * 0.9) + 20 * np.sin(xx * 0.23 + yy * 0.31)
        bg = np.clip(np.dstack([knit * 0.8, knit * 0.9, knit]), 0, 255).astype(np.uint8)
        long_, _ = draw(bg, 'BRING A WARM HAT', shadow=None); short, _ = draw(bg, 'HAT', shadow=None)
        mA = captions.find(long_, 0)['mask']; mB = captions.find(short, 0)['mask']
        EA = captions.erase_mask(long_, mA, None, 7.0, 0, hole_only=True)
        EB = captions.erase_mask(short, mB, None, 7.0, 0, hole_only=True)
        def sd(im):
            L = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32); mu = cv2.blur(L, (7, 7))
            return np.sqrt(np.maximum(cv2.blur(L * L, (7, 7)) - mu * mu, 0))
        plain = captions.erase_mask(short, mB, None, 7.0, 0, feather=captions.FEATHER)
        same, n = captions.fade_erase(short, mB, EB, captions.fade_strength(EB), None, 7.0, 0)
        self.assertTrue(n); self.assertTrue(np.array_equal(same, plain))           # no line around: as filled on its own
        only = EA & (cv2.distanceTransform((~EB).astype(np.uint8), cv2.DIST_L2, 5) > 60)   # under the long line only
        self.assertGreater(int(only.sum()), 2000)
        held, _ = captions.fade_erase(short, mB, EB, captions.fade_strength(EB, past=[EA]), None, 7.0, 0)
        late, _ = captions.fade_erase(short, mB, EB, captions.fade_strength(EB, past=[None] * 18 + [EA]), None, 7.0, 0)
        ground_sd = float(sd(short)[only].mean())
        self.assertLess(float(sd(held)[only].mean()), 0.5 * ground_sd)            # 1 frame after: still a smooth patch
        self.assertGreater(float(sd(late)[only].mean()), 0.8 * ground_sd)         # 19 frames after: nearly the ground
        dU = cv2.distanceTransform((~(EA | EB)).astype(np.uint8), cv2.DIST_L2, 5)
        far = dU > 7 + captions.FEATHER[0] + 60
        self.assertTrue(np.array_equal(held[far], short[far]))                    # far from both lines: bit-exact


class Shadow(unittest.TestCase):
    def test_the_fit_finds_the_drawn_shadow_and_none_where_there_is_none(self):
        frames = [draw(ground('white'), t)[0] for t in ('EACH LITTLE BOX', 'MADE FOR NOW', '12-PART TEST')]
        cap_h = captions.find(frames[0], 0)['cap_h']
        fit = captions.fit_shadow(frames, cap_h)
        self.assertIsNotNone(fit['shadow']); r, A, sig, dy = fit['shadow']
        self.assertLess(abs(A - SHADOW[0]), 0.2); self.assertLess(abs(sig - SHADOW[1]), 3.5)
        self.assertLessEqual(fit['err'], 3.0)
        bare = [draw(ground('white'), t, shadow=None)[0] for t in ('EACH LITTLE BOX', 'MADE FOR NOW')]
        self.assertIsNone(captions.fit_shadow(bare, cap_h)['shadow'])


class LeftGlyphs(unittest.TestCase):
    def test_a_glyph_left_whole_next_to_an_erased_line_is_found_and_page_things_are_not(self):
        f, bb = draw(ground('tan'), 'WHERE IS THE KEY?')
        ch = captions.find(f, 0)['cap_h']
        q = draw(ground('tan'), 'WHERE IS THE KEY?', shadow=None)[0]
        left = ground('tan').copy(); x0 = bb[2] - int(0.6 * ch); left[:, x0:] = q[:, x0:]       # only the «?» stays
        self.assertTrue(captions.left_glyphs(f, left, ch))
        part = left.copy(); part[:, x0:x0 + int(0.35 * ch)] = ground('tan')[:, x0:x0 + int(0.35 * ch)]   # half its ring gone
        self.assertTrue(captions.left_glyphs(f, part, ch))
        self.assertEqual(captions.left_glyphs(f, ground('tan'), ch), [])                       # a clean plate
        page = Image.fromarray(ground('tan')); dr = ImageDraw.Draw(page)                        # a radio button 2 capitals past it
        cx = bb[2] + 2 * ch; dr.ellipse([cx - 22, 98, cx + 22, 142], fill=(0, 0, 0)); dr.ellipse([cx - 14, 106, cx + 14, 134], fill=(255, 255, 255))
        self.assertEqual(captions.left_glyphs(f, np.asarray(page), ch), [])
        self.assertEqual(captions.left_glyphs(ground('tan'), left, ch), [])                    # no caption in the source
        gap = ground('white'); gap[52:126, 300:400] = 40; gap[62:116, 318:382] = 253          # a white gap ringed by a dark photo, off the line
        fw, _ = draw(ground('white'), 'WHERE IS THE KEY?')
        self.assertEqual(captions.left_glyphs(fw, gap, ch), [])

class Fill(unittest.TestCase):
    def test_a_hole_wider_than_the_pyramid_reaches_is_not_black(self):
        I = np.full((420, 900, 3), 120, np.float32); Wt = np.ones((420, 900), np.float32); Wt[60:360, 60:840] = 0
        F = captions.pushpull(I, Wt)
        self.assertGreater(float(F[60:360, 60:840].min()), 100)



# ---- engine/auto_zones.py: the measures round the caption detector ----
import auto_zones


def boxed_word(x, text='OVER', c0=(48, 136, 232), c1=(155, 104, 230)):
    """A tan frame with a white line of text whose one word sits on a coloured box at x (orange to pink unless told)."""
    img = Image.fromarray(ground('tan')); d = ImageDraw.Draw(img)
    w = int(d.textlength(text, font=FONT)) + 40
    grad = np.linspace(0, 1, w)[None, :, None] * (np.array(c1) - np.array(c0)) + np.array(c0)
    a = np.asarray(img).copy(); a[70:170, x:x + w] = np.clip(grad, 0, 255).astype(np.uint8)
    img = Image.fromarray(a); d = ImageDraw.Draw(img)
    d.text((x + 20, 120), text, font=FONT, anchor='lm', fill=(255, 255, 255))
    return np.asarray(img)


class Measures(unittest.TestCase):
    def test_a_coloured_box_behind_the_spoken_word_is_seen_and_plain_captions_are_not(self):
        boxed = [boxed_word(x) for x in (80, 240, 400, 560, 720)]
        share, spots = auto_zones.highlight_boxes(boxed, 65)
        self.assertGreaterEqual(share, 0.8); self.assertGreaterEqual(spots, 3)
        plain = [draw(ground(k), 'SEVEN GREEN BOXES')[0] for k in ('tan', 'white', 'black', 'texture', 'split')]
        self.assertEqual(auto_zones.highlight_boxes(plain, 65), (0.0, 0))

    def test_a_dark_coloured_box_is_seen_too(self):
        for c in ((60, 30, 150), (150, 50, 120), (40, 140, 60)):          # dark blue, purple, green (as drawn: b, g, r)
            share, spots = auto_zones.highlight_boxes([boxed_word(x, c0=c, c1=c) for x in (80, 240, 400, 560, 720)], 65)
            self.assertGreaterEqual(share, 0.8, c); self.assertGreaterEqual(spots, 3, c)

    def test_a_knit_top_in_the_caption_rows_is_bright_but_is_not_text(self):
        knit = ground('tan')
        for y in range(40, 200, 14): knit[y:y + 6, 300:780] = 250           # thin bright ribs, as wide as a garment
        self.assertTrue(auto_zones.bright_letters(knit))
        self.assertFalse(auto_zones.bright_text(knit, 54))
        bare = draw(ground('black'), 'ANOTHER DAY.', shadow=None, outline=0)[0]
        self.assertTrue(auto_zones.bright_text(bare, captions.find(draw(ground('black'), 'ANOTHER DAY.')[0], 0)['cap_h']))

    def test_the_spoken_word_in_another_colour_is_a_fill_colour(self):
        def two_tone(kind, left, right):
            f, _ = draw(ground(kind), left + ' ' + right)
            d = ImageDraw.Draw(Image.new('L', (W, H), 0)); x = W // 2 - d.textlength(left + ' ' + right, font=FONT) / 2 + d.textlength(left + ' ', font=FONT)
            m = Image.new('L', (W, H), 0); ImageDraw.Draw(m).text((x, 120), right, font=FONT, anchor='lm', fill=255)
            f[np.asarray(m) > 128] = (0, 220, 255)                           # yellow, b g r
            return f
        frames = [two_tone(k, 'TAKE THE', 'RED') for k in ('tan', 'texture', 'black', 'split')]
        ch = captions.find(draw(ground('tan'), 'TAKE THE RED')[0], 0)['cap_h']
        cols = auto_zones.accent_colors(frames, ch)
        self.assertEqual(len(cols), 1)
        self.assertLessEqual(max(abs(a - b) for a, b in zip(cols[0], (0, 220, 255))), 40)
        r = captions.find(frames[0], ch, cols)                               # with it, the whole line is letters
        w = captions.find(frames[0], ch)
        self.assertGreater(int((r['mask'] > 0).sum()), 1.2 * int((w['mask'] > 0).sum()))
        self.assertEqual(auto_zones.accent_colors([draw(ground(k), 'TAKE THE RED')[0] for k in ('tan', 'texture', 'black', 'split')], ch), [])

    def test_bright_letters_are_seen_on_dark_ground_and_not_on_a_bare_frame(self):
        self.assertTrue(auto_zones.bright_letters(draw(ground('black'), 'ANOTHER DAY.', shadow=None)[0]))
        self.assertTrue(auto_zones.bright_letters(draw(ground('tan'), 'ANOTHER DAY.')[0]))
        for kind in ('white', 'black', 'tan', 'texture'):
            self.assertFalse(auto_zones.bright_letters(ground(kind)), kind)

    def test_the_share_of_a_block_under_an_erase(self):
        self.assertEqual(auto_zones.covered_share(0, 10, [(0, 4), (3, 6), (9, 12)]), 0.7)
        self.assertEqual(auto_zones.covered_share(5, 5, []), 1.0)
        self.assertEqual(auto_zones.covered_share(0, 10, []), 0.0)


if __name__ == '__main__':
    unittest.main()
