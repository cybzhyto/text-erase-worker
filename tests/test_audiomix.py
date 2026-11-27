"""engine/audiomix.py on drawn tones (no media files, no network): what each key of a Creatomate audio body does.

The engine itself was checked against Creatomate's own render of one real body (the fitted dub of a 77 s ad: 3 stereo stems at
55 % + 36 mono lines with trims and fades, 2026-11-27): same length, lag 0 samples, RMS -21.00 vs -21.01 dB, 100 ms envelope
correlation 1.0000; 31 of 34 lines within -55 dB of Creatomate's, 2 one sample (21 µs) apart. These tests pin the rules that
match came from, so a later change cannot drift away from it unnoticed."""
import json, os, subprocess, sys, tempfile, unittest, wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'engine'))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'worker'))
import audiomix  # noqa: E402

SR = 48000


def tone(path, secs, hz, ch=1, amp=0.5, rate=44100, lead=0.0):
    """A sine (after `lead` s of silence) as an mp3 (mono) or wav (stereo), like the recipe's TTS lines and stems."""
    n, k = int(secs * rate), int(lead * rate)
    t = np.arange(n) / rate
    x = amp * np.sin(2 * np.pi * hz * t)
    x[:k] = 0
    pcm = np.repeat(x[:, None], ch, 1)
    raw = path + '.f32'
    pcm.astype('<f4').tofile(raw)
    args = ['-c:a', 'libmp3lame', '-b:a', '192k'] if path.endswith('.mp3') else ['-c:a', 'pcm_s16le']
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(rate), '-ac', str(ch), '-i', raw,
                    *args, path], check=True)
    os.remove(raw)
    return path


def read(path):
    with wave.open(path) as w:
        assert w.getnchannels() == 2 and w.getframerate() == SR and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), '<i2').reshape(-1, 2) / 32767.0


def rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def amp_at(x, hz):
    """Amplitude of one sine in a stretch (projection on sin / cos)."""
    t = np.arange(len(x)) / SR
    return 2 * float(np.hypot(np.mean(x * np.sin(2 * np.pi * hz * t)), np.mean(x * np.cos(2 * np.pi * hz * t))))


class AudioMix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        d = cls.tmp
        cls.line = tone(f'{d}/line.mp3', 1.0, 1000, ch=1, amp=0.5, lead=0.2)    # 0.2 s silence, then a 1 kHz mono tone
        cls.bed = tone(f'{d}/bed.wav', 2.0, 440, ch=2, amp=0.4)                 # a 2 s stereo 440 Hz bed
        cls.files = {'https://v3b.fal.media/line.mp3': cls.line, 'https://v3b.fal.media/bed.wav': cls.bed}

    def run_mix(self, body):
        out = os.path.join(self.tmp, 'out.wav')
        r = audiomix.mix(body, self.files, out, self.tmp)
        return r, read(out)

    def body(self, **line):
        el = {'name': 'line 0', 'type': 'audio', 'track': 5, 'time': 0.5, 'duration': 0.6,
              'source': 'https://v3b.fal.media/line.mp3', 'trim_start': 0.2, 'volume': '100%'}
        el.update(line)
        return {'output_format': 'mp4', 'width': 64, 'height': 64, 'frame_rate': 1, 'duration': 3,
                'elements': [{'name': 'bg 0', 'type': 'audio', 'track': 1, 'time': 0, 'duration': 3,
                              'source': 'https://v3b.fal.media/bed.wav', 'volume': '50%'}, el]}

    def test_timeline(self):
        r, x = self.run_mix(self.body())
        self.assertEqual(len(x), 3 * SR)                                    # the body's duration, to the sample
        self.assertEqual((r['seconds'], r['elements'], r['sources']), (3.0, 2, 2))
        m = x.mean(1)
        # the bed: 40 % at volume 50 % = 0.2, stereo as it is; it ends with its 2 s file, then silence to 3 s
        self.assertAlmostEqual(amp_at(m[int(1.5 * SR):int(1.9 * SR)], 440), 0.2, delta=0.01)
        self.assertLess(rms(x[int(2.05 * SR):]), 1e-3)
        # the line: trim_start 0.2 skips its silence, so the tone starts at time 0.5; mono at -3 dB per channel
        self.assertLess(amp_at(m[int(0.3 * SR):int(0.49 * SR)], 1000), 0.01)
        self.assertAlmostEqual(amp_at(m[int(0.55 * SR):int(1.05 * SR)], 1000), 0.5 * audiomix.MONO, delta=0.02)
        # duration 0.6: nothing of the line after 1.1 s although its file goes on
        self.assertLess(amp_at(m[int(1.15 * SR):int(1.45 * SR)], 1000), 0.01)

    def test_volume_and_fades(self):
        _, x = self.run_mix(self.body(volume='50%', audio_fade_in=0.2, audio_fade_out=0.2))
        m = x.mean(1)
        self.assertAlmostEqual(amp_at(m[int(0.75 * SR):int(0.85 * SR)], 1000), 0.25 * audiomix.MONO, delta=0.015)
        # linear ramps over the element's first / last 0.2 s: about half way in the middle of each
        self.assertAlmostEqual(amp_at(m[int(0.58 * SR):int(0.62 * SR)], 1000) / (0.25 * audiomix.MONO), 0.5, delta=0.12)
        self.assertAlmostEqual(amp_at(m[int(0.98 * SR):int(1.02 * SR)], 1000) / (0.25 * audiomix.MONO), 0.5, delta=0.12)

    def test_trim_duration_and_default_duration(self):
        # no duration: the element lasts its trim_duration; no body duration: the mix ends with the last element
        b = self.body(trim_duration=0.3)
        del b['elements'][1]['duration']
        del b['duration']
        b['elements'][0]['duration'] = 1.0
        r, x = self.run_mix(b)
        self.assertEqual(len(x), int(round(1.0 * SR)))                       # bed 0–1.0 s is the last end (line ends 0.8)
        m = x.mean(1)
        self.assertGreater(amp_at(m[int(0.55 * SR):int(0.75 * SR)], 1000), 0.3)
        self.assertLess(amp_at(m[int(0.82 * SR):int(0.98 * SR)], 1000), 0.01)

    def test_no_clipping(self):
        # two loud elements sum over full scale: the whole mix is scaled to PEAK, never clipped
        b = self.body(volume='400%')
        r, x = self.run_mix(b)
        self.assertLess(r['gainDb'], 0)
        self.assertAlmostEqual(float(np.abs(x).max()), audiomix.PEAK, delta=0.002)

    def test_refusals(self):
        bad = [
            ('unknown body key', lambda b: b.update({'snapshot_time': 1})),
            ('unknown element key', lambda b: b['elements'][1].update({'animations': []})),
            ('not audio', lambda b: b['elements'][1].update({'type': 'video'})),
            ('no time', lambda b: b['elements'][1].pop('time')),
            ('volume as a number', lambda b: b['elements'][1].update({'volume': 0.5})),
            ('volume too high', lambda b: b['elements'][1].update({'volume': '900%'})),
            ('negative time', lambda b: b['elements'][1].update({'time': -1})),
            ('not https', lambda b: b['elements'][1].update({'source': 'file:///etc/passwd'})),
            ('no elements', lambda b: b.update({'elements': []})),
        ]
        for what, f in bad:
            b = self.body()
            f(b)
            with self.subTest(what):
                with self.assertRaises(audiomix.Refused):
                    audiomix.plan(b)
        audiomix.plan(self.body())                                           # the good body passes

    def test_sniff(self):
        self.assertEqual(audiomix.sniff(self.line), 'mp3')
        self.assertEqual(audiomix.sniff(self.bed), 'wav')
        p = os.path.join(self.tmp, 'list.mp3')                               # a playlist named like audio is not opened
        with open(p, 'w') as f:
            f.write('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\nfile:///etc/passwd\n')
        self.assertIsNone(audiomix.sniff(p))
        b = self.body()
        with self.assertRaises(audiomix.Refused):
            audiomix.mix(b, {**self.files, 'https://v3b.fal.media/line.mp3': p}, os.path.join(self.tmp, 'x.wav'), self.tmp)

    def test_hosts(self):
        import media_job
        for h in ('v3b.fal.media', 'fal.media', 'v3.fal.media', 'f002.backblazeb2.com', 'd2h7xmz5gqybh9.cloudfront.net'):
            self.assertTrue(media_job.HOSTS.fullmatch(h), h)
        for h in ('evil.com', 'fal.media.evil.com', 'localhost', '169.254.169.254', 'backblazeb2.com.example', 'cloudfront.net',
                  'evil.cloudfront.net', 'd2h7xmz5gqybh9.cloudfront.net.evil.com', 'xd2h7xmz5gqybh9.cloudfront.net'):
            self.assertFalse(media_job.HOSTS.fullmatch(h), h)


if __name__ == '__main__':
    unittest.main()
