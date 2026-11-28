"""Tests for build_math.py's kept source layers («orig …») against the erase zones, on small render bodies, no media.

    python -m unittest discover -s tests"""
import json, os, subprocess, sys, tempfile, unittest

ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'engine')
sys.path.insert(0, ENGINE)
from layers import orig_rect, orig_span

SRC = 'https://example.invalid/src.mp4'
W, H, D = 1080, 1920, 10.0


def stops(lo, hi):
    return [{'offset': '0%', 'color': 'rgba(255,255,255,0)'}, {'offset': f'{lo - 0.6:.1f}%', 'color': 'rgba(255,255,255,0)'},
            {'offset': f'{lo}%', 'color': 'rgba(255,255,255,1)'}, {'offset': f'{hi}%', 'color': 'rgba(255,255,255,1)'},
            {'offset': '100%', 'color': 'rgba(255,255,255,0)'}, {'offset': '100%', 'color': 'rgba(255,255,255,0)'}]


def orig(name, t, d, y0, y1, x0=0, x1=100):
    """A kept layer the way the Build makes one (lib/auto-loc-build step 2b): source clip + row mask, then a column mask."""
    full = {'type': 'shape', 'track': 2, 'x': '50%', 'y': '50%', 'width': '100%', 'height': '100%', 'path': 'M 0 0 L 100 0 L 100 100 L 0 100 Z',
            'fill_mode': 'linear', 'mask_mode': 'alpha'}
    clip = {'type': 'video', 'track': 1, 'time': 0, 'source': SRC, 'trim_start': t, 'trim_duration': d, 'duration': d, 'volume': '0%', 'fit': 'cover'}
    return {'name': name, 'type': 'composition', 'track': 8, 'time': t, 'duration': d, 'elements': [
        {'type': 'composition', 'track': 1, 'time': 0, 'duration': d, 'elements': [clip, dict(full, fill_color=stops(y0, y1))]},
        dict(full, fill_x0='0%', fill_y0='50%', fill_x1='100%', fill_y1='50%', fill_color=stops(x0, x1))]}


def plate(t, d):
    """The Build's caption plate: blurred source, its mask, a translucent dark box on top."""
    return {'type': 'composition', 'track': 14, 'time': t, 'duration': d, 'elements': [
        {'type': 'video', 'source': SRC}, {'type': 'composition', 'elements': []},
        {'type': 'shape', 'x': '50%', 'y': '72.5%', 'width': '60%', 'height': '4.4%', 'fill_color': 'rgba(34,34,34,0.75)'}]}


def body(*els):
    return {'width': W, 'height': H, 'duration': D, 'frame_rate': 30,
            'elements': [{'type': 'audio', 'track': 1, 'time': 0, 'duration': D, 'name': 'dub'}] + list(els) +
                        [{'type': 'text', 'track': 15, 'time': 4.0, 'duration': 1.0, 'text': 'una línea'}]}


def build(rb, cfg):
    """Run build_math.py on (rb, cfg) -> (exit code, output, {black, zones} or None)."""
    with tempfile.TemporaryDirectory() as d:
        for name, v in (('rb', rb), ('cfg', cfg)):
            with open(f'{d}/{name}.json', 'w') as f: json.dump(v, f)
        r = subprocess.run([sys.executable, os.path.join(ENGINE, 'build_math.py'), f'{d}/rb.json', SRC, f'{d}/ov', f'{d}/cfg.json'],
                           capture_output=True, text=True)
        out = None
        if r.returncode == 0:
            with open(f'{d}/ov.black.json') as f, open(f'{d}/ov.zones.json') as g:
                out = {'black': json.load(f), 'zones': json.load(g)['zones']}
        return r.returncode, r.stdout + r.stderr, out


def kept(b):
    return [e for e in b['elements'] if (e.get('name') or '').startswith('orig ')]


def covers(e, z, frames=2):
    """Kept layer e shows source pixels over zone z on a frame the plate erases it (a full zone also 2 frames before / after)."""
    x0, y0, x1, y1 = [round(v * s / 100) for v, s in zip(orig_rect(e), (W, H, W, H))]
    t0, t1 = e['time'], e['time'] + e['duration']
    return (z[0] - frames / 30 < t1 and z[1] + frames / 30 > t0 and min(x1, z[4]) > max(x0, z[2]) and min(y1, z[5]) > max(y0, z[3]))


# The 2026-11-28 stop: a promo tape across the frame kept as the original (rows 72.8-99.4 %) for most of the video,
# and the old translucent caption plate measured on every frame (kind P): one-line pages end at 75.3 % (+ margin), the
# two-line page at 36.8 s runs to 79.6 %, into the tape's rows. Times here are shifted into a 10 s body.
TAPE = orig('orig sticker 2', 2.0, 6.0, 72.8, 99.4)
TWO_LINES = [4.0, 1.0, 8.43, 68.12, 84.44, 79.64, 'P']
ONE_LINE = [6.0, 0.8, 18.52, 68.12, 81.2, 75.31, 'P']
CFG = {'_note': 'auto zones', 'caption_rects': [TWO_LINES, ONE_LINE]}


class KeptLayersOverZones(unittest.TestCase):
    def test_a_zone_measured_on_this_build_cuts_the_kept_layer_out_of_its_time_and_the_job_goes_on(self):
        code, log, out = build(body(TAPE, plate(4.0, 1.0)), dict(CFG, _measured=['caption_rects']))
        self.assertEqual(code, 0, log)
        self.assertIn('left out over', log)
        Z = [z for z in out['zones'] if z[6] == 'exact']
        self.assertEqual(len(Z), 2)
        ks = kept(out['black'])
        self.assertTrue(ks, 'the tape stays where no zone is')
        for e in ks:
            for z in Z: self.assertFalse(covers(e, z), (e['time'], e['duration'], z[:7]))
        # the pieces left: before the two-line page, between the pages, after the one-line page (3 frames of margin)
        spans = [(round(e['time'], 2), round(e['time'] + e['duration'], 2)) for e in ks]
        self.assertEqual(spans, [(2.0, 3.9), (5.1, 5.9), (6.9, 8.0)])
        for e in ks:                                   # each piece shows the same source frames the whole layer showed then
            clip = e['elements'][0]['elements'][0]
            self.assertAlmostEqual(clip['trim_start'], e['time'], places=3)
            self.assertAlmostEqual(clip['trim_duration'], e['duration'], places=3)
            self.assertAlmostEqual(e['elements'][0]['duration'], e['duration'], places=3)
            self.assertEqual(orig_rect(e), orig_rect(TAPE))

    def test_a_small_overlap_is_cut_too(self):
        # a one-line plate's bottom reached 54 px into the tape's rows: under the old 15 % line, so its old plate edge
        # came back over the erase without a stop
        code, log, out = build(body(TAPE), {'caption_rects': [ONE_LINE], '_measured': ['caption_rects']})
        self.assertEqual(code, 0, log)
        for e in kept(out['black']): self.assertFalse(covers(e, out['zones'][0]))

    def test_kept_source_layers_lie_under_our_boxes(self):
        code, log, out = build(body(TAPE, plate(4.0, 1.0)), dict(CFG, _measured=['caption_rects']))
        self.assertEqual(code, 0, log)
        els = out['black']['elements']
        box = next(e['track'] for e in els if e.get('type') == 'composition' and not (e.get('name') or '').startswith('orig '))
        self.assertTrue(all(e['track'] < box for e in kept(out['black'])))
        self.assertTrue(all(e['track'] > box for e in els if e.get('type') == 'text'))

    def test_a_cfg_written_for_another_build_still_stops(self):
        # no «_measured»: a hand cfg (or one from an older caller) is a cfg of another build
        code, log, _ = build(body(TAPE, plate(4.0, 1.0)), CFG)
        self.assertEqual(code, 1)
        self.assertIn('the cfg does not fit this build: 1 kept source layer(s)', log)

    def test_the_2026_11_13_case_still_stops_next_to_measured_zones(self):
        # the next build kept the original price tag (an «orig price» layer) where the build the hand zone was measured on
        # had lifted it; the caller's zones_extra (not measured here) erases the old legal line across it
        tag = orig('orig price 1', 1.0, 4.0, 80.0, 86.0, 60.0, 90.0)
        cfg = dict(CFG, zones_extra=[[1.0, 4.0, 5.0, 81.0, 95.0, 84.0, 'light', 31, 70, 5]], _measured=['caption_rects'])
        code, log, _ = build(body(TAPE, tag), cfg)
        self.assertEqual(code, 1)
        self.assertIn('kept by orig price 1', log)
        self.assertIn('does not fit this build', log)
        # the same zone measured on this build is cut out of the tag instead
        code, log, out = build(body(TAPE, tag), dict(cfg, _measured=['caption_rects', 'zones_extra']))
        self.assertEqual(code, 0, log)
        for e in kept(out['black']):
            for z in out['zones']: self.assertFalse(covers(e, z))

    def test_orig_span_keeps_the_build_layout_only(self):
        e = orig_span(TAPE, 3.0, 5.5)
        self.assertEqual((e['time'], e['duration']), (3.0, 2.5))
        self.assertEqual(e['elements'][0]['elements'][0]['trim_start'], 3.0)
        late = json.loads(json.dumps(TAPE)); late['elements'][0]['time'] = 0.5
        self.assertIsNone(orig_span(late, 3.0, 5.5))
        fast = json.loads(json.dumps(TAPE)); fast['elements'][0]['elements'][0]['speed'] = '200%'
        self.assertIsNone(orig_span(fast, 3.0, 5.5))
        self.assertIsNone(orig_span(TAPE, 1.0, 3.0))


if __name__ == '__main__':
    unittest.main()
